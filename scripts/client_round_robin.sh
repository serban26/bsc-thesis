cd ~/Desktop/teza

num_servers=$(head -n1 input/input.txt | awk '{print $1}')
num_requests_line=$((num_servers + 2))
num_requests=$(sed -n "${num_requests_line}p" input/input.txt | awk '{print $1}')
first_req_line=$((num_servers + 3))

start_ms=$(date +%s%3N)

for i in $(seq 0 $((num_requests - 1))); do
    line=$((first_req_line + i))
    read req_name mem units arrival_ms <<<"$(sed -n "${line}p" input/input.txt)"
    now_ms=$(date +%s%3N)
    target_ms=$((start_ms + arrival_ms))
    wait_ms=$((target_ms - now_ms))
    if [ "$wait_ms" -gt 0 ]; then
        wait_sec=$((wait_ms / 1000))
        if [ "$wait_sec" -gt 0 ]; then
            sleep "$wait_sec"
        fi
    fi
    echo "=== request ${req_name} ==="
    curl --no-keepalive --max-time 2 --connect-timeout 2 -s -o /dev/null -w "HTTP %{http_code}" "http://10.0.0.100/?name=${req_name}&mem=${mem}&units=${units}&arrival=${arrival_ms}"
    echo
done
