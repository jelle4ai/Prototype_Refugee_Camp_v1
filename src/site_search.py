"""
Stage 2 – site selection.

Queries OpenStreetMap Overpass API for single open-land parcels near a city
and returns the closest ones whose area meets the requirement.

Each candidate is ONE real OSM way (farmland, meadow, grass, grassland,
brownfield, or disused industrial land).  No clustering or parcel stitching.

Projection helpers metres_to_latlon / latlon_to_metres are imported from
src.geocoding and re-exported from this module for downstream stages.
"""
from __future__ import annotations

import time
from math import ceil, cos, log2, pi, radians, sin, sqrt

import requests
import streamlit as st
import plotly.graph_objects as go

from src.geocoding import geocode_city, metres_to_latlon, latlon_to_metres  # noqa: F401

__all__ = ["metres_to_latlon", "latlon_to_metres", "find_candidates", "render_location_stage"]

# ── Constants ─────────────────────────────────────────────────────────────────
_OVERPASS_URL          = "https://overpass-api.de/api/interpreter"
_DEFAULT_MAX_RADIUS_KM = 10
_MAX_CANDIDATES        = 5
_MIN_CANDIDATES        = 3

_AREA_TOLERANCE = 0.95   # accept parcels >= 95 % of required area (rounding buffer)
_RADIUS_TIERS   = [2, 5, 10]  # km — progressive expansion

# ── Capacity estimation constants — must match layout_engine.py ───────────────
# These mirror the engine's _COMM_PITCH_X/_Y, _N_FAM_PER_COMM, _AVG_PP, and
# parcel.buffer(-35) inset.  Kept here explicitly so site_search.py has no
# import dependency on layout_engine.
_INSET_M          = 35.0   # WS5-derived boundary margin (same as engine)
_COMM_PITCH_X     = 54.0   # community E-W centre-to-centre pitch (m)
_COMM_PITCH_Y     = 48.0   # community N-S centre-to-centre pitch (m)
_PP_PER_COMMUNITY = 80     # 16 families × 5 pp/family (Appendix F)
# A site is selectable only when its Phase-0 slot count >= n_communities_needed + this.
# One spare slot absorbs a single CS5 facility collision at selection time.  A fixed
# slot margin (rather than a percentage buffer) avoids false-negatives on tight-but-valid
# sites: e.g. Site A has 15 Phase-0 slots for 14 communities needed (7 % spare), which
# a 15 % percentage buffer incorrectly rejects.
_MIN_SPARE_SLOTS  = 1

_LAND_TYPES: dict[str, str] = {
    "farmland":           "Farmland",
    "meadow":             "Meadow",
    "grass":              "Managed grassland",
    "grassland":          "Natural grassland",
    "brownfield":         "Brownfield",
    "disused_industrial": "Disused industrial land",
}
_OPEN_LAND = frozenset({"farmland", "meadow", "grass", "grassland"})

_DISCLAIMER = (
    "Screening based on map data only. Ground conditions, flood risk, "
    "legal availability and zoning must be confirmed with local authorities."
)
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_PALETTE = [
    ("#e63946", "rgba(230,57,70,0.18)"),
    ("#2a9d8f", "rgba(42,157,143,0.18)"),
    ("#e9c46a", "rgba(233,196,106,0.28)"),
    ("#f4a261", "rgba(244,162,97,0.22)"),
    ("#264653", "rgba(38,70,83,0.22)"),
]


# ── Fast capacity estimator ──────────────────────────────────────────────────

