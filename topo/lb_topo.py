from mininet.topo import Topo


class LbTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        try:
            with open('input/input.txt') as f:
                first = f.readline().strip()
                num_servers = int(first) if first else 0
        except FileNotFoundError:
            num_servers = 0

        self.addLink(h1, s1)

        for i in range(1, num_servers + 1):
            host_id = i + 1
            host_name = f'h{host_id}'
            ip_last_octet = 10 + i
            ip = f'10.0.0.{ip_last_octet}/24'
            mac_suffix = f"{10 + i:02d}"
            mac = f'00:00:00:00:00:{mac_suffix}'
            host = self.addHost(host_name, ip=ip, mac=mac)
            self.addLink(host, s1)


topos = {"lb": LbTopo}
