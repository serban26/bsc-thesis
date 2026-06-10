from __future__ import annotations

from typing import Sequence


class TrafficClass:
    HTTP  = "http"
    MAPS  = "maps"
    VIDEO = "video"
    AUDIO = "audio"
    VOIP  = "voip"


_PORT_MAP: dict[int, str] = {
    80:   TrafficClass.HTTP,
    8000: TrafficClass.HTTP,
    443:  TrafficClass.MAPS,
    8443: TrafficClass.MAPS,
    554:  TrafficClass.VIDEO,
    1935: TrafficClass.VIDEO,
    8554: TrafficClass.VIDEO,
    1755: TrafficClass.AUDIO,
    8080: TrafficClass.HTTP,
    5060: TrafficClass.VOIP,
    5061: TrafficClass.VOIP,
}

# DSCP values from RFC 4594
DSCP: dict[str, int] = {
    TrafficClass.VOIP:  46,   # EF
    TrafficClass.AUDIO: 34,   # AF41
    TrafficClass.VIDEO: 26,   # AF31
    TrafficClass.MAPS:  18,   # AF21
    TrafficClass.HTTP:   0,   # best effort
}

# VoIP/Audio → top 20% (Tier A), Video → top 50% (Tier A+B), rest → all
_POOL_FRACTION: dict[str, float] = {
    TrafficClass.VOIP:  0.20,
    TrafficClass.AUDIO: 0.20,
    TrafficClass.VIDEO: 0.50,
    TrafficClass.MAPS:  1.00,
    TrafficClass.HTTP:  1.00,
}

# (class, vip_port, cumulative probability) for the experiment traffic mix
TRAFFIC_MIX_CUM = [
    (TrafficClass.HTTP,  8000, 0.35),
    (TrafficClass.MAPS,   443, 0.50),
    (TrafficClass.VIDEO,  554, 0.70),
    (TrafficClass.AUDIO, 1755, 0.85),
    (TrafficClass.VOIP,  5060, 1.00),
]


def classify_traffic(dst_port: int, proto: int = 6) -> str:
    if proto == 17:
        # RTP uses this port range
        if 16384 <= dst_port <= 32767:
            return TrafficClass.VOIP
    return _PORT_MAP.get(dst_port, TrafficClass.HTTP)


def server_pool(servers: Sequence, traffic_class: str) -> list:
    """Restrict the pool to servers allowed for this traffic class."""
    frac = _POOL_FRACTION.get(traffic_class, 1.0)
    if frac >= 1.0:
        return list(servers)
    sorted_s = sorted(servers, key=lambda s: s.weight, reverse=True)
    cutoff = max(1, int(len(sorted_s) * frac))
    return sorted_s[:cutoff]
