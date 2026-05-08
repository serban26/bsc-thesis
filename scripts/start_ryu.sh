set -e
cd "$(dirname "$0")/.."
source .venv/bin/activate

# Phase 2: adaptive load balancer with metrics
exec ryu-manager --observe-links controller/load_balancer.py
