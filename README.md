# SDN Load Balancer — BSc Thesis

Prototype SDN load balancer built with Mininet, Open vSwitch and Ryu.
A client sends traffic to a virtual IP; the controller picks a backend
server for each new flow and installs OpenFlow rules on the switch so
the rest of the flow is handled in the data plane.

## How it works

1. A new flow arrives at the switch → `PacketIn` goes to the controller
2. The controller runs the selected algorithm and picks a backend
3. Two flow rules are installed (client→server and server→client),
   rewriting the VIP to the real server IP so the client never sees the backend
4. Following packets are handled by the switch directly
5. When a flow expires the controller gets a `FlowRemoved` event and
   updates the metrics

## Topology

```
  h1 (10.0.0.1) ──┐
  h2 (10.0.0.2) ──┤
  h3 (10.0.0.3) ──┤  s1 (OVS, OpenFlow 1.3) ◀── Ryu  127.0.0.1:6633
  h4 (10.0.0.4) ──┘

  VIP : 10.0.0.100  /  VMAC : 00:00:00:00:01:00
```

h1 is the client. h2/h3/h4 each run a tiny HTTP server.

## Structure

```
controller/
  load_balancer.py        main Ryu app
  metrics.py              per-server metrics + scoring
  algorithms/
    base.py               abstract base class
    registry.py           name → class lookup
    round_robin.py        weighted round robin
    least_connections.py  pick the least loaded backend

topo/
  run_net.py              interactive Mininet runner
  run_net_auto.py         headless runner used by run_experiment.sh

scripts/
  run_experiment.sh       one-command experiment runner (interactive menu)
  plot_metrics.py         generates result plots
  _clean_net.sh           cleans leftover OVS state

tests/
  test_metrics.py
  test_round_robin.py
```

## Setup

```bash
python3.7 -m venv .venv
source .venv/bin/activate
pip install ryu eventlet==0.30.2 pytest matplotlib
```

## Running an experiment

```bash
source .venv/bin/activate
sudo bash scripts/run_experiment.sh
```

The script asks a few questions (algorithm, number of requests, delay
between requests) then runs everything automatically — starts Ryu,
runs Mininet, fires traffic, stops everything and drops plots in `results/`.

## Algorithms

Both live under `controller/algorithms/` and implement the same interface,
so switching is just choosing at the prompt (or setting `LB_ALGORITHM`).

**round_robin** — cycles through backends in order, ignores live metrics.
Good baseline.

**least_connections** — always picks the backend with the fewest active
flows, weighted by capacity. Adapts to uneven response times.

## Metrics collected per backend

| metric | what it measures |
|--------|-----------------|
| `active_conns` | open flows right now (main signal for least-connections) |
| `total_bytes` | cumulative bytes served |
| `throughput_bps` | recent throughput from port stats |
| `conn_rate_eps` | EWMA arrival rate |
| `avg_flow_dur_s` | EWMA of completed flow durations |
| `port_errors` | tx/rx errors + drops |
| `score` | composite of the above, lower = preferred |

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/
```
