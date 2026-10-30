# SDN-Assisted UDP Time Synchronization in Mininet (with OS-Ken)

## Quick Install on Ubuntu/Debian

From a new Ubuntu or Debian machine, install the complete project from GitHub with:

```bash
curl -fsSL https://raw.githubusercontent.com/USER/REPOSITORY/main/install.sh \
  | bash -s -- https://github.com/USER/REPOSITORY.git "$HOME/TimeSync"
```

Replace `USER/REPOSITORY` with the GitHub owner and repository name. The installer:

* Installs Git, Python, Mininet, Open vSwitch, and build tools with `apt`.
* Starts and enables the Open vSwitch service.
* Installs `uv` for the current user.
* Clones the repository and creates `.venv` with `uv sync`.
* Compiles the project files to verify the installation.

After installation, use two terminals:

```bash
cd "$HOME/TimeSync"
uv run osken-manager sdn_controller.py
```

In the second terminal:

```bash
cd "$HOME/TimeSync"
sudo -E "$HOME/TimeSync/.venv/bin/python" topology.py
```

The topology needs root privileges because Mininet creates network namespaces and virtual interfaces.

## 1. Project Overview
This project implements a Network Time Protocol (NTP)-style clock synchronization application over **UDP sockets**, deployed inside a **Mininet** virtual network and managed by an **OS-Ken** Software-Defined Networking (SDN) controller.

### Why SDN for Time Synchronization?
UDP clock synchronization calculates a client's clock offset by assuming that the network delay from **Client to Server** equals the delay from **Server to Client** (symmetric path delay). When background network congestion floods a switch port, packets sit in buffers unevenly (asymmetric queuing delay and jitter), introducing massive clock synchronization errors.

To solve this, our **OS-Ken SDN Controller** inspects incoming UDP packets via **OpenFlow 1.3**. When it detects time-synchronization traffic (`UDP port 5005`), it installs a high-priority flow rule (`Priority 100`) that routes time-sync packets into a dedicated high-priority Linux Hierarchical Token Bucket (`linux-htb`) hardware/kernel queue (`Queue 1`) on Open vSwitch, bypassing background congestion (`Queue 0`).

---

## 2. System Architecture & Network Topology

The Mininet topology (`topology.py`) builds a star network around a single OpenFlow 1.3 switch (`s1`) with a `10 Mbps` bottleneck capacity and `10 ms` propagation delay:

* **Controller (`c0` - `127.0.0.1:6653`):** Runs `os-ken` (`sdn_controller.py`), managing OpenFlow tables and QoS queue assignments on `s1`.
* **Reference Server (`h1` - `10.0.0.1`):** Runs `time_sync.py --role server` on UDP port `5005` and acts as the `iperf3` congestion sink.
* **Time Sync Client 1 (`h2` - `10.0.0.2`):** Runs `time_sync.py --role client` with an artificial local clock drift (e.g., `+2.5s`) to measure synchronization accuracy.
* **Time Sync Client 2 (`h3` - `10.0.0.3`):** Secondary client (`-1.2s` offset) demonstrating concurrent multi-client support.
* **Congestion Generator (`h4` - `10.0.0.4`):** Uses `iperf3` to flood the `s1 <-> h1` link with `15 Mbps` of background UDP traffic.

### Protocol Responsibilities
| Protocol | Layer | Role in Project |
| :--- | :--- | :--- |
| **UDP** | Transport | Lightweight, connectionless 4-timestamp clock synchronization exchange (`Port 5005`) and `iperf3` congestion generation. |
| **TCP** | Transport | Reliable OpenFlow control channel (`Port 6653`) between Open vSwitch (`s1`) and the OS-Ken controller, plus monitoring agent reports. |
| **ICMP (Ping)** | Network | Host reachability diagnostics and baseline Round-Trip Time (RTT) verification (`pingall`, `ping`). |

---

## 3. Mathematical Model (4-Timestamp Exchange)

