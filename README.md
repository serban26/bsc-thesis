# SDN Round-Robin Load Balancer (Sticky Sessions)

A compact, research-oriented implementation of a Layer‑3 load balancer built on the Ryu SDN controller and OpenFlow 1.3. The controller accepts traffic to a single Virtual IP (VIP) and transparently forwards connections to a pool of backend servers using a round‑robin policy while preserving session stickiness for existing TCP connections.

This repository is part of a BSc thesis and includes scripts and a Mininet topology to reproduce experiments locally.

---

**Highlights**
- **Round‑Robin scheduling** for new TCP connections.
- **Sticky sessions** (source IP/port persistence) to avoid race conditions in TCP handshakes and retransmissions.
- **ARP handling** for the VIP so clients see a single reachable IP.
- Flow installation (forward + reverse) to move packet processing to the switch (OVS) after the controller makes decisions.

**Tech stack**: Python (Ryu), OpenFlow 1.3, Open vSwitch, Mininet.

---

**Table of Contents**
- Project overview
- Quick start
- Repository layout
- How it works (brief)
- Development & testing
- Results
- License & contact

---

**Project overview**

Clients send traffic to a single Virtual IP (VIP). The controller inspects incoming connection attempts and either: (a) assigns the flow to the next backend server in a round‑robin sequence (for new connections), or (b) preserves the mapping for established or retried connections (sticky sessions). The controller programs Open vSwitch with bidirectional flows so subsequent packets are handled in data plane.

Default VIP used in examples: `10.0.0.100`

---

**Quick start (tested on Ubuntu + Mininet)**

Prerequisites

- Python 3.7+
- Mininet
- Open vSwitch
- Ryu controller (Python package)

Install Ryu and minimal Python deps:

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip
pip3 install --user ryu eventlet
```

Run the project (three terminals recommended)

1) Start the Ryu controller (from repository root):

```bash
# recommended: run from repo root so relative imports work
ryu-manager controller/rrlb.py
# or use the helper script
./scripts/start_ryu.sh
```

2) Start the Mininet topology (in another terminal):

```bash
sudo ./scripts/start_mininet.sh
# or, to run the custom topology directly
# sudo mn --custom topo/lb_topo.py --topo lbtopo --controller remote --mac
```

3) Start backend servers (third terminal) if required by your setup:

```bash
./scripts/server.sh
```

4) Run client tests / generate load:

```bash
./scripts/client_round_robin.sh
# or use the Mininet CLI:
# mininet> h1 curl 10.0.0.100
```

Notes
- If you run Mininet locally, ensure Ryu is listening on the correct port (default 6633/6653) and that `start_mininet.sh` points the controller to `127.0.0.1`.
- Use `ryu-manager --verbose controller/rrlb.py` for detailed logs.

---

**Repository layout**

- `controller/rrlb.py` — The Ryu application implementing the round‑robin load balancer with sticky sessions.
- `topo/lb_topo.py` — Custom Mininet topology used during experiments.
- `scripts/` — Helper scripts to start the controller, topology, clients and servers:
   - `start_mininet.sh` — Launches Mininet with the custom topology.
   - `start_ryu.sh` — Starts the Ryu app (convenience wrapper).
   - `server.sh` — Starts simple HTTP/echo backends used as pool servers.
   - `client_round_robin.sh` — Simple client script to exercise the VIP and observe round‑robin behavior.
- `results/` — Experiment outputs and logs (kept out of version control when large).

---

**How it works (short)**

- The controller listens for PacketIn events from OVS.
- ARP: When an ARP request targets the VIP, the controller replies on behalf of the VIP.
- TCP: On SYN (new connection) the controller selects the next backend (round‑robin) and records the mapping keyed by `(src_ip, src_port)`; it installs two flows (forward and reverse) on the switch to rewrite addresses and speed up subsequent packets.
- Sticky sessions: On retransmitted SYNs or packets for known sessions, the controller preserves the previously assigned backend instead of reassigning.
- Barrier messages and correct flow priorities are used to reduce race conditions when programming the switch.

---

**Development notes**

- The code is organized for clarity rather than production performance; it is intended for reproducible experiments and thesis demonstrations.
- To iterate quickly on `rrlb.py`, run `ryu-manager --verbose controller/rrlb.py` and watch logs for decisions and flow installs.

Suggested improvements (ideas)
- Add UDP support and health‑checks for backends.
- Make persistence configurable (cookie timeouts, per‑client policies).

---

**Results & experiments**

Collect logs and CSVs in `results/`. The tests in `scripts/` generate simple request traces to observe server rotation and stickiness under retransmissions.

---

**License & attribution**

This repository is part of a BSc Thesis. Please contact the author for reuse or redistribution details.

**Contact / Questions**

If you want further polish, automated tests, or a `requirements.txt`/`pyproject.toml`, tell me which you'd like and I will add them.
