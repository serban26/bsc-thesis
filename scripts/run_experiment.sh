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
echo -e "  ───────────────────────────────────────"
echo ""
echo -e "  ${BOLD}Algorithm${RESET}"
echo -e "    1) round_robin only"
echo -e "    2) least_connections only"
echo -e "    3) both  ${DIM}(runs twice, same traffic, then compares)${RESET}"
read -rp "$(echo -e "  ${CYAN}?${RESET} Choose [1/2/3]: ")" _algo_choice
case "$_algo_choice" in
    2) ALGOS=("least_connections") ;;
    3) ALGOS=("round_robin" "least_connections") ;;
    *) ALGOS=("round_robin") ;;
esac

NUM_REQUESTS=$(ask "Number of HTTP requests per run" "30")
REQUEST_DELAY=$(ask "Delay between requests (seconds)" "0.5")
DRAIN_SECS=5

echo ""
echo -e "  ${GREEN}▶  algorithm(s) :${RESET} ${ALGOS[*]}"
echo -e "  ${GREEN}▶  requests     :${RESET} $NUM_REQUESTS"
echo -e "  ${GREEN}▶  delay        :${RESET} ${REQUEST_DELAY}s between requests"
echo ""
read -rp "$(echo -e "  ${CYAN}?${RESET} Press Enter to start (Ctrl-C to cancel) ")" _

# ── cleanup helper ────────────────────────────────────────────────────────
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

# ── wipe previous results ────────────────────────────────────────────────
echo -e "${BOLD}Clearing previous results…${RESET}"
rm -rf results/
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
    LB_DELAY="$REQUEST_DELAY" \
    LB_REQUESTS_CSV="$REQUESTS_CSV" \
        sudo -E python3 topo/run_net_auto.py

    sleep 2
    stop_ryu
    sudo ./scripts/_clean_net.sh 2>/dev/null || true

    echo ""
    echo -e "  Generating per-algorithm plots…"
    "$VENV_PYTHON" scripts/plot_metrics.py \
        --metrics "$METRICS_FILE" \
        --requests "$REQUESTS_CSV" \
        --out "results/${ALGO}"
done

# ── comparison plots (only when both algorithms ran) ──────────────────────
if [[ ${#ALGOS[@]} -eq 2 ]]; then
    echo ""
    echo -e "${BOLD}━━━  Generating comparison plots  ━━━━━━━━━━━━━━━━━━━━━${RESET}"
    "$VENV_PYTHON" scripts/plot_comparison.py \
        --rr-metrics  "/tmp/lb_metrics_round_robin.jsonl" \
        --rr-requests "/tmp/lb_requests_round_robin.csv" \
        --lc-metrics  "/tmp/lb_metrics_least_connections.jsonl" \
        --lc-requests "/tmp/lb_requests_least_connections.csv" \
        --out "results/comparison"
fi

echo ""
echo -e "${GREEN}${BOLD}  Done.${RESET}"
for ALGO in "${ALGOS[@]}"; do
    echo -e "  ${DIM}results/${ALGO}/${RESET}"
done
if [[ ${#ALGOS[@]} -eq 2 ]]; then
    echo -e "  ${DIM}results/comparison/${RESET}"
fi
echo ""


VENV_PYTHON="$(pwd)/.venv/bin/python"
VENV_RYU="$(pwd)/.venv/bin/ryu-manager"

RYU_LOG="/tmp/lb_ryu.log"
METRICS_FILE="/tmp/lb_metrics.jsonl"
REQUESTS_CSV="/tmp/lb_requests.csv"
RYU_PID_FILE="/tmp/lb_ryu.pid"

# ── colours ──────────────────────────────────────────────────────────────
BOLD="\033[1m"
CYAN="\033[1;36m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RESET="\033[0m"

ask() {
    # ask <prompt> <default>  → echoes the answer
    local prompt="$1" default="$2" answer
    read -rp "$(echo -e "  ${CYAN}?${RESET} ${prompt} ${YELLOW}[${default}]${RESET}: ")" answer
    echo "${answer:-$default}"
}

pick_algorithm() {
    echo -e "\n  ${BOLD}Algorithm${RESET}" >&2
    echo -e "    1) round_robin        — cycle through backends in order" >&2
    echo -e "    2) least_connections  — always pick the least loaded backend" >&2
    local choice
    read -rp "$(echo -e "  ${CYAN}?${RESET} Choose [1/2]: ")" choice
    case "$choice" in
        2) echo "least_connections" ;;
        *) echo "round_robin" ;;
    esac
}

