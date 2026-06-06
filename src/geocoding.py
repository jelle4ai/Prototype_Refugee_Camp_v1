from math import cos, radians
import requests
from geopy.geocoders import Nominatim

_GEOLOCATOR = Nominatim(user_agent="refugee-camp-planner", timeout=10)
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def geocode_city(place_name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for place_name, or None on failure / not found."""
    try:
        loc = _GEOLOCATOR.geocode(place_name, timeout=10)
        if loc:
            return float(loc.latitude), float(loc.longitude)
        return None
    except Exception:
        return None


def metres_to_latlon(
    x_m: float, y_m: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """
    Local metre offset → geographic (lat, lon).
    x_m = east, y_m = north from origin.
    Equirectangular approximation; accurate to <0.1 % for sites under 50 km.
    """
    lat = origin_lat + y_m / 111_320
    lon = origin_lon + x_m / (111_320 * cos(radians(origin_lat)))
    return lat, lon


def latlon_to_metres(
    lat: float, lon: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """Inverse of metres_to_latlon — returns (x_m, y_m)."""
    y_m = (lat - origin_lat) * 111_320
    x_m = (lon - origin_lon) * 111_320 * cos(radians(origin_lat))
    return x_m, y_m


def fetch_roads(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    origin_lat: float,
    origin_lon: float,
) -> list[list[tuple[float, float]]]:
    """
    Query the public Overpass API for highway ways within the bounding box.
    Returns a list of road polylines in local site-metre coordinates
    (x_m east, y_m north from origin).  Returns [] on any network error.
    """
    query = (
        f"[out:json][timeout:25];"
        f"way[highway]({min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f});"
        f"(._;>;);"
        f"out body;"
    )
    headers = {
        "User-Agent": "refugee-camp-planner/1.0",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(
            _OVERPASS_URL, data={"data": query}, headers=headers, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    nodes: dict[int, tuple[float, float]] = {
        el["id"]: (el["lat"], el["lon"])
        for el in data.get("elements", [])
        if el["type"] == "node"
    }

    roads: list[list[tuple[float, float]]] = []
    for el in data.get("elements", []):
        if el["type"] != "way":
            continue
        coords: list[tuple[float, float]] = []
        for nid in el.get("nodes", []):
            if nid in nodes:
                lat, lon = nodes[nid]
                x_m, y_m = latlon_to_metres(lat, lon, origin_lat, origin_lon)
                coords.append((x_m, y_m))
        if len(coords) >= 2:
            roads.append(coords)

    return roads
