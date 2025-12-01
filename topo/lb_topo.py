from mininet.topo import Topo

class LbTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.11/24', mac='00:00:00:00:00:11')
        h3 = self.addHost('h3', ip='10.0.0.12/24', mac='00:00:00:00:00:12')
        h4 = self.addHost('h4', ip='10.0.0.13/24', mac='00:00:00:00:00:13')
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s1)
        self.addLink(h4, s1)

topos = {"lb": LbTopo}
