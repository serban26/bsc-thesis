from mininet.topo import Topo

class SimpleTopo(Topo):
    def build(self):
        s1 = self.addSwitch("s1", protocols="OpenFlow13")

        client = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        self.addLink(client, s1)

        for i in range(2, 5):
            host = self.addHost(
                f"h{i}",
                ip=f"10.0.0.{i}/24",
                mac=f"00:00:00:00:00:0{i}",
            )
            self.addLink(host, s1)


topos = {"simple": SimpleTopo}
