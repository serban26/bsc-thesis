cd ~/Desktop/teza
source .venv/bin/activate
export PYTHONPATH="$PWD:$PYTHONPATH"

ryu-manager controller.rrlb &
RYU_PID=$!

sleep 3

./scripts/start_mininet.sh

python -m controller.report results/schedule.csv

kill "$RYU_PID" 2>/dev/null || true
