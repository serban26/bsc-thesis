#!/usr/bin/env bash
#
# Validation tests for the adaptive load balancer.
#
# Usage:
#   1. Terminal A:  ./scripts/start_ryu.sh
#   2. Terminal B:  ./scripts/start_mininet.sh
#   3. Terminal C:  ./scripts/test_lb.sh
#
# This script drives traffic from h1 by execing into its network namespace
# via mnexec (Mininet hosts share the root user's filesystem; only the
# netns differs).
#
set -u

VIP=10.0.0.100
PORT=8000
N=${1:-30}        # number of curl requests
CONC=${2:-5}      # concurrent ab clients

# Per-request log: which backend served each request.
# Filename reflects the algorithm in use (LB_ALGORITHM); defaults to round_robin.
ALGO="${LB_ALGORITHM:-round_robin}"
OUT_FILE="${OUT_FILE:-output_${ALGO}.txt}"
: > "${OUT_FILE}"   # truncate
echo "Logging per-request matches to ${OUT_FILE}"
{
    echo "# load-balancer test run"
    echo "# date:      $(date -Iseconds)"
    echo "# algorithm: ${ALGO}"
    echo "# requests:  ${N}"
    echo "# vip:       ${VIP}:${PORT}"
    echo
} >> "${OUT_FILE}"

# Find h1's PID. Mininet renames the host's bash to "mininet:h1" via mnexec.
# We pick the *newest* live PID whose /proc/<pid>/ns/net actually exists,
# to avoid matching stale entries from a previous mininet session.
find_h1_pid() {
    local pid
    for pid in $(pgrep -f 'mininet:h1' 2>/dev/null | sort -rn); do
        if sudo test -e "/proc/${pid}/ns/net"; then
            echo "${pid}"
            return 0
        fi
    done
    return 1
}

h1_pid=$(find_h1_pid || true)
if [[ -z "${h1_pid}" ]]; then
    echo "h1 not running (no live 'mininet:h1' process found)." >&2
    echo "Start the controller and mininet first:" >&2
    echo "  Terminal A: ./scripts/start_ryu.sh" >&2
    echo "  Terminal B: ./scripts/start_mininet.sh" >&2
    echo "  Terminal C: ./scripts/test_lb.sh   (this script)" >&2
    exit 1
fi
echo "Using h1 PID = ${h1_pid}"

run_in_h1() { sudo mnexec -a "${h1_pid}" "$@"; }

echo "=== 1. ping VIP (ICMP load balancing) ==="
run_in_h1 ping -c 4 -W 1 ${VIP} || true

echo
echo "=== 2. ARP table on h1 (VIP should resolve to VMAC 00:00:00:00:01:00) ==="
run_in_h1 ip neigh show ${VIP} || true

echo
echo "=== 2b. warm-up: 3 curls so each backend ARPs h1 back ==="
for i in 1 2 3; do
    run_in_h1 curl -s --max-time 5 -o /dev/null "http://${VIP}:${PORT}/" || true
done

echo
echo "=== 3. ${N} sequential HTTP GETs — counting which server answers ==="
declare -A counts=( [h2]=0 [h3]=0 [h4]=0 [other]=0 )
for i in $(seq 1 "${N}"); do
    body=$(run_in_h1 curl -s --max-time 5 "http://${VIP}:${PORT}/" || true)
    case "${body}" in
        *"served by h2"*) srv=h2 ;;
        *"served by h3"*) srv=h3 ;;
        *"served by h4"*) srv=h4 ;;
        *)                srv=other ;;
    esac
    counts[${srv}]=$((counts[${srv}]+1))
    line=$(printf "req %3d -> %s" "${i}" "${srv}")
    echo "  ${line}"
    echo "${line}" >> "${OUT_FILE}"
done
{
    echo
    echo "# summary"
    echo "h2:    ${counts[h2]}"
    echo "h3:    ${counts[h3]}"
    echo "h4:    ${counts[h4]}"
    echo "other: ${counts[other]}"
} >> "${OUT_FILE}"
echo "  h2: ${counts[h2]}"
echo "  h3: ${counts[h3]}"
echo "  h4: ${counts[h4]}"
echo "  ??: ${counts[other]}"
echo "  → results saved to ${OUT_FILE}"

echo
echo "=== 4. concurrent load (Apache bench) ==="
if command -v ab >/dev/null 2>&1; then
    run_in_h1 ab -n 100 -c "${CONC}" -q "http://${VIP}:${PORT}/" \
        | grep -E "Requests per second|Failed|Time per request|Transfer rate"
else
    echo "  'ab' not installed; falling back to parallel curl"
    for i in $(seq 1 "${CONC}"); do
        ( for j in $(seq 1 20); do
            run_in_h1 curl -s -o /dev/null --max-time 2 "http://${VIP}:${PORT}/" ;
          done ) &
    done
    wait
    echo "  done"
fi

echo
echo "=== 5. drain: idle 20 s so flows expire and FlowRemoved fires ==="
echo "    (watch the controller — bytes/pkts/avgdur should populate,"
echo "     active_conns should drop back to 0)"
sleep 20

echo
echo "=== 6. mininet> commands you can also try manually ==="
echo "  mininet> h1 ping -c5 10.0.0.100"
echo "  mininet> h1 curl -s http://10.0.0.100:8000/"
echo "  mininet> h1 ab -n 200 -c 10 http://10.0.0.100:8000/"
echo
echo "Watch the controller terminal for the periodic metrics snapshot."
