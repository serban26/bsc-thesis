# Classify traffic by destination port so we can route latency-sensitive
# flows (VoIP, audio) to faster servers and mark them with the right DSCP.

from __future__ import annotations

from typing import Sequence


class TrafficClass:
    HTTP  = "http"
    MAPS  = "maps"
    VIDEO = "video"
    AUDIO = "audio"
    VOIP  = "voip"


# map destination port to traffic class
_PORT_MAP: dict[int, str] = {
    80:   TrafficClass.HTTP,
    8000: TrafficClass.HTTP,
    443:  TrafficClass.MAPS,
    8443: TrafficClass.MAPS,
    554:  TrafficClass.VIDEO,
    1935: TrafficClass.VIDEO,
    8554: TrafficClass.VIDEO,
    1755: TrafficClass.AUDIO,
    8080: TrafficClass.AUDIO,
    5060: TrafficClass.VOIP,
    5061: TrafficClass.VOIP,
}

# DSCP values from RFC 4594 - these go into the IP header
DSCP: dict[str, int] = {
    TrafficClass.VOIP:  46,   # EF
    TrafficClass.AUDIO: 34,   # AF41
    TrafficClass.VIDEO: 26,   # AF31
    TrafficClass.MAPS:  18,   # AF21
    TrafficClass.HTTP:   0,   # best effort
}

# how much of the server pool each traffic class is allowed to use.
# VoIP only goes to the fastest servers; HTTP can go anywhere.
_POOL_FRACTION: dict[str, float] = {
    TrafficClass.VOIP:  0.40,
    TrafficClass.AUDIO: 0.40,
    TrafficClass.VIDEO: 0.70,
    TrafficClass.MAPS:  1.00,
    TrafficClass.HTTP:  1.00,
}

# traffic mix for the experiments - (class, vip_port, cumulative probability)
TRAFFIC_MIX_CUM = [
    (TrafficClass.HTTP,  8000, 0.35),
    (TrafficClass.MAPS,   443, 0.50),
    (TrafficClass.VIDEO,  554, 0.70),
    (TrafficClass.AUDIO, 1755, 0.85),
    (TrafficClass.VOIP,  5060, 1.00),
]


def classify_traffic(dst_port: int, proto: int = 6) -> str:
    """Figure out what kind of traffic this is from the destination port."""
    if proto == 17:
        # RTP (VoIP media) uses this port range
        if 16384 <= dst_port <= 32767:
            return TrafficClass.VOIP
    return _PORT_MAP.get(dst_port, TrafficClass.HTTP)


def server_pool(servers: Sequence, traffic_class: str) -> list:
    """Return which servers are allowed to handle this traffic class.
    Higher priority = only the faster servers.
    """
    frac = _POOL_FRACTION.get(traffic_class, 1.0)
    if frac >= 1.0:
        return list(servers)
    sorted_s = sorted(servers, key=lambda s: s.weight, reverse=True)
    cutoff = max(1, int(len(sorted_s) * frac))
    return sorted_s[:cutoff]
