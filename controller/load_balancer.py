# SDN load balancer — intercepts new TCP connections, picks a backend,
# installs a forward+reverse flow pair. Set LB_ALGORITHM to switch algorithms.

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

# Ryu loads this as a script so `controller` isn't in sys.path by default.
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

try:
    from controller.traffic import classify_traffic, TrafficClass, DSCP
except ImportError:  # pragma: no cover
    from traffic import classify_traffic, TrafficClass, DSCP  # type: ignore


# --- service configuration -------------------------------------------------

VIP = "10.0.0.100"
VMAC = "00:00:00:00:01:00"

CLIENT_PORT = 1            # h1's switch port
CLIENT_IP = "10.0.0.1"
CLIENT_MAC = "00:00:00:00:00:01"

# Server list. Override count and weights via env vars if needed.
# e.g. LB_NUM_SERVERS=10 LB_WEIGHTS=16,16,...,4,4,...,1
_num_servers = int(os.environ.get("LB_NUM_SERVERS", "3"))
# servers start at the port after the last client
_num_clients = int(os.environ.get("LB_NUM_CLIENTS", "1"))
_server_port_offset = 1 + _num_clients   # switch port of the first server
_default_weights = [max(1.0, float(_num_servers) - i) for i in range(_num_servers)]
_env_weights = os.environ.get("LB_WEIGHTS", "")
if _env_weights:
    try:
        _parsed = [float(x) for x in _env_weights.split(",")]
        # Pad with 1.0 or truncate to match _num_servers
        if len(_parsed) >= _num_servers:
            _default_weights = _parsed[:_num_servers]
        else:
            _default_weights = _parsed + [1.0] * (_num_servers - len(_parsed))
    except ValueError:
        pass

BACKENDS = [
    (
        f"h{2 + i}",
        _server_port_offset + i,   # may be corrected at switch-connect time
        f"10.0.0.{2 + i}",
        f"00:00:00:00:00:{2 + i:02x}",
        _default_weights[i],
    )
    for i in range(_num_servers)
]

# run_net_auto.py writes this file after starting the topology
_PORTMAP_PATH = os.environ.get("LB_PORTMAP", "/tmp/lb_portmap.json")

# flow timeouts - 2s idle is enough since HTTP/1.0 closes after each response
FLOW_IDLE_TIMEOUT = 2
FLOW_HARD_TIMEOUT = 60

# all backends listen on 8000; the controller rewrites dst port for other VIP ports
HTTP_BACKEND_PORT = int(os.environ.get("LB_BACKEND_PORT", "8000"))

# Periodic stats poll interval
STATS_INTERVAL_S = 3.0
LOG_INTERVAL_S = 5.0

