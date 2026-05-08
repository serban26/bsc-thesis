set -e
sudo pkill -9 -f 'mininet:' 2>/dev/null || true
sudo pkill -9 -f 'mnexec'   2>/dev/null || true

sudo ovs-vsctl --if-exists del-br s1

for i in 1 2 3 4; do
    sudo ip link del "s1-eth$i"  2>/dev/null || true
    sudo ip link del "h$i-eth0"  2>/dev/null || true
done
