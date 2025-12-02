import os
import time
import csv

from .models import Server, Request
from .round_robin_algorithm import RoundRobinAlgorithm


def load_config(path):
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    pos = 0
    num_servers = int(lines[pos].split()[0])
    pos += 1
    servers = []
    base_octet = 11
    for i in range(num_servers):
        parts = lines[pos].split()
        pos += 1
        name = parts[0]
        max_mem = parts[1]
        max_time = parts[2]
        speed = parts[3]
        ip_octet = base_octet + i
        ip = "10.0.0.{}".format(ip_octet)
        mac = "00:00:00:00:00:{:02d}".format(ip_octet)
        port = i + 2
        servers.append(Server(name, max_mem, max_time, speed, ip, mac, port))
    num_requests = int(lines[pos].split()[0])
    pos += 1
    requests = []
    for i in range(num_requests):
        parts = lines[pos].split()
        pos += 1
        name = parts[0]
        mem = parts[1]
        units = parts[2]
        if len(parts) >= 4:
            arrival_ms = int(parts[3])
        else:
            arrival_ms = 0
        requests.append(Request(name, mem, units, arrival_ms, i))
    return servers, requests


class OnlineScheduler:
    def __init__(self, config_path="input/input.txt", log_path="results/schedule.csv"):
        self.config_path = config_path
        self.log_path = log_path
        self.servers, self.request_templates = load_config(config_path)
        self.algorithm = RoundRobinAlgorithm()
        self.start_time = time.time()
        self.next_request_index = 0
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(self.log_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "arrival_ms", "server", "start_ms", "finish_ms", "status"])

    def now_ms(self):
        return int(round((time.time() - self.start_time) * 1000))

    def next_request(self):
        if self.next_request_index >= len(self.request_templates):
            return None
        template = self.request_templates[self.next_request_index]
        self.next_request_index += 1
        arrival_ms = self.now_ms()
        req = Request(template.name, template.memory_mb, template.units, arrival_ms, template.index)
        return req

    def assign_for_new_flow(self):
        req = self.next_request()
        if req is None:
            return None, None
        now_ms = req.arrival_ms
        server = self.algorithm.schedule(self.servers, req, now_ms)
        self.log(req)
        return req, server

    def log(self, req: Request):
        with open(self.log_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    req.name,
                    req.arrival_ms,
                    req.assigned_server or "",
                    req.start_ms if req.start_ms is not None else "",
                    req.finish_ms if req.finish_ms is not None else "",
                    req.status or "",
                ]
            )
