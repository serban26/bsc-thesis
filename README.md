# SDN Round-Robin Load Balancer (Sticky Sessions)

A simple load balancer implemented with the Ryu SDN controller and Mininet, distributing TCP traffic over multiple backend servers using a round-robin policy.

---

**Highlights**
- **Round-robin scheduling** for new TCP connections.
- **Sticky sessions** (source IP/port persistence) to avoid race conditions in TCP handshakes and retransmissions.
- **ARP handling** for the VIP so clients see a single reachable IP.
- Flow installation (forward + reverse) to move packet processing to the switch (OVS) after the controller makes decisions.
- **Offline simulator + web UI** to explore schedules, loads and server utilisation without running Mininet every time.
- **Live React frontend** that follows the backend in real time: requests appear at their arrival time, are coloured by status (pending / assigned / unassigned), and statistics update as the run progresses.

**Tech stack**: Python 3.7 (Ryu, Flask), React 18, Node/npm, OpenFlow 1.3, Open vSwitch, Mininet, HTML/CSS/JS.

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

The web frontend consumes the CSV outputs (`results/schedule.csv`, `results/stats.csv`) and shows a live view of:

- All requests, with arrival time, assigned server, start and finish time.
- Status per request (pending, assigned, unassigned/too consuming).
- Summary statistics for the experiment.

Default VIP used in examples: `10.0.0.100`.

---

## Quick start / How to run

### 0. Prerequisites

- Python 3.7+ (for the Ryu/Mininet part)
- Mininet
- Open vSwitch
- Ryu controller (Python package)
- Node.js + npm (for building the React frontend)
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

To build the React frontend (only needed once or whenever you change the `frontend/` code):

```bash
cd frontend
npm install
npm run build
```

This produces a static build under `frontend/build/` which is served by `app.py`.

---

### 1. Option A – Backend only (simulator + metrics)

Use this if you just want to run the scheduling backend, generate traces/CSVs and look at the numeric results.

From the repository root:

```bash
source .venv/bin/activate
./scripts/run_all.sh
```

What this does (high level):

- Runs the **online scheduler** with the round-robin policy.
- Simulates a batch of incoming requests with non-uniform size/duration.
- Applies simple server capacity constraints.
- Writes logs/CSVs and summary statistics into `results/`.

You can rerun `./scripts/run_all.sh` as many times as you like with different parameters (see the Python modules for tunable settings such as number of servers, request arrival pattern, etc.).

---

### 2. Option B – Interactive web frontend (live schedule view)

Use this if you want a **visual, live timeline** of requests and servers, and to explore different scenarios from your browser.

1. Ensure the frontend is built (see step 0):

   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

2. Start the Flask app from the repo root:

   ```bash
   source .venv/bin/activate
   python app.py
   ```

   By default this serves the UI on:

   ```text
   http://127.0.0.1:8000
   ```

3. In a separate terminal, start the SDN experiment pipeline (Mininet, Ryu, servers, and the client that sends requests according to the input file):

   ```bash
   source .venv/bin/activate
   ./scripts/run_all.sh
   ```

4. Open the browser at `http://127.0.0.1:8000`. The UI shows:

   - The input file (editable text area).
   - A live **schedule table**:
     - Each request as a row.
     - Its assigned server, arrival, start and finish times.
     - Status chip:
       - **Pending (blue)** – request has not arrived yet or has arrived but is not scheduled.
       - **Assigned (green)** – request scheduled and running or completed.
       - **Unassigned (red)** – request marked as too consuming / no server could handle it.
   - A live **console output** view mirroring the backend logs.
   - A **Stats** section with aggregated metrics once the run finishes.

Requests appear in the table **only once their arrival time has passed**, so the frontend timeline matches the simulated time progression.

---

## Repository layout

- `controller/rrlb.py`  
  Ryu application implementing the round-robin load balancer with sticky sessions.

- `models.py`, `online_scheduler.py`, `round_robin_algorithm.py`, `report.py`  
  Simulation backend: request/server models, online scheduler, round-robin policy and reporting/metrics.

