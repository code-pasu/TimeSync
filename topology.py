#This script builds a star topology (h1 server, h2 & h3 clients, h4 congestion generator), configures artificial link delay (10ms) and a 10 Mbps bottleneck on s1, and creates two Linux HTB queues on Open vSwitch so OS-Ken's OFPActionSetQueue actually enforces hardware/kernel bandwidth guarantees.

import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

def create_qos_queues():
    info("*** Configuring OVS QoS Priority Queues on s1-eth1 and s1-eth2...\n")
    # Limit bottleneck link to 10Mbps; give Queue 1 (Time Sync) priority 1 and guaranteed min-rate
    for iface in ["s1-eth1", "s1-eth2", "s1-eth3"]:
        os.system(
            f"ovs-vsctl -- set Port {iface} qos=@newqos -- "
            f"--id=@newqos create QoS type=linux-htb other-config:max-rate=10000000 queues=0=@q0,1=@q1 -- "
            f"--id=@q0 create Queue other-config:min-rate=1000000 other-config:max-rate=9000000 other-config:priority=10 -- "
            f"--id=@q1 create Queue other-config:min-rate=2000000 other-config:max-rate=10000000 other-config:priority=1"
        )

def run_topology():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)

    info("*** Adding OS-Ken Remote Controller\n")
    c0 = net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    info("*** Adding Switch and Hosts\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    h1 = net.addHost("h1", ip="10.0.0.1/24")  # Time Sync Reference Server
    h2 = net.addHost("h2", ip="10.0.0.2/24")  # Client 1
    h3 = net.addHost("h3", ip="10.0.0.3/24")  # Client 2
    h4 = net.addHost("h4", ip="10.0.0.4/24")  # Congestion Generator

    info("*** Creating Links with Controlled Delay (10ms) and 10Mbps Bandwidth\n")
    net.addLink(h1, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h2, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h3, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h4, s1, bw=10, delay="5ms", max_queue_size=100)

    info("*** Starting Network\n")
    net.start()
    create_qos_queues()

    info("*** Dropping into Mininet CLI\n")
    CLI(net)
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    run_topology()