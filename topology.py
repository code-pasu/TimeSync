# topology.py
import os
import time
import json
import socket
import argparse
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

def create_qos_queues():
    info("*** Configuring OVS QoS Priority Queues on s1 ports...\n")
    for iface in ["s1-eth1", "s1-eth2", "s1-eth3", "s1-eth4"]:
        os.system(
            f"ovs-vsctl -- set Port {iface} qos=@newqos -- "
            f"--id=@newqos create QoS type=linux-htb other-config:max-rate=10000000 queues=0=@q0,1=@q1 -- "
            f"--id=@q0 create Queue other-config:min-rate=1000000 other-config:max-rate=9000000 other-config:priority=10 -- "
            f"--id=@q1 create Queue other-config:min-rate=2000000 other-config:max-rate=10000000 other-config:priority=1 > /dev/null"
        )
    # Give switch s1 an IP in the 10.0.0.0/24 subnet so hosts can reach the host OS TCP Telemetry server
    os.system("ip addr add 10.0.0.254/24 dev s1 2>/dev/null; ip link set s1 up")

def set_controller_mode(mode):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("127.0.0.1", 9090))
        s.sendall((json.dumps({"command": "set_mode", "mode": mode}) + "\n").encode("utf-8"))
    time.sleep(1.0)

def run_automated_evaluation(net):
    h1, h2, h3, h4 = net.get("h1", "h2", "h3", "h4")
    py = ".venv/bin/python"

    info("\n*** Warming up ARP tables with pingAll...\n")
    net.pingAll()
    h1.cmd(f"{py} time_sync.py --role server &")
    h1.cmd("iperf3 -s -D")
    time.sleep(1)

    scenarios = [
        ("baseline_no_qos", "baseline", "1. Static Baseline (Congestion, NO SDN Prioritization)"),
        ("dynamic_sdn", "dynamic", "2. Dynamic Reactive SDN (Detects SLA Breach -> Auto-Prioritizes)"),
        ("proactive_qos", "proactive", "3. Proactive SDN QoS (Queue 1 Active from Start)")
    ]

    for tag, mode, title in scenarios:
        info(f"\n============================================================\n")
        info(f"*** Running {title}\n")
        info(f"============================================================\n")
        set_controller_mode(mode)

        # Start h2 client for 30 packets (0.4s interval = 12 seconds total)
        # Inject 15 Mbps UDP congestion flood from h4 starting at t = 3.0 seconds
        h2.sendCmd(
            f"{py} time_sync.py --role client --client-id h2 --offset 2.5 "
            f"--count 30 --interval 0.4 --telemetry-ip 10.0.0.254 --save-csv results_{tag}.csv"
        )
        time.sleep(3.0)
        info("*** [Challenging Scenario] Injecting 15 Mbps UDP Congestion Flood from h4 -> h1...\n")
        h4.cmd("iperf3 -c 10.0.0.1 -u -b 15M -t 8 &")

        output = h2.waitOutput()
        print(output)
        h4.cmd("killall -9 iperf3 2>/dev/null")
        time.sleep(2.0)

    info("\n*** Running Multi-Client Concurrency Verification (h2 + h3)...\n")
    set_controller_mode("proactive")
    h3.cmd(f"{py} time_sync.py --role client --client-id h3 --offset -1.2 --count 10 --interval 0.3 --save-csv results_multiclient_h3.csv &")
    h2.cmd(f"{py} time_sync.py --role client --client-id h2 --offset 2.5 --count 10 --interval 0.3 --save-csv results_multiclient_h2.csv")
    info("*** All CSV logs generated! Run: uv run python evaluate.py to generate plots.\n")

def build_and_run(auto_eval=False):
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")

    net.addLink(h1, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h2, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h3, s1, bw=10, delay="10ms", max_queue_size=100)
    net.addLink(h4, s1, bw=10, delay="5ms", max_queue_size=100)

    net.start()
    create_qos_queues()

    if auto_eval:
        run_automated_evaluation(net)
    else:
        CLI(net)

    os.system("ovs-vsctl --all destroy QoS -- --all destroy Queue > /dev/null 2>&1")
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-eval", action="store_true", help="Run automated Deliverable 2 evaluation suite")
    args = parser.parse_args()
    build_and_run(args.auto_eval)