def _estimate_capacity(
    nodes_latlon: list[tuple[float, float]],
    centroid_lat: float,
    centroid_lon: float,
) -> tuple[int, int]:
    """
    Conservative Phase-0 lattice capacity estimate. Returns (est_capacity_pp, n_slots).

    Method: project the parcel polygon into local metre coordinates (centroid as
    origin), erode by _INSET_M (35 m, the WS5-derived boundary margin), then count
    how many _COMM_PITCH_X × _COMM_PITCH_Y (54×48 m) Phase-0 grid points fall
    inside or on the boundary of the inset polygon.  Each slot represents one
    community of _PP_PER_COMMUNITY = 80 people (16 families × 5 pp/family).

    The estimate is deliberately conservative:
    - Phase-0 count only — no multi-phase bonus (~+30 % on irregular sites).
    - No deduction for CS5 facility footprints (absorbed by _CAPACITY_BUFFER).
    - Uses intersects() to match the engine's candidate filter exactly.

    A site is offered as selectable when est_capacity_pp >= population * _CAPACITY_BUFFER.
    """
    try:
        from shapely.geometry import Polygon as _Poly, Point as _Pt
        pts = [latlon_to_metres(la, lo, centroid_lat, centroid_lon)
               for la, lo in nodes_latlon]
        if len(pts) < 3:
            return 0, 0
        inset = _Poly(pts).buffer(-_INSET_M)
        if inset.is_empty:
            return 0, 0
        gminx, gminy, gmaxx, gmaxy = inset.bounds
        n = 0
        y = gminy
        while y <= gmaxy + 1e-9:
            x = gminx
            while x <= gmaxx + 1e-9:
                if inset.intersects(_Pt(x, y)):
                    n += 1
                x += _COMM_PITCH_X
            y += _COMM_PITCH_Y
        return n * _PP_PER_COMMUNITY, n
    except Exception:
        return 0, 0


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = radius_km * 1_000 / 111_320
    dlon = radius_km * 1_000 / (111_320 * cos(radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _poly_area_m2(
    latlons: list[tuple[float, float]], ref_lat: float, ref_lon: float
) -> float:
    if len(latlons) < 3:
        return 0.0
    pts = [latlon_to_metres(la, lo, ref_lat, ref_lon) for la, lo in latlons]
    n = len(pts)
    area = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )
    return abs(area) / 2.0


def _centroid(latlons: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(p[0] for p in latlons) / len(latlons),
        sum(p[1] for p in latlons) / len(latlons),
    )


def _poly_bbox(latlons: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in latlons]
    lons = [p[1] for p in latlons]
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * cos(radians((lat1 + lat2) / 2))
    return sqrt(dx * dx + dy * dy)


# ── Overpass API ──────────────────────────────────────────────────────────────
# 504s and other 5xx responses from the public Overpass instance are common
# and usually transient (server-side load), so they're worth a couple of
# short-backoff retries. A malformed query (4xx) won't fix itself by
# retrying, so those fail immediately, same as any non-timeout/non-5xx error.
_OVERPASS_MAX_RETRIES = 2
_OVERPASS_BACKOFF_S   = (2.0, 4.0)


def _overpass(query: str, timeout_s: int = 65) -> tuple[dict | None, str]:
    headers = {"User-Agent": "refugee-camp-planner/1.0", "Accept": "application/json"}
    last_err = ""
    for attempt in range(_OVERPASS_MAX_RETRIES + 1):
        transient = False
        try:
            resp = requests.post(
                _OVERPASS_URL, data={"data": query},
                headers=headers, timeout=timeout_s + 10,
            )
            resp.raise_for_status()
            return resp.json(), ""
        except requests.Timeout:
            last_err  = "Overpass API timed out. Try a smaller search radius or retry."
            transient = True
        except requests.HTTPError as exc:
            status    = exc.response.status_code if exc.response is not None else None
            last_err  = f"Network error contacting Overpass: {exc}"
            transient = status is not None and 500 <= status < 600
        except requests.RequestException as exc:
            last_err = f"Network error contacting Overpass: {exc}"
        except Exception as exc:
            last_err = f"Unexpected error: {exc}"

        if not transient or attempt == _OVERPASS_MAX_RETRIES:
            break
        time.sleep(_OVERPASS_BACKOFF_S[attempt])

    return None, last_err


def _land_water_query(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> str:
    bb = f"{min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f}"
    return (
        "[out:json][timeout:60];\n(\n"
        f"  way[landuse=farmland]({bb});\n"
        f"  way[landuse=meadow]({bb});\n"
        f"  way[landuse=grass]({bb});\n"
        f"  way[natural=grassland]({bb});\n"
        f"  way[landuse=brownfield]({bb});\n"
        f"  way[landuse=industrial][disused=yes]({bb});\n"
        f"  way[natural=water]({bb});\n"
        f'  way[waterway~"^(river|stream|canal|drain)$"]({bb});\n'
        f"  way[natural=wetland]({bb});\n"
        ");\nout geom 1000;"
    )


def _roads_near_query(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> str:
    bb = f"{min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f}"
    return (
        "[out:json][timeout:30];\n"
        'way[highway~"^(primary|secondary|tertiary|residential|unclassified|track|service|road)$"]'
        f"({bb});\nout geom 500;"
    )


def _road_query(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> str:
    bb = f"{min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f}"
    return f"[out:json][timeout:25];\nway[highway]({bb});\n(._;>;);\nout body;"


# ── Element classification ────────────────────────────────────────────────────

def _classify(tags: dict) -> tuple[str, str]:
    lu  = tags.get("landuse", "")
    nat = tags.get("natural", "")
    if lu in ("farmland", "meadow", "grass", "brownfield"):
        return "land", lu
    if lu == "industrial" and tags.get("disused") == "yes":
        return "land", "disused_industrial"
    if nat == "grassland":
        return "land", "grassland"
    if nat in ("water", "wetland") or "waterway" in tags:
        return "water", nat or tags.get("waterway", "")
    return "other", ""


# ── Core parcel search ────────────────────────────────────────────────────────

def find_candidates(
    city_lat: float,
    city_lon: float,
    max_radius_km: float,
    required_area_m2: float,
) -> tuple[list[dict], str, int]:
    """
    Return the closest single-parcel candidates whose area >= 95 % of
    required_area_m2.

    Each progressive tier issues its OWN Overpass query with a matching
    bounding box.  This avoids the 1 000-element cap cutting off nearby
    parcels that exist inside a larger search box.  Stops at the first
    tier that yields >= _MIN_CANDIDATES qualifying parcels.

    Returns (candidates, error_message, used_radius_km).
    """
    min_area = required_area_m2 * _AREA_TOLERANCE
    tiers = sorted(set(_RADIUS_TIERS + [int(max_radius_km)]))

    def _geom(el: dict) -> list[tuple[float, float]]:
        return [(g["lat"], g["lon"]) for g in el.get("geometry", [])]

    saved_raw_land:       list[dict]  = []
    saved_water_bboxes:   list[tuple] = []
    used_km = tiers[-1]

    # ── Progressive Overpass queries — one per tier ───────────────────────────
    for tier in tiers:
        mn, mw, mx, me = _bbox(city_lat, city_lon, tier)
        land_data, err = _overpass(_land_water_query(mn, mw, mx, me), timeout_s=65)
        if err:
            return [], err, 0

        water_bboxes: list[tuple] = []
        raw_land:     list[dict]  = []
        for el in land_data.get("elements", []):
            if el["type"] != "way":
                continue
            tags = el.get("tags", {})
            cat, sub = _classify(tags)
            latlons = _geom(el)
            if len(latlons) < 3:
                continue
            if cat == "water":
                water_bboxes.append(_poly_bbox(latlons))
            elif cat == "land":
                raw_land.append({"latlons": latlons, "sub": sub, "id": el["id"]})

        # Count qualifying parcels strictly within the circular radius
        # (bbox is rectangular — corners can be ~41 % further than tier km)
        qualifying_count = 0
        for way in raw_land:
            c_lat, c_lon = _centroid(way["latlons"])
            area = _poly_area_m2(way["latlons"], c_lat, c_lon)
            if area >= min_area and _dist_m(c_lat, c_lon, city_lat, city_lon) <= tier * 1_000:
                qualifying_count += 1

        saved_raw_land    = raw_land
        saved_water_bboxes = water_bboxes
        used_km = tier

        if qualifying_count >= _MIN_CANDIDATES or tier >= max_radius_km:
            break

    # ── Roads query for the chosen tier ──────────────────────────────────────
    mn, mw, mx, me = _bbox(city_lat, city_lon, used_km)
    road_data, _ = _overpass(_roads_near_query(mn, mw, mx, me), timeout_s=35)
    road_elements = road_data.get("elements", []) if road_data else []
    road_nodes: list[tuple[float, float]] = [
        node
        for el in road_elements
        if el["type"] == "way"
        for node in _geom(el)
    ]

    # ── Build full parcel list with road proximity ────────────────────────────
    parcels: list[dict] = []
    for way in saved_raw_land:
        latlons = way["latlons"]
        c_lat, c_lon = _centroid(latlons)
        area    = _poly_area_m2(latlons, c_lat, c_lon)
        city_d  = _dist_m(c_lat, c_lon, city_lat, city_lon)
        if area < min_area or city_d > used_km * 1_000:
            continue
        p_bbox = _poly_bbox(latlons)
        road_d = (
            min(_dist_m(c_lat, c_lon, rlat, rlon) for rlat, rlon in road_nodes)
            if road_nodes else 99_999.0
        )
        parcels.append({
            "osm_id":           way["id"],
            "nodes_latlon":     latlons,
            "centroid_lat":     c_lat,
            "centroid_lon":     c_lon,
            "area_m2":          area,
            "area_ha":          area / 10_000,
            "land_type":        way["sub"],
            "land_label":       _LAND_TYPES.get(way["sub"], way["sub"].replace("_", " ").title()),
            "bbox":             p_bbox,
            "city_dist_m":      city_d,
            "road_dist_m":      road_d,
            "has_water":        any(_bbox_overlaps(p_bbox, wb) for wb in saved_water_bboxes),
            "required_area_m2": required_area_m2,
            "margin_pct":       (area / required_area_m2 - 1.0) * 100,
        })

    # Primary sort: distance from city centre (ascending).
    # Area, roads, water are secondary and appear only in pros/cons text.
    parcels.sort(key=lambda p: p["city_dist_m"])
    top = parcels[:_MAX_CANDIDATES]

    # Fast capacity estimate for each returned candidate (Shapely, no full placement).
    # population is exact: required_area_m2 / 45 (from requirements_engine total_area_m2).
    _pop_est = required_area_m2 / 45.0
    for c in top:
        est_cap, est_comm = _estimate_capacity(
            c["nodes_latlon"], c["centroid_lat"], c["centroid_lon"]
        )
        c["est_capacity_pp"]  = est_cap
        c["est_communities"]  = est_comm
        _n_comm_needed       = ceil(_pop_est / _PP_PER_COMMUNITY)
        c["fits_population"] = est_comm >= _n_comm_needed + _MIN_SPARE_SLOTS

    # Diagnostic — visible in the Streamlit server log
    print(
        f"[site_search] {len(parcels)} qualifying parcel(s) within {used_km} km; "
        f"showing top {len(top)}"
    )
    for i, c in enumerate(top):
        fits = "fits" if c["fits_population"] else "TOO SMALL"
        print(
            f"  Site {_LABELS[i]}: {c['city_dist_m'] / 1_000:.2f} km from city, "
            f"{c['area_ha']:.1f} ha, {c['land_label']}, "
            f"est {c['est_capacity_pp']:,} pp ({fits})"
        )

    for i, c in enumerate(top):
        c["label"]          = _LABELS[i]
        c["radius_used_km"] = used_km

    return top, "", used_km


def _fetch_parcel_roads(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    origin_lat: float, origin_lon: float,
) -> tuple[list[list[tuple[float, float]]], str]:
    data, err = _overpass(_road_query(min_lat, min_lon, max_lat, max_lon), timeout_s=30)
    if err:
        return [], err
    nodes: dict[int, tuple[float, float]] = {
        el["id"]: (el["lat"], el["lon"])
        for el in data.get("elements", []) if el["type"] == "node"
    }
    roads: list[list[tuple[float, float]]] = []
    for el in data.get("elements", []):
        if el["type"] != "way":
            continue
        seg: list[tuple[float, float]] = []
        for nid in el.get("nodes", []):
            if nid in nodes:
                lat, lon = nodes[nid]
                x, y = latlon_to_metres(lat, lon, origin_lat, origin_lon)
                seg.append((x, y))
        if len(seg) >= 2:
            roads.append(seg)
    return roads, ""


# ── Pros / cons ───────────────────────────────────────────────────────────────

def _pros_cons(c: dict) -> tuple[list[str], list[str]]:
    req_ha = c["required_area_m2"] / 10_000
    ha = c["area_ha"]
    m  = c["margin_pct"]
    pros: list[str] = []
    cons: list[str] = []

    # Raw area note (kept brief; the capacity estimate below is the real signal)
    if m >= 50:
        pros.append(f"{ha:.1f} ha — well above the {req_ha:.1f} ha minimum")
    elif m >= 0:
        pros.append(f"{ha:.1f} ha — above the {req_ha:.1f} ha minimum ({m:.0f}% margin)")
    else:
        cons.append(
            f"Slightly below area minimum ({ha:.1f} ha vs {req_ha:.1f} ha needed)"
        )

    # Usable capacity estimate (accounts for 35 m margin + shape; replaces old
    # "slim area margin" and "marginal area" cons which only looked at raw area)
    est_cap  = c.get("est_capacity_pp", 0)
    est_comm = c.get("est_communities", 0)
    pop      = round(c["required_area_m2"] / 45.0)
    if c.get("fits_population", True):
        buffer_pct = round((est_cap / pop - 1) * 100) if pop > 0 else 0
        pros.append(
            f"Estimated usable capacity ~{est_cap:,} pp "
            f"({est_comm} community slots after 35 m margin, {buffer_pct}% above required)"
        )
    else:
        cons.append(
            f"Estimated usable capacity only ~{est_cap:,} pp — "
            f"not enough for {pop:,} people after 35 m boundary margin and shape effects"
        )

    rd = c["road_dist_m"]
    if rd < 100:
        pros.append(f"Adjacent to an existing road (~{rd:.0f} m)")
    elif rd < 500:
        pros.append(f"Close to an existing road ({rd:.0f} m from parcel centre)")
    elif rd < 1_500:
        cons.append(f"Moderate distance from the nearest mapped road ({rd:.0f} m)")
    else:
        cons.append(f"Far from the nearest mapped road ({rd / 1_000:.1f} km)")

    cd = c["city_dist_m"] / 1_000
    if cd < 1:
        cons.append(f"Very close to the city centre ({cd:.1f} km) — check for urban conflicts")
    elif cd <= 5:
        pros.append(f"{cd:.1f} km from the city centre — practical access distance")
    elif cd <= 10:
        cons.append(f"{cd:.1f} km from the city centre — longer supply lines")
    else:
        cons.append(f"{cd:.1f} km from the city centre — remote location")

    if c["has_water"]:
        cons.append("Contains or adjoins mapped water / wetland — may need drainage planning")
    else:
        pros.append("No mapped water or wetland detected on parcel")

    if c["land_type"] in _OPEN_LAND:
        pros.append(f"Open land: {c['land_label'].lower()}")
    else:
        cons.append(f"Land type '{c['land_label']}' may require clearance or remediation")

    return pros, cons


# ── Plotly figures ────────────────────────────────────────────────────────────

def _search_radius_fig(city_lat: float, city_lon: float, radius_km: float) -> go.Figure:
    """Circle at the current search radius so the user can calibrate before searching."""
    n = 72
    lats, lons = [], []
    for i in range(n + 1):
        angle = 2 * pi * i / n
        dlat = radius_km / 111.32 * cos(angle)
        dlon = radius_km / (111.32 * cos(radians(city_lat))) * sin(angle)
        lats.append(city_lat + dlat)
        lons.append(city_lon + dlon)
    zoom = max(7, min(13, round(13 - log2(max(1, radius_km)) * 1.2)))
    fig = go.Figure([
        go.Scattermapbox(
            lat=lats, lon=lons,
            mode="lines",
            fill="toself",
            fillcolor="rgba(31,71,136,0.08)",
            line=dict(width=2, color="#1F4788"),
            name=f"{radius_km} km search area",
        ),
        go.Scattermapbox(
            lat=[city_lat], lon=[city_lon],
            mode="markers",
            marker=dict(size=10, color="#C2603F"),
            name="City centre",
        ),
    ])
    fig.update_layout(
        mapbox=dict(style="open-street-map",
                    center=dict(lat=city_lat, lon=city_lon), zoom=zoom),
        height=350,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01,
                    bgcolor="rgba(255,255,255,0.85)", font=dict(color="black")),
    )
    return fig


def _candidates_fig(
    candidates: list[dict],
    city_lat: float,
    city_lon: float,
    focused_label: str | None = None,
) -> go.Figure:
    traces: list = [go.Scattermapbox(
        lat=[city_lat], lon=[city_lon],
        mode="markers+text",
        marker=dict(size=10, color="black"),
        text=["City centre"], textposition="top right",
        name="City centre",
    )]

    for i, c in enumerate(candidates):
        is_focus = (c["label"] == focused_label)
        line_col, fill_col = _PALETTE[i % len(_PALETTE)]

        lats = [nd[0] for nd in c["nodes_latlon"]] + [c["nodes_latlon"][0][0]]
        lons = [nd[1] for nd in c["nodes_latlon"]] + [c["nodes_latlon"][0][1]]
        traces.append(go.Scattermapbox(
            lat=lats, lon=lons,
            mode="lines",
            fill="toself", fillcolor=fill_col,
            line=dict(color=line_col, width=3 if is_focus else 2),
            name=f"Site {c['label']}",
        ))
        traces.append(go.Scattermapbox(
            lat=[c["centroid_lat"]], lon=[c["centroid_lon"]],
            mode="markers+text",
            marker=dict(size=8 if is_focus else 6, color=line_col),
            text=[f"  {c['label']}"], textposition="middle right",
            showlegend=False,
        ))

    # Map view: zoom to focused candidate or fit all
    if focused_label:
        sel = next((c for c in candidates if c["label"] == focused_label), None)
        if sel:
            bb = sel["bbox"]
            mid_lat = (bb[0] + bb[2]) / 2
            mid_lon = (bb[1] + bb[3]) / 2
            span_km = max(
                _dist_m(bb[0], mid_lon, bb[2], mid_lon),
                _dist_m(mid_lat, bb[1], mid_lat, bb[3]),
            ) / 1_000
            zoom = 14 if span_km < 1 else 13 if span_km < 2 else 12 if span_km < 5 else 11
        else:
            mid_lat, mid_lon, zoom = city_lat, city_lon, 11
    else:
        all_lats = [city_lat] + [nd[0] for c in candidates for nd in c["nodes_latlon"]]
        all_lons = [city_lon] + [nd[1] for c in candidates for nd in c["nodes_latlon"]]
        mid_lat  = (min(all_lats) + max(all_lats)) / 2
        mid_lon  = (min(all_lons) + max(all_lons)) / 2
        span_km  = max(
            _dist_m(min(all_lats), mid_lon, max(all_lats), mid_lon),
            _dist_m(mid_lat, min(all_lons), mid_lat, max(all_lons)),
        ) / 1_000
        zoom = 12 if span_km < 5 else 11 if span_km < 12 else 10 if span_km < 25 else 9

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=mid_lat, lon=mid_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0),
        height=500,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)",
                    font=dict(color="black")),
    )
    return fig


