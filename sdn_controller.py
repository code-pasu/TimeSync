# sThis script acts as either the reference server or a client with an artificial clock offset (e.g., +2.5 seconds) so you can measure exact Synchronization Error (abs({Estimated Offset} - {True Offset}))

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ipv4, udp, ether_types

SYNC_UDP_PORT = 5005
HIGH_PRIO_QUEUE = 1
LOW_PRIO_QUEUE = 0

class TimeSyncQoSController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TimeSyncQoSController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.prioritize_sync = True  # Toggle this via TCP/REST for comparison!

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        ethernet_protocols = pkt.get_protocols(ethernet.ethernet)
        if not ethernet_protocols:
            return
        eth = ethernet_protocols[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port
        out_port = self.mac_to_port[dpid].get(eth.dst, ofproto.OFPP_FLOOD)

        # Check if packet is UDP Time-Sync traffic
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        if out_port != ofproto.OFPP_FLOOD and ip_pkt and udp_pkt:
            if self.prioritize_sync and (udp_pkt.dst_port == SYNC_UDP_PORT or udp_pkt.src_port == SYNC_UDP_PORT):
                # Install High-Priority Flow Rule (Priority 100, Queue 1)
                match = parser.OFPMatch(
                    eth_type=ether_types.ETH_TYPE_IP,
                    ip_proto=17,  # UDP
                    udp_dst=udp_pkt.dst_port,
                    udp_src=udp_pkt.src_port,
                    eth_dst=eth.dst
                )
                actions = [parser.OFPActionSetQueue(HIGH_PRIO_QUEUE), parser.OFPActionOutput(out_port)]
                self.add_flow(datapath, 100, match, actions)
                self.logger.info(f"[SDN] Prioritized UDP Time-Sync flow on port {SYNC_UDP_PORT} -> Queue {HIGH_PRIO_QUEUE}")
            else:
                # Normal background UDP traffic -> Low Priority Queue 0
                actions = [parser.OFPActionSetQueue(LOW_PRIO_QUEUE), parser.OFPActionOutput(out_port)]
        else:
            actions = [parser.OFPActionOutput(out_port)]

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        )
        datapath.send_msg(out)