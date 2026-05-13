#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_PYTHON="$(pwd)/.venv/bin/python"
VENV_RYU="$(pwd)/.venv/bin/ryu-manager"

RYU_LOG="/tmp/lb_ryu.log"
RYU_PID_FILE="/tmp/lb_ryu.pid"

BOLD="\033[1m"
CYAN="\033[1;36m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
DIM="\033[2m"
RESET="\033[0m"

ask() {
    local prompt="$1" default="$2" answer
    read -rp "$(echo -e "  ${CYAN}?${RESET} ${prompt} ${YELLOW}[${default}]${RESET}: ")" answer
    echo "${answer:-$default}"
}

# ── interactive prompts ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  SDN Load Balancer — experiment setup${RESET}"
echo -e "  ──────────────────────────────────────────────────"
echo ""
echo -e "  ${BOLD}Algorithm${RESET}"
echo -e "    1) round_robin           — simple cycle, equal distribution"
echo -e "    2) weighted_round_robin  — cycle weighted by server capacity"
echo -e "    3) least_connections     — always pick least-loaded backend"
echo -e "    4) least_response_time   — always pick fastest-responding backend"
    echo -e "    5) adaptive_predictive   — least work left + dynamic bandwidth allocation"
    echo -e "    6) all five              — run each in sequence, then compare"
echo ""
read -rp "$(echo -e "  ${CYAN}?${RESET} Choose [1/2/3/4/5/6]: ")" _algo_choice
case "$_algo_choice" in
    2) ALGOS=("weighted_round_robin") ;;
    3) ALGOS=("least_connections") ;;
    4) ALGOS=("least_response_time") ;;
    5) ALGOS=("adaptive_predictive") ;;
    6) ALGOS=("round_robin" "weighted_round_robin" "least_connections" "least_response_time" "adaptive_predictive") ;;
    *) ALGOS=("round_robin") ;;
esac

echo ""
echo -e "  ${BOLD}Traffic parameters${RESET}"
NUM_REQUESTS=$(ask "Number of HTTP requests per run" "1500")
RATE=$(ask "Request rate (req/s sent from client)" "40")

echo ""
echo -e "  ${BOLD}Backend servers${RESET}  ${DIM}(ordered best-first: h2=fastest Tier A, hN=slowest Tier D)${RESET}"
NUM_SERVERS=$(ask "Number of backend servers (h2..hN)" "50")

echo ""
echo -e "  ${BOLD}Backend weights${RESET}  ${DIM}(Tier A weight; B gets /4, C gets /8, D gets 1 -- mirrors server capacity)${RESET}"
MAX_WEIGHT=$(ask "Max weight (Tier A)" "16")

