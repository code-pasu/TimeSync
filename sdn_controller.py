import json
import time
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ipv4, udp, ether_types
from os_ken.lib import hub

SYNC_UDP_PORT = 5005
IPERF_UDP_PORT = 5201
HIGH_PRIO_QUEUE = 1
LOW_PRIO_QUEUE = 0
TELEMETRY_TCP_PORT = 9090
SLA_ERROR_THRESHOLD_MS = 2.0  # Trigger dynamic SDN mitigation if sync error > 2.0 ms

class DynamicTimeSyncSDN(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(DynamicTimeSyncSDN, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        # Modes: "dynamic" (starts OFF, auto-enables on SLA breach), "baseline" (always OFF), "proactive" (always ON)
        self.policy_mode = "dynamic"
        self.qos_active = False
        self.port_tx_bytes = {}

        # Spawn background green threads for OpenFlow Port Stats polling & TCP Telemetry
        self.monitor_thread = hub.spawn(self._port_stats_poller)
        self.telemetry_thread = hub.spawn(self._tcp_telemetry_server)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Table-miss rule (Priority 0 -> send to controller)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info(f"[SDN] Switch s{datapath.id} connected. Policy Mode: {self.policy_mode.upper()}")

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority, idle_timeout=idle_timeout,
            match=match, instructions=inst
        )
        datapath.send_msg(mod)

    def enforce_dynamic_qos_on_all_switches(self, reason=""):
        """Dynamically pushes Priority 100 FlowMods to s1 to move UDP 5005 into VIP Queue 1."""
        if self.policy_mode == "baseline" or self.qos_active:
            return
        self.qos_active = True
        self.logger.warning(f"⚡ [DYNAMIC SDN POLICY TRIGGERED] {reason} -> Elevating UDP {SYNC_UDP_PORT} to Queue {HIGH_PRIO_QUEUE}!")

        for dpid, datapath in self.datapaths.items():
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser

            # Install proactive bidirectional high-priority rules for UDP 5005 using NORMAL/learned ports
            for mac, out_port in self.mac_to_port.get(dpid, {}).items():
                for match_kwargs in [
                    {"eth_type": ether_types.ETH_TYPE_IP, "ip_proto": 17, "udp_dst": SYNC_UDP_PORT, "eth_dst": mac},
                    {"eth_type": ether_types.ETH_TYPE_IP, "ip_proto": 17, "udp_src": SYNC_UDP_PORT, "eth_dst": mac},
                ]:
                    match = parser.OFPMatch(**match_kwargs)
                    actions = [parser.OFPActionSetQueue(HIGH_PRIO_QUEUE), parser.OFPActionOutput(out_port)]
                    self.add_flow(datapath, 100, match, actions)

    def reset_policy_mode(self, mode):
        """Resets switch flow tables when switching between Baseline, Dynamic, and Proactive modes."""
        self.policy_mode = mode
        self.qos_active = (mode == "proactive")
        self.logger.info(f"🔄 [SDN MODE SWITCH] Mode set to '{mode.upper()}', qos_active={self.qos_active}")

        for dpid, datapath in self.datapaths.items():
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            # Delete all non-table-miss flows (priority > 0) by resetting table and restoring table-miss
            del_mod = parser.OFPFlowMod(
                datapath=datapath, command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
                match=parser.OFPMatch()
            )
            datapath.send_msg(del_mod)
            # Re-install table-miss
            actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
            self.add_flow(datapath, 0, parser.OFPMatch(), actions)

    def _tcp_telemetry_server(self):
        """TCP Server on 0.0.0.0:9090 listening for client telemetry & test-runner mode commands."""
        server_sock = hub.socket.socket(hub.socket.AF_INET, hub.socket.SOCK_STREAM)
        server_sock.setsockopt(hub.socket.SOL_SOCKET, hub.socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", TELEMETRY_TCP_PORT))
        server_sock.listen(10)
        self.logger.info(f"[TCP Monitor] Listening for Telemetry on TCP port {TELEMETRY_TCP_PORT}...")

        while True:
            conn, _ = server_sock.accept()
            try:
                data = conn.recv(1024).decode("utf-8").strip()
                if data:
                    msg = json.loads(data.splitlines()[0])
                    if "command" in msg and msg["command"] == "set_mode":
                        self.reset_policy_mode(msg["mode"])
                    elif "sync_error_ms" in msg:
                        err = float(msg["sync_error_ms"])
                        cid = msg.get("client_id", "client")
                        seq = msg.get("seq", 0)
                        # Ignore Packet #1 ARP/Packet-In cold start; trigger on genuine congestion
                        if seq > 2 and err > SLA_ERROR_THRESHOLD_MS and self.policy_mode == "dynamic" and not self.qos_active:
                            self.enforce_dynamic_qos_on_all_switches(
                                reason=f"SLA Breach on {cid} (seq={seq}, sync_error={err:.2f}ms > {SLA_ERROR_THRESHOLD_MS}ms)"
                            )
            except Exception as e:
                self.logger.debug(f"Telemetry parse warning: {e}")
            finally:
                conn.close()

    def _port_stats_poller(self):
        """Periodically polls OpenFlow switch port byte counters every 2 seconds."""
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                req = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                dp.send_msg(req)
            hub.sleep(2)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()
        for stat in ev.msg.body:
            if stat.port_no > 100:
                continue
            key = (dpid, stat.port_no)
            if key in self.port_tx_bytes:
                prev_bytes, prev_time = self.port_tx_bytes[key]
                dt = max(now - prev_time, 0.1)
                mbps = ((stat.tx_bytes - prev_bytes) * 8.0) / (dt * 1_000_000.0)
                if mbps > 8.0 and self.policy_mode == "dynamic" and not self.qos_active:
                    self.enforce_dynamic_qos_on_all_switches(
                        reason=f"Congestion Detected on s{dpid}-eth{stat.port_no} ({mbps:.2f} Mbps > 8.0 Mbps threshold)"
                    )
            self.port_tx_bytes[key] = (stat.tx_bytes, now)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port
        out_port = self.mac_to_port[dpid].get(eth.dst, ofproto.OFPP_FLOOD)

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        if out_port != ofproto.OFPP_FLOOD and ip_pkt and udp_pkt:
            is_sync = (udp_pkt.dst_port == SYNC_UDP_PORT or udp_pkt.src_port == SYNC_UDP_PORT)
            if is_sync and self.qos_active:
                q_id = HIGH_PRIO_QUEUE
                prio = 100
            else:
                q_id = LOW_PRIO_QUEUE
                prio = 50

            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ip_proto=17,
                udp_dst=udp_pkt.dst_port,
                udp_src=udp_pkt.src_port,
                eth_dst=eth.dst
            )
            actions = [parser.OFPActionSetQueue(q_id), parser.OFPActionOutput(out_port)]
            self.add_flow(datapath, prio, match, actions, idle_timeout=30)
        elif out_port != ofproto.OFPP_FLOOD:
            # Install standard L2 rule for ARP/ICMP/TCP
            match = parser.OFPMatch(eth_dst=eth.dst, eth_src=eth.src)
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(datapath, 10, match, actions, idle_timeout=60)
        else:
            actions = [parser.OFPActionOutput(out_port)]

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions, data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        )
        datapath.send_msg(out)