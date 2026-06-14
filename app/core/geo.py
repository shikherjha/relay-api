"""Geo helpers (haversine) for rescue radius + demand-locality scoring."""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def geo_decay(distance_km: float, radius_km: float) -> float:
    """Linear locality weight in [0,1]; 1 at the point, 0 at/after the radius."""
    if radius_km <= 0:
        return 0.0
    return max(0.0, 1.0 - distance_km / radius_km)
