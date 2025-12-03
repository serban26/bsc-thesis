# SDN Round-Robin Load Balancer (Sticky Sessions)

A simple load balancer implemented with the Ryu SDN controller and Mininet, distributing TCP traffic over multiple backend servers using a round-robin policy.

---

**Highlights**
- **Round-Robin scheduling** for new TCP connections.
- **Sticky sessions** (source IP/port persistence) to avoid race conditions in TCP handshakes and retransmissions.
- **ARP handling** for the VIP so clients see a single reachable IP.
- Flow installation (forward + reverse) to move packet processing to the switch (OVS) after the controller makes decisions.
- **Offline simulator + web UI** to explore schedules, loads and server utilisation without running Mininet every time.

**Tech stack**: Python 3.7 (Ryu, Flask), OpenFlow 1.3, Open vSwitch, Mininet, HTML/JS.

---

**Table of Contents**
- Project overview  
- Quick start / How to run  
- Repository layout  
- How it works (brief)  
- Development & testing  
- Results  

---

## Project overview

Clients send traffic to a single Virtual IP (VIP). The controller inspects incoming connection attempts and either: (a) assigns the flow to the next backend server in a round-robin sequence (for new connections), or (b) preserves the mapping for established or retried connections (sticky sessions). The controller programs Open vSwitch with bidirectional flows so subsequent packets are handled in data plane.

In addition, an **offline scheduler** models requests with duration/size and simple server capacities. This lets you run controlled experiments, generate traces and visualise how the round-robin policy behaves under non-uniform load.

Default VIP used in examples: `10.0.0.100`

---

## Quick start / How to run

### 0. Prerequisites

- Python 3.7+ (for the Ryu/Mininet part)
- Mininet
- Open vSwitch
- Ryu controller (Python package)
- `bash` and a Unix-like environment (Ubuntu recommended)

Install Python dependencies (inside the repo root):

```bash
python3 -m venv .venv
source .venv/bin/activate

python -V  # ideally 3.7 for Ryu

pip install "eventlet==0.30.2" "ryu==4.34" flask
```

You may need:

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip
```

if not already installed.

---

### 1. Option A – Backend only (simulator + metrics)

Use this if you just want to run the scheduling backend, generate traces/CSVs and look at the numeric results.

From the repository root:

```bash
source .venv/bin/activate
./run_all.sh
```

What this does (high level):

- Runs the **online scheduler** with the round-robin policy.
- Simulates a batch of incoming requests with non-uniform size/duration.
- Applies simple server capacity constraints.
- Writes logs/CSVs and summary statistics into `results/`.

You can rerun `./run_all.sh` as many times as you like with different parameters (see `run_all.sh` and the Python modules for tunable settings such as number of servers, request arrival pattern, etc.).

---

### 2. Option B – Interactive web frontend

Use this if you want a **visual timeline** of requests and servers, and to explore different scenarios from your browser.

1. Start the app:

   ```bash
   source .venv/bin/activate
   python app.py
   ```

   By default this serves the UI on:

   ```bash
   http://127.0.0.1:8000
   ```

2. Open that URL in your browser to see:

   - The list of requests (arrival time, duration, size).
   - The servers and their assigned jobs over time.
   - Basic metrics (utilisation, response time, queueing).

You can tweak the configuration in the Python files and refresh the page to see how the schedule and metrics change.

---

## Repository layout

- `controller/rrlb.py` – Ryu application implementing the round-robin load balancer with sticky sessions.
- `models.py`, `online_scheduler.py`, `round_robin_algorithm.py`, `report.py` – Simulation backend: request/server models, online scheduler, round-robin policy and reporting/metrics.
- `run_all.sh` – Convenience script to run the full backend experiment pipeline (simulator + reporting).
- `app.py` – Flask app exposing a small HTTP API and serving the interactive web UI.
- `index.html` – Single-page UI used to visualise schedules and server timelines in the browser.
- `scripts/` – Helper scripts:
  - `start_ryu.sh` – Starts the Ryu controller.
  - `server.sh` – Starts simple HTTP/echo backend servers in Mininet hosts.
  - `client_round_robin.sh` – Simple client load generator hitting the VIP.
  - `start_mininet.sh` (if present) – Wrapper to start the custom Mininet topology.
- `topo/lb_topo.py` – Custom Mininet topology used in SDN experiments (single switch + client + multiple servers).
- `results/` – Experiment outputs, logs, CSVs and plots.

---

## How it works (short)

- The Ryu controller listens for `PacketIn` events from OVS.
- **ARP:** When an ARP request targets the VIP, the controller replies on behalf of the VIP.
- **TCP:** On SYN (new connection) the controller:
  - Picks the next backend server in round-robin order.
  - Records a sticky mapping keyed by `(src_ip, src_port)`.
  - Installs forward and reverse flows on the switch, rewriting IP/MAC addresses as needed.
- **Sticky sessions:** For retransmitted SYNs or any packet belonging to a known session, the existing mapping is reused.
- Barrier messages and appropriate flow priorities minimise race conditions when flows are installed.

The **offline simulator** mirrors this logic at a higher abstraction level:

- Requests are modelled with arrival time, duration and a simple “size”.
- Servers have capacity limits (e.g. max concurrent units / memory).
- The round-robin policy assigns requests in order while respecting capacity and queueing.
- The scheduler records start time, finish time, waiting time, and which server handled each request.

---

## Development notes

- Code is organised for clarity and experimentation, not for production throughput.
- The simulator and Flask UI are a convenient way to design, debug and evaluate algorithms before (or alongside) running Mininet experiments.
- For debugging the controller, use:

  ```bash
  ryu-manager --verbose controller/rrlb.py
  ```

---

## Results & experiments

- Run `./run_all.sh` to generate new experiment runs.  
- Collect logs, CSVs and figures in `results/`.  
- Use the web UI (`python app.py`) to visually inspect schedules, per-server utilisation and queueing patterns.
