from .models import Server, Request


class RoundRobinAlgorithm:
    def __init__(self):
        self.rr_index = 0

    def can_ever_handle(self, server: Server, request: Request) -> bool:
        if request.memory_mb > server.max_memory_mb:
            return False
        if server.speed_units_per_s <= 0:
            return False
        duration = request.units / server.speed_units_per_s
        if duration > server.max_time_s:
            return False
        return True

    def schedule(self, servers, request: Request, now_ms: int):
        chosen = None
        num = len(servers)
        if num == 0:
            request.status = "no_server"
            return None
        for offset in range(num):
            idx = (self.rr_index + offset) % num
            s: Server = servers[idx]
            if not self.can_ever_handle(s, request):
                continue
            chosen = s
            self.rr_index = (idx + 1) % num
            break
        if chosen is None:
            request.status = "too_consuming"
            return None

        now_s = now_ms / 1000.0
        start_s = max(now_s, float(chosen.available_at))
        duration_s = request.units / chosen.speed_units_per_s
        finish_s = start_s + duration_s
        chosen.available_at = finish_s

        request.status = "assigned"
        request.assigned_server = chosen.name
        request.start_ms = int(round(start_s * 1000))
        request.finish_ms = int(round(finish_s * 1000))
        return chosen
