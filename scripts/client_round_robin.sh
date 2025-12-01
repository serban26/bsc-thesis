for i in $(seq 1 10); do
    echo "=== request $i ==="
    curl --no-keepalive -s -o /dev/null -w "HTTP %{http_code}\n" http://10.0.0.100/
    sleep 1
done