# Tier-based weight generation: mirrors the 4-tier server pool in run_net_auto.py.
#   Tier A (fast,   first 40%): MAX_WEIGHT        -- powerful machines
#   Tier B (medium, next  30%): MAX_WEIGHT // 4   -- standard servers
#   Tier C (slow,   next  20%): MAX_WEIGHT // 8   -- underpowered
#   Tier D (legacy, last  10%): 1                 -- old hardware
#
# A linear "-1 per step" formula collapses to the same weight for all servers
# when N >> MAX_WEIGHT, making WRR indistinguishable from RR in the plots.
# Tier-based weights give WRR the same routing intent as LC/LRT/AP -- the
# fair static baseline for comparison against the dynamic algorithms.
WEIGHTS=$(python3 - <<PYEOF
n, max_w = $NUM_SERVERS, $MAX_WEIGHT
thresholds = (0.40, 0.70, 0.90)
weights = []
for i in range(n):
    frac = i / max(n - 1, 1)
    if   frac < thresholds[0]: w = max_w
    elif frac < thresholds[1]: w = max(1, max_w // 4)
    elif frac < thresholds[2]: w = max(1, max_w // 8)
    else:                      w = 1
    weights.append(w)
print(','.join(str(w) for w in weights))
PYEOF
)

DRAIN_SECS=15

echo ""
echo -e "  ${GREEN}▶  algorithm(s) :${RESET} ${ALGOS[*]}"
echo -e "  ${GREEN}▶  servers      :${RESET} $NUM_SERVERS  (h2 … h$((NUM_SERVERS + 1)))"
echo -e "  ${GREEN}▶  requests     :${RESET} $NUM_REQUESTS  at ${RATE} req/s"
echo -e "  ${GREEN}▶  weights      :${RESET} $WEIGHTS"
echo ""
read -rp "$(echo -e "  ${CYAN}?${RESET} Press Enter to start (Ctrl-C to cancel) ")" _

# ── cleanup helpers ───────────────────────────────────────────────────────
stop_ryu() {
    if [[ -f "$RYU_PID_FILE" ]]; then
        kill "$(cat "$RYU_PID_FILE")" 2>/dev/null || true
        rm -f "$RYU_PID_FILE"
        sleep 1
    fi
}

cleanup() {
    echo ""
    stop_ryu
    sudo ./scripts/_clean_net.sh 2>/dev/null || true
}
trap cleanup EXIT

source .venv/bin/activate

# ── wipe previous results ─────────────────────────────────────────────────
echo -e "${BOLD}Clearing previous results…${RESET}"
sudo rm -rf results/
mkdir -p results/

# ── run each algorithm ────────────────────────────────────────────────────
for ALGO in "${ALGOS[@]}"; do
    METRICS_FILE="/tmp/lb_metrics_${ALGO}.jsonl"
    REQUESTS_CSV="/tmp/lb_requests_${ALGO}.csv"

    echo ""
    echo -e "${BOLD}━━━  $ALGO  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

    echo -e "${BOLD}[1/3] Cleaning up…${RESET}"
    sudo ./scripts/_clean_net.sh 2>/dev/null || true
    sudo rm -f "$METRICS_FILE" "$REQUESTS_CSV" "$RYU_LOG" "$RYU_PID_FILE"

    echo -e "${BOLD}[2/3] Starting Ryu (${ALGO})…${RESET}"
    LB_ALGORITHM="$ALGO" \
    LB_METRICS_FILE="$METRICS_FILE" \
    LB_WEIGHTS="$WEIGHTS" \
    LB_NUM_SERVERS="$NUM_SERVERS" \
        "$VENV_RYU" --observe-links controller/load_balancer.py \
        > "$RYU_LOG" 2>&1 &
    echo "$!" > "$RYU_PID_FILE"
    for i in $(seq 1 20); do
        if grep -q "Listening on" "$RYU_LOG" 2>/dev/null \
        || ss -tlnp 2>/dev/null | grep -q ':6633'; then
            echo -e "  controller is up."
            break
        fi
        sleep 1
    done

    echo -e "${BOLD}[3/3] Running traffic…${RESET}"
    echo ""
    LB_REQUESTS="$NUM_REQUESTS" \
    LB_DRAIN="$DRAIN_SECS" \
    LB_RATE="$RATE" \
    LB_REQUESTS_CSV="$REQUESTS_CSV" \
    LB_NUM_SERVERS="$NUM_SERVERS" \
        sudo -E python3 topo/run_net_auto.py

    sleep 2
    stop_ryu
    sudo ./scripts/_clean_net.sh 2>/dev/null || true

    echo ""
    echo -e "  Generating per-algorithm plots…"
    LB_NUM_SERVERS="$NUM_SERVERS" \
    "$VENV_PYTHON" scripts/plot_metrics.py \
        --metrics "$METRICS_FILE" \
        --requests "$REQUESTS_CSV" \
        --out "results/${ALGO}"
done

# ── comparison plots (only when more than one algorithm ran) ──────────────
if [[ ${#ALGOS[@]} -gt 1 ]]; then
    echo ""
    echo -e "${BOLD}━━━  Generating comparison plots  ━━━━━━━━━━━━━━━━━━━━━${RESET}"

    ALGO_ARGS=()
    for ALGO in "${ALGOS[@]}"; do
        ALGO_ARGS+=("--algo" "$ALGO" "/tmp/lb_metrics_${ALGO}.jsonl" "/tmp/lb_requests_${ALGO}.csv")
    done

    LB_NUM_SERVERS="$NUM_SERVERS" \
    "$VENV_PYTHON" scripts/plot_comparison.py \
        "${ALGO_ARGS[@]}" \
        --out "results/comparison"
fi

echo ""
echo -e "${GREEN}${BOLD}  Done.${RESET}"
for ALGO in "${ALGOS[@]}"; do
    echo -e "  ${DIM}results/${ALGO}/${RESET}"
done
if [[ ${#ALGOS[@]} -gt 1 ]]; then
    echo -e "  ${DIM}results/comparison/${RESET}"
fi
echo ""
