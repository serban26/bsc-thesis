cd ~/Desktop/teza
sudo -E mn --custom topo/lb_topo.py --topo lb \
    --controller=remote,ip=127.0.0.1,port=6633 \
    --switch ovsk,protocols=OpenFlow13