def _detail_fig(
    candidate: dict, origin_lat: float, origin_lon: float, roads_m: list
) -> go.Figure:
    line_col = "#e63946"
    lats = [nd[0] for nd in candidate["nodes_latlon"]] + [candidate["nodes_latlon"][0][0]]
    lons = [nd[1] for nd in candidate["nodes_latlon"]] + [candidate["nodes_latlon"][0][1]]

    traces: list = [go.Scattermapbox(
        lat=lats, lon=lons,
        mode="lines",
        fill="toself", fillcolor="rgba(230,57,70,0.15)",
        line=dict(color=line_col, width=3),
        name=f"Site {candidate['label']}",
    )]

    for road in roads_m:
        if len(road) < 2:
            continue
        rlats, rlons = [], []
        for x, y in road:
            la, lo = metres_to_latlon(x, y, origin_lat, origin_lon)
            rlats.append(la)
            rlons.append(lo)
        traces.append(go.Scattermapbox(
            lat=rlats, lon=rlons,
            mode="lines",
            line=dict(color="#457b9d", width=2),
            showlegend=False,
        ))
    if roads_m:
        traces.append(go.Scattermapbox(
            lat=[None], lon=[None], mode="lines",
            line=dict(color="#457b9d", width=2),
            name=f"Roads within site ({len(roads_m)} segments)",
        ))

    bb = candidate["bbox"]
    mid_lat = (bb[0] + bb[2]) / 2
    mid_lon = (bb[1] + bb[3]) / 2
    span_km = max(
        _dist_m(bb[0], mid_lon, bb[2], mid_lon),
        _dist_m(mid_lat, bb[1], mid_lat, bb[3]),
    ) / 1_000
    zoom = 14 if span_km < 1 else 13 if span_km < 2 else 12 if span_km < 4 else 11

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=mid_lat, lon=mid_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0),
        height=480,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)",
                    font=dict(color="black")),
    )
    return fig


