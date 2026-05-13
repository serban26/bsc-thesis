# SDN Load Balancer

A software-defined network load balancer built with Ryu + Mininet + Open vSwitch,
implementing and comparing five scheduling algorithms on a heterogeneous server pool.

## Overview

The controller intercepts every new TCP connection to a Virtual IP (10.0.0.100) and
selects a backend server using one of five algorithms. Flow rules are installed in OVS
to transparently rewrite IP/MAC headers, so neither the client nor the servers need any
special configuration.

The experiment uses 50 backend servers arranged in four performance tiers:

| Tier | Servers | Base delay    | Max concurrent | Spike ×  |
|------|---------|---------------|----------------|----------|
| A    | 20      | 5–25 ms       | 8–16           | 8×       |
| B    | 10      | 50–150 ms     | 4–8            | 5×       |
| C    | 10      | 300–600 ms    | 2–3            | 2×       |
| D    | 10      | 800–1000 ms   | 1              | 1.5×     |

## Algorithms

| Name                  | Module                               | Key idea                                      |
|-----------------------|--------------------------------------|-----------------------------------------------|
| Round Robin           | `algorithms/round_robin.py`          | Cycle through servers, ignore all metrics     |
| Weighted Round Robin  | `algorithms/weighted_round_robin.py` | Cycle proportional to static tier weight      |
| Least Connections     | `algorithms/least_connections.py`    | Route to server with fewest active flows      |
| Least Response Time   | `algorithms/least_response_time.py`  | Route to server with lowest EWMA latency      |
| Adaptive Predictive   | `algorithms/adaptive_predictive.py`  | Work-left score + dynamic weight adaptation   |

The Adaptive Predictive algorithm is the main contribution. Its routing score is
`(active_conns + 1) × avg_flow_dur_s / dyn_weight`, which combines an immediate
overload indicator with historical service time. A second EWMA layer continuously
adjusts `dyn_weight` based on each server's throughput relative to the pool average.

## Repository Layout

```
controller/
  load_balancer.py       # Ryu app: PacketIn, FlowRemoved, ARP handling
  metrics.py             # Per-server metrics (active_conns, EWMA duration, etc.)
  simple_switch.py       # Basic L2 forwarding (used separately)
  algorithms/
    base.py              # Abstract LoadBalancingAlgorithm class
    registry.py          # Algorithm lookup by name
    round_robin.py
    weighted_round_robin.py
    least_connections.py
    least_response_time.py
    adaptive_predictive.py
topo/
  simple_topo.py         # Mininet topology: 1 switch, 1 client, N servers
  run_net.py             # Entry point with traffic generator and HTTP servers
  run_net_auto.py        # Variant used by the automated experiment runner
scripts/
  run_experiment.sh      # Run one algorithm end-to-end; save results
  compare.sh             # Run all five algorithms in sequence + comparison plots
  start_ryu.sh           # Start the Ryu controller (called by run_experiment.sh)
  start_mininet.sh       # Start the Mininet network (called by run_experiment.sh)
  test_lb.sh             # Quick smoke test
  plot_metrics.py        # Per-algorithm plots (distribution, response times, etc.)
  plot_comparison.py     # Cross-algorithm comparison plots (8 figures)
  _clean_net.sh          # Kill OVS/Mininet/Ryu leftover processes
results/
  round_robin/
  weighted_round_robin/
  least_connections/
  least_response_time/
  adaptive_predictive/
  comparison/
tests/
  test_metrics.py
  test_round_robin.py
```

## Requirements

- Python 3.7+ with a virtualenv at `.venv`
- Ryu 4.34 (`pip install ryu`)
- Mininet 2.3.x (system install: `sudo apt install mininet`)
- Open vSwitch (system install)
- matplotlib, numpy, pandas (`pip install matplotlib numpy pandas`)

## Quick Start

```bash
# activate the venv
source .venv/bin/activate

# run all five algorithms and generate comparison plots (~10-15 min)
bash scripts/compare.sh

# or run a single algorithm
LB_ALGORITHM=adaptive_predictive NUM_SERVERS=50 bash scripts/run_experiment.sh
```

Results are written to `results/<algorithm>/` and comparison plots to
`results/comparison/`.

## Configuration

The following environment variables are read by the experiment scripts:

| Variable        | Default        | Description                          |
|-----------------|----------------|--------------------------------------|
| `LB_ALGORITHM`  | `round_robin`  | Algorithm name (see table above)     |
| `NUM_SERVERS`   | `50`           | Number of backend servers            |
| `NUM_REQUESTS`  | `1500`         | Total requests in the timed run      |
| `RATE`          | `40`           | Requests per second                  |
| `MAX_WEIGHT`    | `16`           | Maximum static weight (Tier A)       |

## Composite Score

Algorithm performance is summarised by a single composite score:

```
S = R × E × F
```

where:
- `R = 1 − error_rate` — reliability
- `E = 1 / (1 + mean_latency_s)` — average speed
- `F = 1 / (1 + p95_latency_s)` — tail behaviour

Results from the 50-server experiment (1500 req, 40 req/s):

| Algorithm | Mean (ms) | p95 (ms) | Errors | Score    |
|-----------|-----------|----------|--------|----------|
| AP        | 92        | 241      | 0.6 %  | **0.73** |
| LRT       | 126       | 502      | 0.4 %  | 0.59     |
| WRR       | 172       | 690      | 1.0 %  | 0.50     |
| LC        | 191       | 911      | 0.9 %  | 0.44     |
| RR        | 361       | 1144     | 1.0 %  | 0.34     |
