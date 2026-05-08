#!/usr/bin/env python3
"""
Mininet runner for Phase 2.

Builds the same topology as topo/simple_topo.py but uses the Mininet
Python API so we can:

  * auto-start a tiny HTTP server on h2/h3/h4 that prints which host
    served the request (useful to *see* the load balancer rotating).
  * pre-populate ARP tables so the client always reaches the VIP via
    the controller-supplied VMAC.
  * drop into a CLI for manual `pingall`, `h1 curl`, `iperf` etc.

Run as root:  sudo python3 topo/run_net.py
"""

import os
import shutil
import signal
import sys
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


VIP = "10.0.0.100"
VMAC = "00:00:00:00:01:00"
CLIENT_IP = "10.0.0.1"
CLIENT_MAC = "00:00:00:00:00:01"
HTTP_PORT = 8000


def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=False, build=False)

    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=6633)

    s1 = net.addSwitch("s1", protocols="OpenFlow13")

    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
    net.addLink(h1, s1)

    servers = []
    for i in (2, 3, 4):
        h = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                        mac=f"00:00:00:00:00:0{i}")
        net.addLink(h, s1)
        servers.append(h)

    return net, h1, servers


_SCRIPT_DIR = "/tmp/lb_servers"


def _prepare_script_dir():
    """Recreate the script directory so we never hit stale-owner permission
    errors (the sticky bit on /tmp prevents overwriting another user's file)."""
    shutil.rmtree(_SCRIPT_DIR, ignore_errors=True)
    os.makedirs(_SCRIPT_DIR, mode=0o755, exist_ok=True)


def _write_server_script(host_name: str, host_ip: str) -> str:
    """Write a self-contained HTTP server script to _SCRIPT_DIR and return
    its path.

    Writing to a file (rather than passing a -c '...' one-liner) avoids
    all shell-quoting problems: h.cmd() runs through a shell, so any
    single-quotes or special characters inside a -c argument will silently
    break the Python code.
    """
    path = f"{_SCRIPT_DIR}/lb_server_{host_name}.py"
    body = f"served by {host_name} ({host_ip})"
    script = f"""\
#!/usr/bin/env python3
import http.server
import socketserver

BODY = b"{body}\\n"
PORT = {HTTP_PORT}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    # silence the per-request access log
    def log_message(self, fmt, *args):
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", PORT), _Handler) as srv:
    srv.serve_forever()
"""
    with open(path, "w") as fh:
        fh.write(script)
    return path


def disable_offload(hosts):
    """Disable TX checksum offloading on every host interface.

    OVS rewrites IP addresses using SetField actions and must also update
    the TCP/UDP checksum.  Linux veth pairs send data packets with
    CHECKSUM_PARTIAL (the kernel only fills in the pseudo-header checksum,
    expecting the NIC to finish the full checksum).  OVS intercepts the
    packet before any real NIC, so the final checksum is never computed,
    and OVS's incremental update produces a wrong result.

    Disabling TX offloading (tx/gso/tso) forces the kernel to compute the
    complete checksum in software *before* handing the packet to OVS, so
    OVS's incremental update after the IP rewrite is correct.

    This is CRITICAL for TCP to work through the load balancer.  Without it
    pings (ICMP) succeed (ICMP checksum does not include IP src/dst) but
    HTTP requests fail silently (wrong TCP checksum, packets dropped by the
    receiver without any RST or error).

    We deliberately leave RX offloading alone: disabling rx checksum
    verification causes the kernel to verify checksums in software and
    would drop packets whose checksums OVS hasn't yet corrected.
    """
    for h in hosts:
        for intf in h.intfList():
            if intf.name == 'lo':
                continue
            # Try ethtool first; fall back to a direct /sys write so that
            # the setup works even when ethtool is not installed.
            result = h.cmd(
                f'ethtool -K {intf.name} tx off gso off tso off 2>&1'
            )
            if 'not found' in result or 'command not found' in result:
                # ethtool unavailable \u2014 use the sysfs knob directly
                h.cmd(
                    f'echo 0 > /sys/class/net/{intf.name}/features/tx-checksumming'
                    f' 2>/dev/null; true'
                )
                print(f'  [warn] ethtool not found; used sysfs for {intf.name}')


def start_servers(servers):
    """Start a per-backend HTTP server that always returns its own hostname.

    Each backend gets a dedicated Python script written to _SCRIPT_DIR so
        that h.cmd() never has to pass Python source code through a shell.
        All Mininet hosts share the same rootfs, so we name each script after
        the host to avoid collisions.
    """
    _prepare_script_dir()
    for h in servers:
        script = _write_server_script(h.name, h.IP())
        log = f"/tmp/lb_servers/lb_server_{h.name}.log"
        # kill any stale instance from a previous run
        h.cmd(f"pkill -f 'python3 {script}' 2>/dev/null; true")
        h.cmd(f"nohup python3 {script} > {log} 2>&1 &")
    # give the servers a moment to bind their ports
    time.sleep(0.8)


def stop_servers(servers):
    for h in servers:
        script = f"{_SCRIPT_DIR}/lb_server_{h.name}.py"
        h.cmd(f"pkill -f 'python3 {script}' 2>/dev/null; true")


def prime_arp(client, servers, vip=VIP, vmac=VMAC):
    """Pre-load ARP caches to eliminate the ARP round-trip on first request.

    * Client gets VIP  -> VMAC  (so it can reach the virtual service immediately)
    * Each server gets CLIENT_IP -> CLIENT_MAC  (so they never need to ARP for
      the client before sending a SYN-ACK, removing the biggest latency spike
      and the race between the first TCP response and the reverse-flow install)
    """
    client.cmd(f"ip neigh replace {vip} lladdr {vmac} dev {client.name}-eth0")
    for srv in servers:
        srv.cmd(
            f"ip neigh replace {CLIENT_IP} lladdr {CLIENT_MAC}"
            f" dev {srv.name}-eth0"
        )


def main():
    setLogLevel("info")
    net, h1, servers = build()
    net.build()
    net.start()

    try:
        disable_offload(net.hosts)
        start_servers(servers)
        prime_arp(h1, servers)
        print("\n=== Phase 2 LB net up ===")
        print(f"  client : {h1.name} ({h1.IP()})")
        for s in servers:
            print(f"  server : {s.name} ({s.IP()})  http://*:{HTTP_PORT}")
        print(f"  VIP    : {VIP}  (handled by Ryu controller)")
        print("Try:  h1 curl -s http://10.0.0.100:8000/")
        print("      h1 ab  -n 30 -c 5 http://10.0.0.100:8000/   (if installed)")
        print("      pingall\n")

        CLI(net)
    finally:
        stop_servers(servers)
        net.stop()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Must run as root (sudo).", file=sys.stderr)
        sys.exit(1)
    # make Ctrl-C clean
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
