from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ipv4
from ryu.lib.packet import tcp
from ryu.lib.packet import arp
from ryu.lib.packet import ether_types
import urllib.parse

from controller.online_scheduler import OnlineScheduler


class RoundRobinLB(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(RoundRobinLB, self).__init__(*args, **kwargs)
        self.datapath = None
        self.vip_ip = "10.0.0.100"
        self.vip_mac = "00:00:00:00:00:fe"
        self.scheduler = OnlineScheduler()
        self.backends = []
        for s in self.scheduler.servers:
            self.backends.append(
                {"ip": s.ip, "mac": s.mac, "port": s.port, "name": s.name}
            )
        self.backend_by_name = {b["name"]: b for b in self.backends}
        self.known_sessions = {}
        self.rejected_sessions = set()
        self.logger.info("--- Load Balancer Starting ---")

    def add_flow(self, dp, priority, match, actions, idle=0, hard=0):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle,
            hard_timeout=hard,
        )
        dp.send_msg(mod)

    def get_backend_for_session(self, src_ip, src_port, req_info=None):
        key = (src_ip, src_port)
        if key in self.known_sessions:
            return self.known_sessions[key]
        if key in self.rejected_sessions:
            return None
        req, server_model = self.scheduler.assign_for_new_flow(req_info=req_info)
        if server_model is None or req is None or req.status == "too_consuming":
            self.logger.info(
                "Request from {}:{} marked too_consuming or no server available".format(
                    src_ip, src_port
                )
            )
            self.rejected_sessions.add(key)
            return None
        backend = self.backend_by_name.get(server_model.name)
        if backend is None:
            self.rejected_sessions.add(key)
            return None
        self.logger.info(
            "LB DECISION: {} mapped to {} (req {} start {} finish {})".format(
                src_ip,
                backend["ip"],
                req.name,
                req.start_ms,
                req.finish_ms,
            )
        )
        self.known_sessions[key] = backend
        return backend

    def extract_request_metadata(self, pkt):
        try:
            raw_payload = None
            if getattr(pkt, "protocols", None):
                last = pkt.protocols[-1]
                if isinstance(last, (bytes, bytearray)):
                    raw_payload = last
            if not raw_payload:
                return None
            text = raw_payload.decode("ascii", errors="ignore")
            first_line = text.split("\r\n", 1)[0]
            parts = first_line.split()
            if len(parts) < 2:
                return None
            path = parts[1]
            parsed = urllib.parse.urlparse(path)
            qs = urllib.parse.parse_qs(parsed.query)
            name = qs.get("name", [None])[0]
            mem = qs.get("mem", [None])[0]
            units = qs.get("units", [None])[0]
            arrival = qs.get("arrival", [None])[0]
            if name is None or mem is None or units is None:
                return None
            info = {
                "name": name,
                "memory_mb": float(mem),
                "units": float(units),
            }
            if arrival is not None:
                info["arrival_ms"] = int(arrival)
            return info
        except Exception as e:
            self.logger.debug("Failed to parse request metadata: %s", e)
            return None

    def send_arp_reply(self, dp, pkt_arp, in_port):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        eth = ethernet.ethernet(
            dst=pkt_arp.src_mac,
            src=self.vip_mac,
            ethertype=ether_types.ETH_TYPE_ARP,
        )
        arp_reply = arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=self.vip_mac,
            src_ip=self.vip_ip,
            dst_mac=pkt_arp.src_mac,
            dst_ip=pkt_arp.src_ip,
        )
        p = packet.Packet()
        p.add_protocol(eth)
        p.add_protocol(arp_reply)
        p.serialize()
        actions = [parser.OFPActionOutput(in_port)]
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=ofp.OFPP_CONTROLLER,
            actions=actions,
            data=p.data,
        )
        dp.send_msg(out)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        self.datapath = dp
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)
        ]
        self.add_flow(dp, 0, match, actions)
        self.logger.info("logs: switch connected")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            p_arp = pkt.get_protocol(arp.arp)
            if p_arp and p_arp.opcode == arp.ARP_REQUEST and p_arp.dst_ip == self.vip_ip:
                self.send_arp_reply(dp, p_arp, in_port)
                return
            else:
                actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
                out = parser.OFPPacketOut(
                    datapath=dp,
                    buffer_id=ofp.OFP_NO_BUFFER,
                    in_port=in_port,
                    actions=actions,
                    data=msg.data,
                )
                dp.send_msg(out)
                return
        if eth.ethertype == ether_types.ETH_TYPE_IP:
            ip = pkt.get_protocol(ipv4.ipv4)
            if ip and ip.dst == self.vip_ip:
                t = pkt.get_protocol(tcp.tcp)
                if t and t.dst_port == 80:
                    req_info = self.extract_request_metadata(pkt)
                    backend = self.get_backend_for_session(ip.src, t.src_port, req_info=req_info)
                    if backend is None:
                        return
                    match_fwd = parser.OFPMatch(
                        eth_type=0x0800,
                        ipv4_src=ip.src,
                        ipv4_dst=self.vip_ip,
                        ip_proto=6,
                        tcp_src=t.src_port,
                        tcp_dst=80,
                    )
                    actions_fwd = [
                        parser.OFPActionSetField(ipv4_dst=backend["ip"]),
                        parser.OFPActionSetField(eth_dst=backend["mac"]),
                        parser.OFPActionOutput(backend["port"]),
                    ]
                    match_rev = parser.OFPMatch(
                        eth_type=0x0800,
                        ipv4_src=backend["ip"],
                        ipv4_dst=ip.src,
                        ip_proto=6,
                        tcp_src=80,
                        tcp_dst=t.src_port,
                    )
                    actions_rev = [
                        parser.OFPActionSetField(ipv4_src=self.vip_ip),
                        parser.OFPActionSetField(eth_src=self.vip_mac),
                        parser.OFPActionOutput(in_port),
                    ]
                    self.add_flow(dp, 100, match_fwd, actions_fwd, idle=30)
                    self.add_flow(dp, 100, match_rev, actions_rev, idle=30)
                    out = parser.OFPPacketOut(
                        datapath=dp,
                        buffer_id=ofp.OFP_NO_BUFFER,
                        in_port=in_port,
                        actions=actions_fwd,
                        data=msg.data,
                    )
                    dp.send_msg(out)
                    return
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=actions,
            data=msg.data,
        )
        dp.send_msg(out)
