class Server:
    def __init__(self, name, max_memory_mb, max_time_s, speed_units_per_s, ip, mac, port):
        self.name = name
        self.max_memory_mb = float(max_memory_mb)
        self.max_time_s = float(max_time_s)
        self.speed_units_per_s = float(speed_units_per_s)
        self.ip = ip
        self.mac = mac
        self.port = int(port)
        self.available_at = 0.0


class Request:
    def __init__(self, name, memory_mb, units, arrival_ms, index):
        self.name = name
        self.memory_mb = float(memory_mb)
        self.units = float(units)
        self.arrival_ms = int(arrival_ms)
        self.index = index
        self.assigned_server = None
        self.start_ms = None
        self.finish_ms = None
        self.status = None