- `app.py`  
  Flask app that:
  - Serves the built React frontend from `frontend/build/`.
  - Exposes a small HTTP API:
    - `GET /api/load-input` / `POST /api/save-input` – manage `input/input.txt`.
    - `POST /api/run` – kick off the backend pipeline via `scripts/run_all.sh`.
    - `GET /api/status` – current run status + console output.
    - `GET /results/schedule.csv`, `GET /results/stats.csv` – CSVs consumed by the frontend.

- `frontend/`  
  React single-page application for the live schedule view:
  - `src/App.js` – main layout and polling logic (status + CSVs).
  - `src/components/` – input panel, schedule table, status/console panel, stats section.
  - `src/styles/` – dark theme, table styling, coloured status chips.
  - `build/` – static bundle served by Flask (not tracked in Git).

- `scripts/` – Helper scripts:
  - `run_all.sh` – Convenience script to run the full backend experiment pipeline (Mininet, Ryu, servers, client, scheduler and CSV/report generation).
  - `start_ryu.sh` – Starts the Ryu controller.
  - `server.sh` – Starts simple HTTP/echo backend servers in Mininet hosts.
  - `client_round_robin.sh` – Client load generator hitting the VIP according to `input/input.txt`.
  - `start_mininet.sh` (if present) – Wrapper to start the custom Mininet topology.

- `topo/lb_topo.py`  
  Custom Mininet topology used in SDN experiments (single switch + client + multiple servers).

- `input/input.txt`  
  Text-based experiment definition: servers, global parameters, and request list (name, size, duration, arrival time).

- `results/`  
  Experiment outputs, logs, CSVs and plots – including:
  - `schedule.csv` – per-request schedule (name, server, arrival, start, finish, status).
  - `stats.csv` – aggregated metrics per run / per server.

---

## How it works (short)

### Controller path (Ryu + Mininet)

- The Ryu controller listens for `PacketIn` events from OVS.
- **ARP:** When an ARP request targets the VIP, the controller replies on behalf of the VIP.
- **TCP:** On SYN (new connection) the controller:
  - Picks the next backend server in round-robin order.
  - Records a sticky mapping keyed by `(src_ip, src_port)`.
  - Installs forward and reverse flows on the switch, rewriting IP/MAC addresses as needed.
- **Sticky sessions:** For retransmitted SYNs or any packet belonging to a known session, the existing mapping is reused.
- Barrier messages and appropriate flow priorities minimise race conditions when flows are installed.

### Offline scheduler + React frontend

- Requests are modelled with arrival time, duration and a simple “size”.
- Servers have capacity limits (e.g. max concurrent units / memory).
- The round-robin policy assigns requests in order while respecting capacity and queueing.
- When a request **cannot be scheduled** (e.g. would exceed capacity or take too long), it is marked as unassigned / too consuming and not retried indefinitely.
- The scheduler writes its decisions to `results/schedule.csv` and summary metrics to `results/stats.csv`.
- The React frontend polls periodically:
  - `/api/status` for run state and console output.
  - `results/schedule.csv` and `results/stats.csv` for live data.
- On the UI side:
  - Requests only appear once their **arrival time** has passed (based on the run start time).
  - Each request row is coloured and tagged according to its status.

---

## Development notes

- Code is organised for clarity and experimentation, not for production throughput.
- The simulator and React + Flask UI are a convenient way to design, debug and evaluate algorithms before (or alongside) running full Mininet experiments.
- For debugging the controller, you can run:

  ```bash
  ryu-manager --verbose controller/rrlb.py
  ```

---

## Results & experiments

- Run `./scripts/run_all.sh` to generate new experiment runs.  
- Collect logs, CSVs and figures in `results/`.  
- Use the web UI (`python app.py` + browser) to visually inspect schedules, per-server utilisation and queueing patterns.  
- You can extend the backend with additional scheduling algorithms and still reuse the same simulator and frontend to compare policies side by side.
