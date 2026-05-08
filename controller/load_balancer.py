"""
Adaptive load-balancing controller (Phase 2).

Topology assumptions (see topo/simple_topo.py):

    h1 (10.0.0.1)   client            -> port 1 on s1
    h2 (10.0.0.2)   server #1         -> port 2 on s1
    h3 (10.0.0.3)   server #2         -> port 3 on s1
    h4 (10.0.0.4)   server #3         -> port 4 on s1

The controller exposes a Virtual IP (VIP) that the client uses to reach
"the service". On every new client->VIP flow, the controller picks the
backend with the lowest adaptive score and installs:

    forward flow : (client_ip, VIP, l4_proto, sport, dport) -> rewrite to server
    reverse flow : (server_ip, client_ip, l4_proto, dport, sport) -> rewrite back

Both flows are installed with idle/hard timeouts and the SEND_FLOW_REM flag,
so we get OFPFlowRemoved events from which we update per-server byte/packet
counters and decrement the active-connection count.

Periodically we also pull OFPFlowStats and OFPPortStats to refresh
throughput/error metrics, and we log a metrics snapshot every few seconds.

Validate from the mininet CLI with the helpers in scripts/test_lb.sh.
"""

from __future__ import annotations

import json
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.lib.packet import arp, ethernet, ether_types, icmp, ipv4, packet, tcp, udp
from ryu.ofproto import ofproto_v1_3

# Ryu loads this file by path, so `controller` isn't a package at runtime.
# Make `metrics.py` importable whether we're loaded as a script (ryu-manager)
# or as part of the `controller` package (pytest).
import os
import sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from controller.metrics import ServerMetrics  # package-style (tests)
    from controller.metrics import RequestEntry
except ImportError:  # pragma: no cover - exercised by ryu-manager
    from metrics import ServerMetrics  # script-style (ryu-manager)  # type: ignore
    from metrics import RequestEntry  # type: ignore

try:
    from controller.algorithms import get_algorithm, available_algorithms
except ImportError:  # pragma: no cover - exercised by ryu-manager
    from algorithms import get_algorithm, available_algorithms


# --- service configuration -------------------------------------------------

VIP = "10.0.0.100"
VMAC = "00:00:00:00:01:00"

CLIENT_PORT = 1            # h1's switch port
CLIENT_IP = "10.0.0.1"
CLIENT_MAC = "00:00:00:00:00:01"

# (port, ip, mac, weight)
BACKENDS = [
    ("h2", 2, "10.0.0.2", "00:00:00:00:00:02", 1.0),
    ("h3", 3, "10.0.0.3", "00:00:00:00:00:03", 1.0),
    ("h4", 4, "10.0.0.4", "00:00:00:00:00:04", 1.0),
]

# Flow lifetimes
# HTTP/1.0 connections close immediately after each response, so the flow
# goes idle right away.  A 10-s idle timeout lets active_conns drain quickly
# (important for least-connections accuracy) while still covering any
# keep-alive or slow-response scenario.
FLOW_IDLE_TIMEOUT = 2   # seconds — HTTP/1.0 closes after each response so the
                        # flow goes idle immediately; 2 s gives OVS time to see
                        # the FIN exchange before evicting the entry.
FLOW_HARD_TIMEOUT = 60

# Periodic stats poll interval
STATS_INTERVAL_S = 3.0
LOG_INTERVAL_S = 5.0

# Where to write machine-readable metrics snapshots for offline analysis/plotting.
# Each line is a JSON object: {"t": <monotonic_s>, "servers": [<snapshot>, ...]}.
# Set to empty string to disable.
METRICS_JSONL = os.environ.get("LB_METRICS_FILE", "/tmp/lb_metrics.jsonl")

# Priorities
PRIO_DEFAULT = 0
PRIO_L2 = 1
PRIO_LB = 10

# Load-balancing algorithm: override at runtime with the LB_ALGORITHM env var.
DEFAULT_ALGORITHM = "round_robin"


class AdaptiveLoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}            # dpid -> Datapath
        self.mac_to_port = {}          # dpid -> {mac: port}

        self.servers: list[ServerMetrics] = [
            ServerMetrics(name=n, port=p, ip=ip, mac=mac, weight=w)
            for (n, p, ip, mac, w) in BACKENDS
        ]
        # ip -> ServerMetrics, port -> ServerMetrics for fast lookup
        self.servers_by_ip = {s.ip: s for s in self.servers}
        self.servers_by_port = {s.port: s for s in self.servers}

        # Pluggable load-balancing algorithm (strategy pattern). The active
        # algorithm is chosen via the LB_ALGORITHM env var, defaulting to
        # round robin. New algorithms register themselves in
        # controller/algorithms/registry.py.
        algo_name = os.environ.get("LB_ALGORITHM", DEFAULT_ALGORITHM)
        try:
            self._algorithm = get_algorithm(algo_name)
        except ValueError:
            self.logger.error(
                "unknown LB_ALGORITHM=%r; available=%s; falling back to %s",
                algo_name, available_algorithms(), DEFAULT_ALGORITHM,
            )
            self._algorithm = get_algorithm(DEFAULT_ALGORITHM)
        self.logger.info("load-balancing algorithm: %s", self._algorithm.name)

        # cookie -> ServerMetrics, used to credit FlowRemoved to the right server
        # Forward and reverse flows each get their own cookie so FlowRemoved
        # events are attributed correctly.
        self._cookie_to_server: dict[int, ServerMetrics] = {}
        self._next_cookie = 1
        # Set of cookies belonging to REVERSE flows — used to avoid double-
        # decrementing active_conns (we only credit the forward cookie).
        self._reverse_cookies: set = set()

        # Pending-flow cache: maps the 5-tuple that uniquely identifies a
        # client connection to the (server, fwd_actions) already chosen for it.
        # OVS takes a short but non-zero time to commit a FlowMod.  In that
        # window the SYN, ACK, and first GET can all arrive as PacketIn events.
        # Without this cache each would trigger a new server selection,
        # sending successive packets of the SAME TCP connection to DIFFERENT
        # backends — which breaks the TCP 3-way handshake.
        # Entry format: (src_ip, proto, src_port, dst_port) -> (server, [actions])
        self._pending_flows: dict = {}

        # background pollers
        self._stats_thread = hub.spawn(self._stats_loop)
        self._log_thread = hub.spawn(self._log_loop)

        # Open the JSONL metrics file for writing (truncate on start).
        self._metrics_fh = None
        if METRICS_JSONL:
            try:
                self._metrics_fh = open(METRICS_JSONL, "w")
                self.logger.info("metrics JSONL -> %s", METRICS_JSONL)
            except OSError as exc:
                self.logger.warning("cannot open metrics file %s: %s", METRICS_JSONL, exc)

    # ------------------------------------------------------------------
    # Switch lifecycle
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        ofp, parser = dp.ofproto, dp.ofproto_parser

        # table-miss -> controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                          ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, PRIO_DEFAULT, match, actions)
        self.logger.info("switch %016x connected, table-miss installed", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _add_flow(self, dp, priority, match, actions, *,
                  idle_timeout=0, hard_timeout=0, cookie=0, flags=0,
                  buffer_id=None):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        kwargs = dict(datapath=dp, priority=priority, match=match,
                      instructions=inst, idle_timeout=idle_timeout,
                      hard_timeout=hard_timeout, cookie=cookie, flags=flags)
        if buffer_id is not None:
            kwargs["buffer_id"] = buffer_id
        dp.send_msg(parser.OFPFlowMod(**kwargs))

    def _send_packet(self, dp, in_port, data):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        actions = [parser.OFPActionOutput(ofp.OFPP_TABLE)]
        out = parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                  in_port=in_port, actions=actions, data=data)
        dp.send_msg(out)

    def _alloc_cookie(self, server: ServerMetrics) -> int:
        c = self._next_cookie
        self._next_cookie += 1
        self._cookie_to_server[c] = server
        return c

    # ------------------------------------------------------------------
    # Backend selection (delegates to the configured algorithm)
    # ------------------------------------------------------------------
    def _select_server(self) -> ServerMetrics:
        return self._algorithm.select(self.servers)

    # ------------------------------------------------------------------
    # PacketIn
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # ARP for the VIP -> reply ourselves; other ARP -> L2 learn/flood
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            arp_pkt = pkt.get_protocol(arp.arp)
            if (arp_pkt is not None
                    and arp_pkt.opcode == arp.ARP_REQUEST
                    and arp_pkt.dst_ip == VIP):
                self._reply_vip_arp(dp, in_port, eth, arp_pkt)
                return
            self._l2_learn_and_forward(dp, in_port, msg, eth)
            return

        if eth.ethertype == ether_types.ETH_TYPE_IP:
            ip4 = pkt.get_protocol(ipv4.ipv4)
            if ip4 is not None:
                # client -> VIP : install LB flow pair
                if ip4.dst == VIP:
                    self._handle_client_to_vip(dp, in_port, msg, eth, ip4, pkt)
                    return
                # server -> client : the reverse flow handles this in the
                # data plane, but the very first packet (SYN-ACK) may arrive
                # at the controller before OVS has committed the FlowMod.
                # Detect it by checking whether the source is a known backend
                # and forward it with the VIP rewrite applied directly.
                srv = self.servers_by_ip.get(ip4.src)
                if srv is not None and ip4.dst == CLIENT_IP:
                    self._handle_server_to_client(dp, in_port, msg, eth, ip4, srv)
                    return

        # generic L2 learning for everything else
        self._l2_learn_and_forward(dp, in_port, msg, eth)

    # ------------------------------------------------------------------
    def _reply_vip_arp(self, dp, in_port, eth, arp_pkt):
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_ARP,
            src=VMAC, dst=eth.src))
        reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=VMAC, src_ip=VIP,
            dst_mac=arp_pkt.src_mac, dst_ip=arp_pkt.src_ip))
        reply.serialize()
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        actions = [parser.OFPActionOutput(in_port)]
        out = parser.OFPPacketOut(datapath=dp,
                                  buffer_id=ofp.OFP_NO_BUFFER,
                                  in_port=ofp.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=reply.data)
        dp.send_msg(out)
        self.logger.debug("ARP %s -> VIP, replied with %s",
                          arp_pkt.src_ip, VMAC)

    # ------------------------------------------------------------------
    def _handle_server_to_client(self, dp, in_port, msg, eth, ip4, srv):
        """Forward a server->client packet that missed the reverse flow rule.

        This covers the race where the SYN-ACK (or any early response packet)
        reaches the controller before OVS has committed the reverse FlowMod.
        We apply the VIP rewrite inline and forward the packet directly.
        """
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        actions = [
            parser.OFPActionSetField(eth_src=VMAC),
            parser.OFPActionSetField(eth_dst=CLIENT_MAC),
            parser.OFPActionSetField(ipv4_src=VIP),
            parser.OFPActionOutput(CLIENT_PORT),
        ]
        # Always use inline data — avoids OVS buffer expiry race.
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data,
        )
        dp.send_msg(out)
        self.logger.debug("srv->client fallback: %s -> %s via VIP", srv.name, CLIENT_IP)

    # ------------------------------------------------------------------
    def _handle_client_to_vip(self, dp, in_port, msg, eth, ip4, pkt):
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        proto = ip4.proto
        l4_match: dict = {}
        l4_rev: dict = {}
        client_src_port = 0
        server_dst_port = 0

        if proto == 6:  # TCP
            t = pkt.get_protocol(tcp.tcp)
            if t is None:
                return
            client_src_port = t.src_port
            server_dst_port = t.dst_port
            l4_match = {"ip_proto": 6, "tcp_src": t.src_port, "tcp_dst": t.dst_port}
            l4_rev = {"ip_proto": 6, "tcp_src": t.dst_port, "tcp_dst": t.src_port}
        elif proto == 17:  # UDP
            u = pkt.get_protocol(udp.udp)
            if u is None:
                return
            client_src_port = u.src_port
            server_dst_port = u.dst_port
            l4_match = {"ip_proto": 17, "udp_src": u.src_port, "udp_dst": u.dst_port}
            l4_rev = {"ip_proto": 17, "udp_src": u.dst_port, "udp_dst": u.src_port}
        elif proto == 1:  # ICMP — no port concept, use broad match
            l4_match = {"ip_proto": 1}
            l4_rev = {"ip_proto": 1}
        else:
            return  # don't load-balance unknown L4

        # ── Deduplication: OVS takes ~1 ms to commit a FlowMod.  In that
        # window the SYN, ACK, and first data segment can all arrive as
        # PacketIn.  Re-use the server we already picked for this 5-tuple
        # instead of selecting a new one (which would route successive TCP
        # segments to DIFFERENT backends and break the handshake).
        flow_key = (ip4.src, proto, client_src_port, server_dst_port)
        if flow_key in self._pending_flows:
            srv, fwd_actions = self._pending_flows[flow_key]
            self.logger.debug(
                "dup PacketIn for %s:%s -> VIP  reusing %s",
                ip4.src, client_src_port, srv.name,
            )
            out = parser.OFPPacketOut(
                datapath=dp,
                buffer_id=ofp.OFP_NO_BUFFER,
                in_port=in_port,
                actions=fwd_actions,
                data=msg.data,
            )
            dp.send_msg(out)
            return

        srv = self._select_server()
        srv.on_selected()
        self._algorithm.on_selected(srv)

        # Allocate separate cookies for forward and reverse flows so
        # FlowRemoved events from either direction can be attributed.
        fwd_cookie = self._alloc_cookie(srv)
        rev_cookie = self._alloc_cookie(srv)
        self._reverse_cookies.add(rev_cookie)

        # Enqueue this request in the server's per-server queue so that
        # metrics and future algorithms can inspect / manage it.
        srv.enqueue_request(RequestEntry(
            cookie=fwd_cookie,
            ts=time.monotonic(),
            proto=proto,
            client_src_port=client_src_port,
            server_dst_port=server_dst_port,
            dispatched=True,
        ))

        self.logger.info(
            "LB: %s:%s -> %s  picked %s (score=%.3f, active=%d, queue=%d, w=%.1f)",
            ip4.src, client_src_port or "*", VIP,
            srv.name, srv.score(), srv.active_conns, srv.queue_depth, srv.weight,
        )
        if self._metrics_fh is not None:
            try:
                rec = json.dumps({
                    "event": "select",
                    "t": round(time.monotonic(), 3),
                    "server": srv.name,
                    "client_port": client_src_port,
                    "proto": proto,
                    "score": round(srv.score(), 4),
                    "active_conns": srv.active_conns,
                    "queue_depth": srv.queue_depth,
                })
                self._metrics_fh.write(rec + "\n")
                self._metrics_fh.flush()
            except OSError:
                pass

        # ----- forward: client -> VIP becomes client -> server ----------
        fwd_match = parser.OFPMatch(
            in_port=in_port,
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=ip4.src,
            ipv4_dst=VIP,
            **l4_match,
        )
        fwd_actions = [
            parser.OFPActionSetField(eth_src=VMAC),
            parser.OFPActionSetField(eth_dst=srv.mac),
            parser.OFPActionSetField(ipv4_dst=srv.ip),
            parser.OFPActionOutput(srv.port),
        ]
        self._add_flow(dp, PRIO_LB, fwd_match, fwd_actions,
                       idle_timeout=FLOW_IDLE_TIMEOUT,
                       hard_timeout=FLOW_HARD_TIMEOUT,
                       cookie=fwd_cookie,
                       flags=ofp.OFPFF_SEND_FLOW_REM)

        # ----- reverse: server -> client becomes VIP -> client ----------
        rev_match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=srv.ip,
            ipv4_dst=ip4.src,
            **l4_rev,
        )
        rev_actions = [
            parser.OFPActionSetField(eth_src=VMAC),
            parser.OFPActionSetField(eth_dst=eth.src),
            parser.OFPActionSetField(ipv4_src=VIP),
            parser.OFPActionOutput(in_port),
        ]
        self._add_flow(dp, PRIO_LB, rev_match, rev_actions,
                       idle_timeout=FLOW_IDLE_TIMEOUT,
                       hard_timeout=FLOW_HARD_TIMEOUT,
                       cookie=rev_cookie,
                       flags=ofp.OFPFF_SEND_FLOW_REM)

        # Cache this 5-tuple so duplicate PacketIn events (SYN retransmits,
        # ACK, first GET arriving before FlowMod is committed) are handled
        # correctly without re-selecting a server.
        self._pending_flows[flow_key] = (srv, fwd_actions)

        # ---- send the trigger packet through the freshly installed flow.
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=fwd_actions,
            data=msg.data,
        )
        dp.send_msg(out)

    # ------------------------------------------------------------------
    def _l2_learn_and_forward(self, dp, in_port, msg, eth):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        dpid = dp.id

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        out_port = self.mac_to_port[dpid].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port,
                                    eth_src=eth.src, eth_dst=eth.dst)
            self._add_flow(dp, PRIO_L2, match, actions,
                           idle_timeout=FLOW_IDLE_TIMEOUT)

        # Always use inline data — avoids OVS buffer expiry race.
        out = parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                  in_port=in_port, actions=actions, data=msg.data)
        dp.send_msg(out)

    # ------------------------------------------------------------------
    # Stats handling
    # ------------------------------------------------------------------
    def _stats_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                self._request_stats(dp)
            hub.sleep(STATS_INTERVAL_S)

    def _request_stats(self, dp):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        dp.send_msg(parser.OFPFlowStatsRequest(dp))
        dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply(self, ev):
        # Sum live byte/packet counts of currently-installed forward flows
        # per server. We only refresh the per-server "live throughput" view;
        # cumulative totals are advanced on FlowRemoved.
        live_bytes = {s.ip: 0 for s in self.servers}
        live_packets = {s.ip: 0 for s in self.servers}
        for st in ev.msg.body:
            srv = self._cookie_to_server.get(st.cookie)
            if srv is None:
                continue
            live_bytes[srv.ip] += st.byte_count
            live_packets[srv.ip] += st.packet_count
        # surface as transient counters (not added to total_*; those are
        # incremented in on_flow_removed to avoid double-counting)
        for s in self.servers:
            s._live_bytes = live_bytes[s.ip]      # type: ignore[attr-defined]
            s._live_packets = live_packets[s.ip]  # type: ignore[attr-defined]

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply(self, ev):
        for ps in ev.msg.body:
            srv = self.servers_by_port.get(ps.port_no)
            if srv is None:
                continue
            srv.on_port_stats(
                tx_bytes=ps.tx_bytes, rx_bytes=ps.rx_bytes,
                tx_errors=ps.tx_errors, rx_errors=ps.rx_errors,
                tx_dropped=ps.tx_dropped, rx_dropped=ps.rx_dropped,
            )

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def _flow_removed(self, ev):
        msg = ev.msg
        is_reverse = msg.cookie in self._reverse_cookies
        if is_reverse:
            self._reverse_cookies.discard(msg.cookie)

        srv = self._cookie_to_server.pop(msg.cookie, None)
        if srv is None:
            return

        duration = msg.duration_sec + msg.duration_nsec / 1e9

        if is_reverse:
            # Reverse flow expired — just remove from cookie map; the forward
            # flow's removal is the authoritative event for metrics and queue.
            self.logger.debug(
                "rev-flow removed: %s  bytes=%d pkts=%d dur=%.2fs",
                srv.name, msg.byte_count, msg.packet_count, duration,
            )
            return

        # Forward flow removed — credit the server and update the queue.
        srv.on_flow_removed(byte_count=msg.byte_count,
                            packet_count=msg.packet_count,
                            duration_s=duration)
        entry = srv.dequeue_request(msg.cookie)
        # Remove from pending-flow cache so a future connection reusing the
        # same ephemeral port gets a fresh server selection.
        if entry is not None:
            flow_key = (CLIENT_IP, entry.proto, entry.client_src_port, entry.server_dst_port)
            self._pending_flows.pop(flow_key, None)
        self._algorithm.on_flow_removed(srv)
        self.logger.info(
            "flow removed: %s  bytes=%d pkts=%d dur=%.2fs  active->%d queue->%d",
            srv.name, msg.byte_count, msg.packet_count, duration,
            srv.active_conns, srv.queue_depth,
        )
        if self._metrics_fh is not None:
            try:
                rec = json.dumps({
                    "event": "flow_removed",
                    "t": round(time.monotonic(), 3),
                    "server": srv.name,
                    "active_conns": srv.active_conns,
                    "queue_depth": srv.queue_depth,
                    "bytes": msg.byte_count,
                    "duration_s": round(duration, 2),
                })
                self._metrics_fh.write(rec + "\n")
                self._metrics_fh.flush()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Periodic logging
    # ------------------------------------------------------------------
    def _log_loop(self):
        # let the switch connect first
        hub.sleep(LOG_INTERVAL_S)
        t0 = time.monotonic()
        while True:
            snaps = [s.snapshot() for s in self.servers]
            t = time.monotonic()
            self.logger.info("=== metrics @ t=%.1fs ===", t)
            for s in snaps:
                self.logger.info(
                    " %s ip=%-9s w=%.1f active=%d queue=%d sel=%d "
                    "bytes=%d pkts=%d thr=%.0f bps "
                    "rate=%.2f/s err=%d avgdur=%.2fs score=%.3f",
                    s["name"], s["ip"], s["weight"],
                    s["active_conns"], s["queue_depth"], s["selections_total"],
                    s["total_bytes"], s["total_packets"],
                    s["throughput_bps"], s["conn_rate_eps"],
                    s["port_errors"], s["avg_flow_dur_s"], s["score"],
                )
            if self._metrics_fh is not None:
                try:
                    line = json.dumps({"t": round(t - t0, 3), "servers": snaps})
                    self._metrics_fh.write(line + "\n")
                    self._metrics_fh.flush()
                except OSError:
                    pass
            hub.sleep(LOG_INTERVAL_S)
