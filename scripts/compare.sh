#!/usr/bin/env bash
# Regenerate comparison plots from existing per-algorithm result files.
# No experiment re-run needed — reads whatever is already in /tmp/.
#
# Usage:
#   ./scripts/compare.sh                   # auto-detect algos from /tmp
#   ./scripts/compare.sh round_robin weighted_round_robin least_connections
#   NUM_SERVERS=5 ./scripts/compare.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_PYTHON="$(pwd)/.venv/bin/python"
BOLD="\033[1m"; GREEN="\033[1;32m"; CYAN="\033[1;36m"; DIM="\033[2m"; RESET="\033[0m"

# ── resolve which algorithms to include ──────────────────────────────────
ALL_KNOWN=("round_robin" "weighted_round_robin" "least_connections" "least_response_time" "adaptive_predictive")

if [[ $# -gt 0 ]]; then
    # Explicit list passed as arguments
    ALGOS=("$@")
else
    # Auto-detect: any algo whose requests CSV exists in /tmp
    ALGOS=()
    for algo in "${ALL_KNOWN[@]}"; do
        if [[ -f "/tmp/lb_requests_${algo}.csv" ]]; then
            ALGOS+=("$algo")
        fi
    done
fi

if [[ ${#ALGOS[@]} -eq 0 ]]; then
    echo -e "${BOLD}No result files found in /tmp.${RESET}"
    echo "Run the experiment first:  ./scripts/run_experiment.sh"
    exit 1
fi

echo ""
echo -e "${BOLD}  Comparison plot generator${RESET}"
echo -e "  ─────────────────────────────────────────"
echo -e "  Algorithms: ${GREEN}${ALGOS[*]}${RESET}"
echo ""

# ── check files exist ─────────────────────────────────────────────────────
MISSING=0
for algo in "${ALGOS[@]}"; do
    metrics="/tmp/lb_metrics_${algo}.jsonl"
    requests="/tmp/lb_requests_${algo}.csv"
    if [[ ! -f "$requests" ]]; then
        echo -e "  [warn] missing requests CSV: $requests"
        MISSING=1
    fi
    if [[ ! -f "$metrics" ]]; then
        echo -e "  [warn] missing metrics JSONL: $metrics  (utilization plot will be empty)"
    fi
done
if [[ $MISSING -eq 1 ]]; then
    echo ""
    echo "Some files are missing.  Run the experiment for those algorithms first."
    exit 1
fi

# ── resolve NUM_SERVERS ───────────────────────────────────────────────────
if [[ -z "${NUM_SERVERS:-}" ]]; then
    # Try to infer from the first requests CSV: count unique server names
    first_csv="/tmp/lb_requests_${ALGOS[0]}.csv"
    NUM_SERVERS=$(awk -F',' 'NR>1 && $2~/^h[0-9]+$/ {print $2}' "$first_csv" \
                  | sort -u | wc -l | tr -d ' ')
    # Fall back to 3 if we couldn't detect
    [[ "$NUM_SERVERS" -lt 1 ]] && NUM_SERVERS=3
    echo -e "  Auto-detected NUM_SERVERS=${CYAN}${NUM_SERVERS}${RESET}"
fi

# ── build --algo args ─────────────────────────────────────────────────────
ALGO_ARGS=()
for algo in "${ALGOS[@]}"; do
    ALGO_ARGS+=("--algo" "$algo" "/tmp/lb_metrics_${algo}.jsonl" "/tmp/lb_requests_${algo}.csv")
done

OUT_DIR="results/comparison"
mkdir -p "$OUT_DIR"

echo -e "  Output: ${GREEN}${OUT_DIR}/${RESET}"
echo ""

source .venv/bin/activate

LB_NUM_SERVERS="$NUM_SERVERS" \
"$VENV_PYTHON" scripts/plot_comparison.py \
    "${ALGO_ARGS[@]}" \
    --out "$OUT_DIR"

echo ""
echo -e "${GREEN}${BOLD}  Done.${RESET}  Plots written to ${OUT_DIR}/"
echo ""