# where to write per-event metrics for the plotting scripts
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

        # cookie -> server so we know which server a FlowRemoved event belongs to
        self._cookie_to_server: dict[int, ServerMetrics] = {}
        self._next_cookie = 1
        # keep track of reverse flow cookies separately so we don't decrement active_conns twice
        self._reverse_cookies: set = set()

        # OVS takes ~1ms to install flows, so multiple PacketIn events can arrive
        # for the same connection before the rule is in. Cache the first pick.
        self._pending_flows: dict = {}

        # remember each client's MAC and switch port for fallback rewrites
        self._client_info: dict = {}

        # remember the original VIP port per connection so we can restore it
        # in response packets when the VIP port != HTTP_BACKEND_PORT
        self._srv_to_vip: dict = {}

        # background pollers
        self._stats_thread = hub.spawn(self._stats_loop)
        self._log_thread = hub.spawn(self._log_loop)

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
    def _deferred_portmap_apply(self):
        hub.sleep(2.0)
        self._apply_portmap()

    def _apply_portmap(self):
        """OVS doesn't assign ports in order; this corrects them from the portmap."""
        try:
            with open(_PORTMAP_PATH) as f:
                portmap = json.load(f)
        except FileNotFoundError:
            self.logger.info("no portmap at %s — using sequential offset=%d",
                             _PORTMAP_PATH, _server_port_offset)
            return
        except Exception as exc:
            self.logger.warning("portmap load error: %s", exc)
            return

        changed = 0
        for srv in self.servers:
            real_port = portmap.get(srv.name)
            if real_port is not None and int(real_port) != srv.port:
                self.logger.info("portmap: %s port %d → %d",
                                 srv.name, srv.port, int(real_port))
                self.servers_by_port.pop(srv.port, None)
                srv.port = int(real_port)
                changed += 1
        # Rebuild servers_by_port with corrected ports.
        if changed:
            self.servers_by_port = {s.port: s for s in self.servers}
            self.logger.info("portmap applied: %d server port(s) updated", changed)
        else:
            self.logger.info("portmap applied: all ports already correct")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        ofp, parser = dp.ofproto, dp.ofproto_parser

        hub.spawn(self._deferred_portmap_apply)

        # table-miss rule - send unknown packets to the controller
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
    # Backend selection
    # ------------------------------------------------------------------
    def _select_server(self, traffic_class: str = "http") -> ServerMetrics:
        return self._algorithm.select(self.servers, traffic_class)

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
                if srv is not None and ip4.dst in self._client_info:
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
        """VIP rewrite fallback for packets that arrive before the FlowMod commits."""
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        client_mac, client_port = self._client_info.get(
            ip4.dst, (CLIENT_MAC, CLIENT_PORT)
        )
        actions = [
            parser.OFPActionSetField(eth_src=VMAC),
            parser.OFPActionSetField(eth_dst=client_mac),
            parser.OFPActionSetField(ipv4_src=VIP),
        ]
        # restore the original VIP port in the response if needed
        if ip4.proto == 6:
            pkt_fb = packet.Packet(msg.data)
            tcp_fb = pkt_fb.get_protocol(tcp.tcp)
            if tcp_fb:
                vip_port = self._srv_to_vip.get((srv.ip, ip4.dst, tcp_fb.dst_port))
                if vip_port and vip_port != HTTP_BACKEND_PORT:
                    actions.append(parser.OFPActionSetField(tcp_src=vip_port))
        actions.append(parser.OFPActionOutput(client_port))
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

        tc = TrafficClass.HTTP
        if proto == 6:  # TCP
            t = pkt.get_protocol(tcp.tcp)
            if t is None:
                return
            client_src_port = t.src_port
            server_dst_port = t.dst_port
            tc = classify_traffic(server_dst_port, proto=6)
            l4_match = {"ip_proto": 6, "tcp_src": t.src_port, "tcp_dst": t.dst_port}
            l4_rev = {"ip_proto": 6, "tcp_src": HTTP_BACKEND_PORT, "tcp_dst": t.src_port}
        elif proto == 17:  # UDP
            u = pkt.get_protocol(udp.udp)
            if u is None:
                return
            client_src_port = u.src_port
            server_dst_port = u.dst_port
            tc = classify_traffic(server_dst_port, proto=17)
            l4_match = {"ip_proto": 17, "udp_src": u.src_port, "udp_dst": u.dst_port}
            l4_rev = {"ip_proto": 17, "udp_src": HTTP_BACKEND_PORT, "udp_dst": u.src_port}
        elif proto == 1:  # ICMP — no port concept, use broad match
            l4_match = {"ip_proto": 1}
            l4_rev = {"ip_proto": 1}
        else:
            return  # don't load-balance unknown L4

        # check if we already picked a server for this flow
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

        # learn this client's MAC and port
        if ip4.src not in self._client_info:
            self._client_info[ip4.src] = (eth.src, in_port)
            self.logger.info("new client: %s  mac=%s  port=%d", ip4.src, eth.src, in_port)

        srv = self._select_server(tc)
        srv.on_selected()
        self._algorithm.on_selected(srv)

        fwd_cookie = self._alloc_cookie(srv)
        rev_cookie = self._alloc_cookie(srv)
        self._reverse_cookies.add(rev_cookie)

        srv.enqueue_request(RequestEntry(
            cookie=fwd_cookie,
            ts=time.monotonic(),
            proto=proto,
            client_src_port=client_src_port,
            server_dst_port=server_dst_port,
            dispatched=True,
            client_ip=ip4.src,
            traffic_class=tc,
        ))

        self.logger.info(
            "LB [%s]: %s:%s -> %s  picked %s (score=%.3f, active=%d, queue=%d, w=%.1f)",
            tc, ip4.src, client_src_port or "*", VIP,
            srv.name, srv.score(), srv.active_conns, srv.queue_depth, srv.weight,
        )
        if self._metrics_fh is not None:
            try:
                rec = json.dumps({
                    "event": "select",
                    "t": round(time.monotonic(), 3),
                    "traffic_class": tc,
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

        # forward rule: rewrite VIP -> real server address
        fwd_match = parser.OFPMatch(
            in_port=in_port,
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=ip4.src,
            ipv4_dst=VIP,
            **l4_match,
        )
        fwd_actions = [
            parser.OFPActionSetField(ip_dscp=DSCP.get(tc, 0)),
            parser.OFPActionSetField(eth_src=VMAC),
            parser.OFPActionSetField(eth_dst=srv.mac),
            parser.OFPActionSetField(ipv4_dst=srv.ip),
        ]
        if proto == 6 and server_dst_port != HTTP_BACKEND_PORT:
            fwd_actions.append(parser.OFPActionSetField(tcp_dst=HTTP_BACKEND_PORT))
        elif proto == 17 and server_dst_port != HTTP_BACKEND_PORT:
            fwd_actions.append(parser.OFPActionSetField(udp_dst=HTTP_BACKEND_PORT))
        fwd_actions.append(parser.OFPActionOutput(srv.port))
        self._add_flow(dp, PRIO_LB, fwd_match, fwd_actions,
                       idle_timeout=FLOW_IDLE_TIMEOUT,
                       hard_timeout=FLOW_HARD_TIMEOUT,
                       cookie=fwd_cookie,
                       flags=ofp.OFPFF_SEND_FLOW_REM)

        # reverse rule: rewrite server address back to VIP
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
        ]
        if proto == 6 and server_dst_port != HTTP_BACKEND_PORT:
            rev_actions.append(parser.OFPActionSetField(tcp_src=server_dst_port))
        elif proto == 17 and server_dst_port != HTTP_BACKEND_PORT:
            rev_actions.append(parser.OFPActionSetField(udp_src=server_dst_port))
        rev_actions.append(parser.OFPActionOutput(in_port))

        # remember which VIP port was used so the fallback can restore it
        if proto in (6, 17) and server_dst_port != HTTP_BACKEND_PORT:
            self._srv_to_vip[(srv.ip, ip4.src, client_src_port)] = server_dst_port
        self._add_flow(dp, PRIO_LB, rev_match, rev_actions,
                       idle_timeout=FLOW_IDLE_TIMEOUT,
                       hard_timeout=FLOW_HARD_TIMEOUT,
                       cookie=rev_cookie,
                       flags=ofp.OFPFF_SEND_FLOW_REM)

        self._pending_flows[flow_key] = (srv, fwd_actions)

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
        live_bytes = {s.ip: 0 for s in self.servers}
        live_packets = {s.ip: 0 for s in self.servers}
        for st in ev.msg.body:
            srv = self._cookie_to_server.get(st.cookie)
            if srv is None:
                continue
            live_bytes[srv.ip] += st.byte_count
            live_packets[srv.ip] += st.packet_count
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
            self.logger.debug(
                "rev-flow removed: %s  bytes=%d pkts=%d dur=%.2fs",
                srv.name, msg.byte_count, msg.packet_count, duration,
            )
            return

        srv.on_flow_removed(byte_count=msg.byte_count,
                            packet_count=msg.packet_count,
                            duration_s=duration)
        entry = srv.dequeue_request(msg.cookie)
        if entry is not None:
            flow_key = (entry.client_ip or CLIENT_IP, entry.proto, entry.client_src_port, entry.server_dst_port)
            self._pending_flows.pop(flow_key, None)
            if entry.proto in (6, 17) and entry.server_dst_port != HTTP_BACKEND_PORT:
                self._srv_to_vip.pop((srv.ip, entry.client_ip, entry.client_src_port), None)
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
        hub.sleep(LOG_INTERVAL_S)  # wait for the switch to connect first
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
