# Changelog — Week 6 (May 29 – June 6, 2026)

## New file: `controller/traffic.py` — QoS traffic classification

Added a whole new module for classifying incoming connections by type. The idea is that VoIP traffic needs to go to fast servers way more than a regular HTTP page load does, so we classify each connection on arrival and route it accordingly.

Five traffic classes:
- `http` — plain web traffic
- `maps` — API / mapping traffic
- `video` — streaming video
- `audio` — audio streams
- `voip` — real-time voice

---

## Multi-client support — `topo/run_net_auto.py` + `controller/load_balancer.py`

Previously, the experiment only had one client host (h1). added support for multiple simultaneous clients because a single client firing 80 req/s was a bit unrealistic and also meant the load-generator itself could become a bottleneck

**What changed in the topology:**
- `build()` now creates `NUM_CLIENTS` hosts (h1 as before, plus hc2, hc3... using IPs in the 10.0.0.200+ range to not clash with server IPs)
- Each client runs its own load-generator script in parallel
- The total load is divided evenly — if you have 10 clients at 80 req/s total, each fires at 8 req/s

---

## Round Robin — fixed incorrect traffic distribution

The RR algorithm was calling `server_pool()` before cycling through servers. That function restricts which servers are eligible based on traffic class, so VoIP requests only went to TierA servers, video only went to TierA+B, etc. Plain round robin is not supposed to do that. It should send requests to all servers equally regardless of type.

The effect was that even RR was sending 61% of traffic to TierA (when the expected number is 20%), which made it look almost like WRR in the results.

Also simplified the implementation: before there was a separate cursor per traffic class (`_cursors: dict[str, int]`), which again doesn't make sense for plain RR. Now there's just a single `_cursor: int` that advances globally across all requests.

---

## Weighted Round Robin — two bugs fixed

**Bug 1 — `server_pool()` was being called here too**, same issue as RR above. Removed it. WRR should cycle all servers; the weights already handle the "fast servers get more" part.

**Bug 2 — `_MAX_SLOTS = 8` was capping TierA at half its intended share.** TierA servers have weight 16, so they should get 16 slots in each WRR cycle. But `_MAX_SLOTS = 8` was silently capping them at 8, meaning the actual slot ratio was `8:4:2:1` instead of the intended `16:4:2:1`. TierA was getting roughly half the traffic it should. Raised `_MAX_SLOTS` to 16.

After both fixes: TierA gets ~61% of traffic (10 servers × 16 slots / 260 total slots), which is exactly what the weight configuration implies.

---

## Least Connections and Least Response Time — same `server_pool()` bug

Both LC and LRT were also calling `server_pool()`. Removed it from both. The whole point of LC and LRT is that they route to good servers *based on live metrics* — LC learns about congestion from active_conns, LRT learns from response time history. They don't need a static tier filter; they'll figure it out themselves

---

## Tier distribution — reduced TierA from 40% to 20% of pool

With 50 servers and the old thresholds `(0.40, 0.70, 0.90)`, TierA had 20 servers. That was too many. Even without the `server_pool()` bug, having 20 fast servers out of 50 means any algorithm will naturally send ~40% of traffic there just by cycling. The numbers were hard to interpret.

Changed to `(0.20, 0.50, 0.80)`, giving:
- Tier A: 10 servers (fast, 5–25 ms)
- Tier B: 15 servers (medium, 50–150 ms)
- Tier C: 15 servers (slow, 300–600 ms)
- Tier D: 10 servers (legacy, 800–1000 ms)

Updated the same thresholds in `run_experiment.sh` (the weight-generation Python snippet) and in both plotting scripts.

---

## AP algorithm improvements

Several changes to the adaptive predictive algorithm to make it better and faster at learning:

- **Two-speed EWMA**: `_DYN_ALPHA_FAST = 0.60` during bootstrap (first 5 flows per server), `_DYN_ALPHA_SLOW = 0.20` after that. Learns fast early on, stays stable once it has data.
- **Flow counter** (`_flow_count`): tracks how many completed flows we've seen per server. Used to decide when we trust a server's learned weight.
- **Dynamic pool restriction** (`_DYN_POOL_FRAC = 0.08`): after bootstrap, exclude servers whose learned weight is below 8% of the best server's weight. In practice this filters out TierC/D once AP has measured them.
- **Per-traffic-class connection penalty** (`_AC_MULT`): VoIP gets a multiplier of 3.0, audio 2.0, video 1.5, HTTP 1.0. This makes AP more aggressively avoid busy servers for latency-sensitive traffic classes.
- **Removed the shared `_global_dur` ratchet**: previously the target weight was computed relative to a fleet-wide average duration, which could skew results if one very slow server dominated the average. Now each server's target is just `weight / avg_dur` — self-normalizing per server.

---

## Plotting fixes

Both `scripts/plot_comparison.py` and `scripts/plot_metrics.py` had the tier thresholds hardcoded as `0.4/0.6/0.8` (old values). Updated to `0.2/0.5/0.8` so the bar colors and tier boundary lines in the charts match the actual server assignments.

Added a new comparison plot `cmp_06_tier_routing.png` — a stacked bar chart showing what percentage of traffic each algorithm sent to each tier. This ended up being the clearest single visualization for explaining why AP beats the others. Replaced the old `cmp_04_fairness.png` with `cmp_04_latency_percentiles.png` (p50/p95/p99 grouped bars).

Fixed a crash in `plot_response_times()` when there were zero successful requests — the boxplot call would fail with an empty list. Added a fallback "No successful requests" text label.

---

## Experiment script improvements (`scripts/run_experiment.sh`)

- Default rate changed from 40 req/s to 80 req/s
- Added `NUM_CLIENTS` prompt (default 10)
- Added `LB_NUM_CLIENTS` to both the Ryu launch command and the mininet topology command so they stay in sync
- Added an early-exit check: if Ryu dies during startup the script now prints the log and exits instead of silently proceeding with no controller
- Added `pkill -9 ryu-manager` to the cleanup phase so stale Ryu processes from a previous failed run don't block port 6633

