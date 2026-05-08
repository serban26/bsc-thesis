set -e
cd "$(dirname "$0")/.."
./scripts/_clean_net.sh

# Phase 2: launch via Python API so HTTP servers are auto-started.
exec sudo -E python3 topo/run_net.py
