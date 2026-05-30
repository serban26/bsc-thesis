set -e
sudo pkill -9 -f 'mininet:' 2>/dev/null || true
sudo pkill -9 -f 'mnexec'   2>/dev/null || true
sudo pkill -9 -f 'ryu-manager' 2>/dev/null || true

# mn -c is the most complete cleanup (removes OVS bridges, netns, veth pairs)
sudo mn -c 2>/dev/null || true

# Belt-and-suspenders: remove any leftover interfaces by scanning ip link
sudo ovs-vsctl --if-exists del-br s1 2>/dev/null || true

for intf in $(ip link show 2>/dev/null | awk -F': ' '{print $2}' | grep -E '^(s1-eth|h[0-9]+-eth)'); do
    sudo ip link del "$intf" 2>/dev/null || true
done

# Remove stale portmap so the next run always gets fresh OVS port numbers.
rm -f /tmp/lb_portmap.json 2>/dev/null || true