# ── Session-state initialisation ──────────────────────────────────────────────

def _init_state(inputs: dict) -> None:
    if "ss2_init" in st.session_state:
        return
    city = inputs.get("city", "")
    st.session_state.update({
        "ss2_init":          True,
        "ss2_city":          city,
        "ss2_geocoded":      False,
        "ss2_city_lat":      0.0,
        "ss2_city_lon":      0.0,
        "ss2_geocode_error": "",
        "ss2_radius_km":     _DEFAULT_MAX_RADIUS_KM,
        "ss2_search_done":   False,
        "ss2_candidates":    [],
        "ss2_search_error":  "",
        "ss2_used_radius":   0,
        "ss2_selected":      None,
        "ss2_focused":       None,
        "ss2_roads_done":    False,
        "ss2_roads_m":       [],
        "ss2_roads_error":   "",
        # Last successfully-fetched roads per parcel label, kept separately
        # from ss2_roads_m so a failed retry never destroys data we already
        # had for that parcel — see render_location_stage's selection block.
        "ss2_roads_cache":   {},
    })
    if city:
        result = geocode_city(city)
        if result:
            st.session_state["ss2_city_lat"], st.session_state["ss2_city_lon"] = result
            st.session_state["ss2_geocoded"] = True
        else:
            st.session_state["ss2_geocode_error"] = city