Each synchronization round exchanges 4 timestamps (packed as 8-byte IEEE 754 floats using Python's `struct.pack("!ddd", ...)`):
1. **$T_1$:** Client sends UDP request (recorded using client's drifted clock).
2. **$T_2$:** Server receives UDP request (recorded using server's reference clock).
3. **$T_3$:** Server transmits UDP reply containing $(T_1, T_2, T_3)$ (recorded using server's reference clock).
4. **$T_4$:** Client receives UDP reply (recorded using client's drifted clock).

From $(T_1, T_2, T_3, T_4)$, the client computes:
* **Round-Trip Delay ($\delta$):**
  $$\delta = (T_4 - T_1) - (T_3 - T_2)$$
* **Estimated Clock Offset ($\theta$):**
  $$\theta = \frac{(T_2 - T_1) + (T_3 - T_4)}{2}$$
* **Synchronization Error:**
  $$\text{Error} = |\theta - \text{True Offset}|$$

---

## 4. Environment Setup (Linux / CachyOS / Arch & Ubuntu with `uv`)

Because all Mininet hosts share the host Linux kernel, **Mininet** and **Open vSwitch** must be installed as system packages, while **OS-Ken** and Python dependencies are managed inside a `uv` virtual environment.

### Step 4.1: Install System Dependencies & Start Open vSwitch

**On CachyOS / Arch Linux:**
```bash
sudo pacman -Syu mininet openvswitch iproute2 iperf3
```
* **What it does:** Installs the Mininet network namespace emulator, Open vSwitch software switch, Linux `tc` (Traffic Control) utilities, and `iperf3` traffic generator.

**On Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install -y mininet openvswitch-switch iproute2 iperf3
```

**Start the Open vSwitch Kernel Module & Daemons:**
```bash
sudo modprobe openvswitch
sudo systemctl enable --now ovsdb-server.service
sudo systemctl enable --now ovs-vswitchd.service
```
* **What it does:** Loads the Linux kernel module for virtual switching and starts the Open vSwitch database (`ovsdb-server`) and forwarding daemon (`ovs-vswitchd`). Without these running, Mininet will hang at `*** Starting 1 switches s1`.

### Step 4.2: Configure the Python Environment with `uv`

```bash
# 1. Initialize uv with Python 3.11
uv init --python 3.11

# 2. Install OS-Ken (<4.0.0) and compatible Eventlet
uv add "os-ken<4.0.0" "eventlet<0.37.0" "setuptools<80" matplotlib
```
* **Why `os-ken<4.0.0`?** In `os-ken` v4.0.0+, OpenStack removed the `osken-manager` CLI executable. Pinning `<4.0.0` retains the standalone `osken-manager` runner needed for SDN labs.

```bash
# 3. Bridge the system Mininet package into the isolated uv environment
python3 -c "import mininet, os; print(os.path.dirname(os.path.dirname(mininet.__file__)))" \
  > $(uv run python -c "import site; print(site.getsitepackages()[0])")/system_mininet.pth
```
* **What it does:** Finds the global directory where `pacman`/`apt` installed the `mininet` Python bindings and writes a `.pth` pointer file inside `.venv` so `uv` can import `mininet` without module resolution errors.

---

## 5. Step-by-Step Execution & Expected Outputs

Open **two terminal windows** in the project directory.

### Step 5.1: Start the OS-Ken SDN Controller (Terminal 1)
```bash
uv run osken-manager --verbose sdn_controller.py
```
* **What this command does:** Starts the OS-Ken OpenFlow 1.3 controller listening on TCP port `6653`.
* **Expected Output:**
  The terminal will initialize `ofp_event`, print `instantiating app sdn_controller.py of TimeSyncQoSController`, and remain open waiting for switch `s1` to connect. Once Mininet starts in Terminal 2, you will see `switch_features_handler` register the switch.

### Step 5.2: Launch the Mininet Topology & QoS Queues (Terminal 2)
```bash
sudo mn -c
sudo .venv/bin/python topology.py
```
* **What these commands do:**
  * `sudo mn -c`: Cleans up any leftover virtual interfaces, OpenFlow locks, or namespaces from previous runs.
  * `sudo .venv/bin/python topology.py`: Builds hosts `h1`–`h4` and switch `s1`, applies `10 Mbps` bandwidth and `10 ms` link delays, uses `ovs-vsctl` to configure two hardware/software HTB priority queues on `s1` (`Queue 1` = Priority 1 for Time Sync; `Queue 0` = Priority 10 for background traffic), and opens the `mininet>` CLI.
* **Expected Output:**
  ```text
  *** Adding OS-Ken Remote Controller
  *** Adding Switch and Hosts
  *** Creating Links with Controlled Delay (10ms) and 10Mbps Bandwidth
  *** Starting Network
  *** Configuring OVS QoS Priority Queues on s1-eth1 and s1-eth2...
  <9 UUID strings confirming OVS QoS and Queue creation>
  *** Dropping into Mininet CLI
  mininet>
  ```
  *Note on Delay Behaviour:* When `ovs-vsctl` attaches the `linux-htb` QoS queues to switch ports (`s1-eth1`, `s1-eth2`, `s1-eth3`), it replaces the `10ms` delay qdisc on the **switch-side** interfaces while preserving the `10ms` delay qdisc on the **host-side** interfaces (`h1-eth0`, `h2-eth0`, `h3-eth0`). Thus, each one-way trip experiences exactly `10 ms` of propagation delay (`20 ms` total round-trip time).

---

## 6. Running the Experiments (Inside `mininet>` CLI)

### Experiment 1: Baseline Connectivity Check (ICMP)
```bash
mininet> pingall
```
* **What it does:** Sends ICMP Echo Requests between every pair of hosts to verify that `sdn_controller.py` learns MAC addresses and installs OpenFlow forwarding rules.
* **Expected Output:** `Results: 0% dropped (12/12 received)`.

### Experiment 2: Baseline UDP Time Synchronization (No Congestion)
```bash
mininet> h1 .venv/bin/python time_sync.py --role server &
mininet> h2 .venv/bin/python time_sync.py --role client --offset 2.5
```
* **What it does:** Starts the UDP reference server on `h1` in the background (`&`), then runs 30 synchronization exchanges from `h2` with a simulated clock offset of `+2.5` seconds.
* **Expected Output & Analysis:**
  ```text
  seq,rtt_ms,estimated_offset_s,sync_error_ms
  1,50.331,-2.487989,12.011
  2,20.352,-2.499998,0.002
  3,20.400,-2.499992,0.008
  ...
  30,20.355,-2.499994,0.006
  ```
  * **Why Packet #1 has ~50 ms RTT and ~12 ms Error:** On the very first packet, `s1` does not have a flow rule for `h2 -> h1` UDP port `5005`. It encapsulates the packet into an OpenFlow `Packet-In` message to the OS-Ken controller. This control-plane detour delays only the forward path ($T_1 \to T_2$), creating path asymmetry and a `~12 ms` error.
  * **Why Packets #2–#30 have ~20.3 ms RTT and <0.05 ms Error:** Once OS-Ken installs the OpenFlow rule (`Priority 100 -> Queue 1`), subsequent packets are switched directly in the Open vSwitch kernel fast-path. Forward delay (`10 ms`) equals return delay (`10 ms`), yielding near-zero synchronization error (`0.001 ms – 0.05 ms`).

### Experiment 3: Multi-Client Synchronization
```bash
mininet> h2 .venv/bin/python time_sync.py --role client --offset 2.5 &
mininet> h3 .venv/bin/python time_sync.py --role client --offset -1.2
```
* **What it does:** Runs two independent clients (`h2` drifted by `+2.5s`, `h3` drifted by `-1.2s`) concurrently against `h1`.
* **Expected Output:** Both clients converge on their true offsets (`-2.5000s` for `h2` and `+1.2000s` for `h3`) with sub-millisecond error.

### Experiment 4: Performance Under Congestion (SDN QoS ON vs. OFF)
```bash
mininet> h1 iperf3 -s -D
mininet> h4 iperf3 -c 10.0.0.1 -u -b 15M -t 30 &
mininet> h2 .venv/bin/python time_sync.py --role client --offset 2.5
```
* **What it does:**
  1. `h1 iperf3 -s -D`: Runs an `iperf3` server on `h1` as a background daemon.
  2. `h4 iperf3 -c 10.0.0.1 -u -b 15M -t 30 &`: Floods `15 Mbps` of UDP traffic from `h4` to `h1` across the `10 Mbps` bottleneck link (`s1-eth1`) for 30 seconds.
  3. Runs the time-sync client on `h2` during the congestion flood.
* **Expected Output Comparison:**
  * **With SDN Prioritization OFF (`self.prioritize_sync = False` in `sdn_controller.py`):** Both `iperf3` and time-sync packets compete inside `Queue 0`. The `s1-eth1` buffer fills up (`max-rate=9Mbps`), causing severe asymmetric queuing delay on the $T_1 \to T_2$ path. **RTT spikes to `80 ms – 150 ms+`** and **Synchronization Error jumps to `30 ms – 60 ms+`** (plus occasional packet `TIMEOUT` drops).
  * **With SDN Prioritization ON (`self.prioritize_sync = True` in `sdn_controller.py`):** OS-Ken directs UDP port `5005` into `Queue 1` (`priority=1`, guaranteed bandwidth). Time-sync packets bypass the congested `Queue 0` buffer completely. **RTT stays near `~20.5 ms`** and **Synchronization Error remains `< 0.5 ms`** despite the 15 Mbps flood.

---

## 7. Repository Structure
```text
.
├── README.md            # Project documentation, setup guide, and expected outputs
├── pyproject.toml       # uv project configuration and dependencies
├── time_sync.py         # UDP Time Synchronization Server & Client implementation
├── sdn_controller.py    # OS-Ken OpenFlow 1.3 Controller with QoS prioritization
└── topology.py          # Mininet topology with TCLink delays and OVS HTB queues
```