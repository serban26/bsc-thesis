#!/usr/bin/env bash
cd ~/Desktop/teza

num_servers=$(head -n1 input/input.txt | awk '{print $1}')

sudo -E mn --custom topo/lb_topo.py --topo lb \
    --controller=remote,ip=127.0.0.1,port=6633 \
    --switch ovsk,protocols=OpenFlow13 <<EOF
$(for i in $(seq 2 $((num_servers + 1))); do
  echo "h$i bash -lc 'cd /home/luca/Desktop/teza && ./scripts/server.sh' &"
done)
h1 bash -lc 'cd /home/luca/Desktop/teza && sleep 2 && ./scripts/client_round_robin.sh'
exit
EOF