# ── Main render ───────────────────────────────────────────────────────────────

def render_location_stage() -> None:
    st.header("Site Selection")
    inputs = st.session_state["site_inputs"]

    # ── Guard: demographic mismatch ───────────────────────────────────────────
    pop      = inputs.get("population") or 0
    men      = inputs.get("men")      or 0
    women    = inputs.get("women")    or 0
    children = inputs.get("children") or 0
    split    = men + women + children
    if split != pop:
        st.warning(
            f"**Demographic mismatch:** {men:,} men + {women:,} women + {children:,} children "
            f"= {split:,}, but population is stored as {pop:,}. "
            "Figures may have been assumed rather than confirmed. "
            "You can correct them or proceed."
        )
        if st.button("Back to questions", key="btn_ss2_back"):
            st.session_state["stage"] = "input"
            st.rerun()
        st.divider()

    req_area = inputs.get("required_area_m2") or 0
    if req_area <= 0:
        st.warning(
            "Population has not been entered yet. "
            "Please return to Stage 1 and enter the headcount via the Set button."
        )
        if st.button("Back to questions", key="btn_ss2_nopop"):
            st.session_state["stage"] = "input"
            st.rerun()
        return

    if "ss2_init" not in st.session_state:
        with st.spinner(f"Locating {inputs.get('city', '')}..."):
            _init_state(inputs)
        st.rerun()

    # ── Geocoding ─────────────────────────────────────────────────────────────
    if st.session_state["ss2_geocode_error"]:
        st.error(
            f"Could not geocode **{st.session_state['ss2_geocode_error']}**. "
            "Try a more specific name below."
        )

    # Location search — always visible (Nielsen #1 visibility, #6 recognition over recall)
    if st.session_state.get("ss2_geocoded"):
        st.caption(f"Location: **{st.session_state['ss2_city']}** — update below to change")
    else:
        st.caption("Enter a location to begin site search:")
    c1, c2 = st.columns([4, 1])
    new_name = c1.text_input(
        "Place name", value=st.session_state["ss2_city"],
        key="ss2_name_input", label_visibility="collapsed",
        placeholder="e.g. Enschede, Netherlands",
    )
    if c2.button("Search", key="btn_ss2_geocode", use_container_width=True):
        result = geocode_city(new_name)
        if result:
            st.session_state.update({
                "ss2_city":          new_name,
                "ss2_geocoded":      True,
                "ss2_city_lat":      result[0],
                "ss2_city_lon":      result[1],
                "ss2_geocode_error": "",
                "ss2_search_done":   False,
                "ss2_candidates":    [],
                "ss2_search_error":  "",
                "ss2_selected":      None,
                "ss2_focused":       None,
                "ss2_roads_done":    False,
                "ss2_roads_m":       [],
                "ss2_roads_error":   "",
                "ss2_roads_cache":   {},   # new city -> labels refer to different parcels
            })
            st.rerun()
        else:
            st.error(f'Could not geocode "{new_name}". Add the country name and retry.')

    if not st.session_state["ss2_geocoded"]:
        st.info("Enter a location above to start searching for candidate sites.")
        return

    city_lat = float(st.session_state["ss2_city_lat"])
    city_lon = float(st.session_state["ss2_city_lon"])
    req_ha   = req_area / 10_000

    st.caption(
        f"City centre: {city_lat:.5f} N, {city_lon:.5f} E  |  "
        f"Required site area: **{req_ha:.1f} ha** (population × 45 m²)"
    )

    # ── Search controls ───────────────────────────────────────────────────────
    r_col, btn_col = st.columns([3, 1])
    r_col.slider(
        "Max search radius (km) — auto searches 2/5/max km progressively",
        min_value=3, max_value=30, step=1,
        key="ss2_radius_km",
    )
    radius = st.session_state["ss2_radius_km"]

    search_clicked = btn_col.button(
        "Find candidate sites", type="primary",
        use_container_width=True, key="btn_ss2_search",
    )

    if search_clicked:
        st.session_state.update({
            "ss2_selected":    None,
            "ss2_focused":     None,
            "ss2_roads_done":  False,
            "ss2_roads_m":     [],
            "ss2_roads_error": "",
            "ss2_roads_cache": {},   # new search -> labels refer to different parcels
        })
        with st.spinner(
            f"Searching for open land near {st.session_state['ss2_city']} "
            f"(up to {radius} km)..."
        ):
            candidates, err, used_radius = find_candidates(city_lat, city_lon, radius, req_area)
        st.session_state.update({
            "ss2_search_done":  True,
            "ss2_candidates":   candidates,
            "ss2_search_error": err,
            "ss2_used_radius":  used_radius,
        })
        st.rerun()

    if not st.session_state["ss2_search_done"]:
        st.plotly_chart(
            _search_radius_fig(city_lat, city_lon, radius),
            use_container_width=True,
        )
        return

    # ── Search error ──────────────────────────────────────────────────────────
    if st.session_state["ss2_search_error"]:
        st.error(st.session_state["ss2_search_error"])
        if st.button("Retry search", key="btn_ss2_retry"):
            st.session_state["ss2_search_done"] = False
            st.rerun()
        return

    candidates: list[dict] = st.session_state["ss2_candidates"]
    used_radius: int        = st.session_state.get("ss2_used_radius", radius)

    if not candidates:
        st.warning(
            f"No qualifying open-land parcels found within {used_radius} km. "
            "Try widening the search radius or check the city name."
        )
        return

    if len(candidates) < _MIN_CANDIDATES:
        st.info(
            f"Only {len(candidates)} qualifying parcel(s) found within {used_radius} km. "
            "Consider widening the search radius for more options."
        )

    fits_count = sum(1 for c in candidates if c.get("fits_population", True))
    pop_display = round(req_area / 45.0)
    if fits_count == 0:
        st.warning(
            f"**No candidate site within {used_radius} km can comfortably fit "
            f"{pop_display:,} people** after applying the 35 m safety margin and "
            f"accounting for site shape. Consider widening the search radius or "
            f"reducing the population."
        )

    st.subheader(
        f"{len(candidates)} candidate site(s) found within **{used_radius} km** of city centre"
    )

    # Overview map — zoomed to focused candidate if one is active
    focused = st.session_state.get("ss2_focused")
    st.plotly_chart(
        _candidates_fig(candidates, city_lat, city_lon, focused_label=focused),
        use_container_width=True,
    )

    # ── Candidate cards ───────────────────────────────────────────────────────
    for c in candidates:
        is_selected = (st.session_state["ss2_selected"] == c["label"])
        is_focused  = (st.session_state.get("ss2_focused") == c["label"])
        fits        = c.get("fits_population", True)

        with st.container(border=True):
            h_col, show_col, sel_col = st.columns([3, 1.5, 1.5])
            h_col.markdown(
                f"**Site {c['label']}** · {c['land_label']} · "
                f"{c['area_ha']:.1f} ha · "
                f"{c['city_dist_m'] / 1_000:.1f} km from city centre"
            )

            show_clicked = show_col.button(
                "Unzoom" if is_focused else "Show on map",
                key=f"btn_show_{c['label']}",
                use_container_width=True,
                type="primary" if is_focused else "secondary",
            )

            if fits:
                select_clicked = sel_col.button(
                    "Selected ✓" if is_selected else "Select site",
                    key=f"btn_sel_{c['label']}",
                    use_container_width=True,
                    type="primary" if is_selected else "secondary",
                )
            else:
                sel_col.markdown(
                    f"<div style='color:#b71c1c;font-size:0.82em;text-align:center;"
                    f"padding:0.55em 0.3em;line-height:1.3'>"
                    f"Too small<br/>for {pop_display:,} people</div>",
                    unsafe_allow_html=True,
                )
                select_clicked = False

            pros, cons = _pros_cons(c)
            p_col, n_col = st.columns(2)
            with p_col:
                if pros:
                    st.markdown("**Pros**")
                    for pro in pros:
                        st.markdown(f"+ {pro}")
            with n_col:
                if cons:
                    st.markdown("**Cons**")
                    for con in cons:
                        st.markdown(f"- {con}")

            st.caption(f"Note: {_DISCLAIMER}")

        if show_clicked:
            st.session_state["ss2_focused"] = None if is_focused else c["label"]
            st.rerun()

        if select_clicked and not is_selected and fits:
            # Seed from the per-parcel cache rather than blanking to [] --
            # if this parcel's roads were already fetched successfully earlier
            # this session, a fresh fetch that then fails (e.g. a transient
            # Overpass 504) must not destroy that data. See the fetch block
            # below, which only overwrites on a successful result.
            cached = st.session_state["ss2_roads_cache"].get(c["label"], [])
            st.session_state.update({
                "ss2_selected":    c["label"],
                "ss2_roads_done":  False,
                "ss2_roads_m":     cached,
                "ss2_roads_error": "",
            })
            st.rerun()

    # ── Selected parcel detail ────────────────────────────────────────────────
    sel_label = st.session_state["ss2_selected"]
    if sel_label is None:
        return

    sel = next((c for c in candidates if c["label"] == sel_label), None)
    if sel is None:
        return

    st.divider()
    st.subheader(f"Site {sel_label} — detailed view")

    bb = sel["bbox"]
    origin_lat, origin_lon = bb[0], bb[1]
    width_m  = abs(latlon_to_metres(bb[0], bb[3], origin_lat, origin_lon)[0])
    length_m = abs(latlon_to_metres(bb[2], bb[1], origin_lat, origin_lon)[1])

    if not st.session_state["ss2_roads_done"]:
        with st.spinner("Detecting roads within selected site area..."):
            roads_m, roads_err = _fetch_parcel_roads(
                bb[0], bb[1], bb[2], bb[3], origin_lat, origin_lon
            )
        if roads_err:
            # Failed even after _fetch_parcel_roads' internal retries --
            # leave ss2_roads_m exactly as the selection step seeded it
            # (cached previous success, or [] if there's never been one)
            # rather than overwriting good data with this failure's [].
            st.session_state["ss2_roads_error"] = roads_err
        else:
            # A real result, even a confirmed-empty one -- becomes the new
            # cache entry for this parcel.
            st.session_state["ss2_roads_cache"][sel_label] = roads_m
            st.session_state.update({"ss2_roads_m": roads_m, "ss2_roads_error": ""})
        st.session_state["ss2_roads_done"] = True
        st.rerun()

    roads_m   = st.session_state["ss2_roads_m"]
    roads_err = st.session_state["ss2_roads_error"]

    if roads_err and roads_m:
        st.warning(
            f"Road detection issue: {roads_err} — showing {len(roads_m)} road "
            f"segment(s) from an earlier successful fetch for this site."
        )
    elif roads_err:
        st.warning(f"Road detection issue: {roads_err} — no road data available "
                   f"for this site. Try selecting it again to retry.")
    elif roads_m:
        st.success(f"{len(roads_m)} road segment(s) detected within site area.")
    else:
        st.info("No existing roads detected within the site boundary.")

    st.plotly_chart(_detail_fig(sel, origin_lat, origin_lon, roads_m), use_container_width=True)

    # Single-parcel polygon in local metre coordinates from SW corner of bbox
    parcel_polygon_m = [
        latlon_to_metres(la, lo, origin_lat, origin_lon)
        for la, lo in sel["nodes_latlon"]
    ]

    st.session_state["site"] = {
        "origin_lat":       origin_lat,
        "origin_lon":       origin_lon,
        "width_m":          float(width_m),
        "length_m":         float(length_m),
        "centre_lat":       sel["centroid_lat"],
        "centre_lon":       sel["centroid_lon"],
        "parcel_polygon_m": parcel_polygon_m,
        "roads_m":          roads_m,
        "roads_fetch_error": roads_err,
    }

    st.divider()
    est_cap  = sel.get("est_capacity_pp", 0)
    est_comm = sel.get("est_communities", 0)
    pop_conf = inputs.get("population", 0)
    if est_cap > 0:
        buffer_pct = round((est_cap / pop_conf - 1) * 100) if pop_conf > 0 else 0
        st.info(
            f"**Estimated capacity: ~{est_cap:,} people** "
            f"({est_comm} community slots after 35 m margin, {buffer_pct:+d}% vs required). "
            f"Generation will confirm the exact fit."
        )
    if st.button(
        "Confirm site", type="primary",
        use_container_width=True, key="btn_ss2_confirm",
    ):
        st.session_state["stage"] = "summary"
        st.rerun()
