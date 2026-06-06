"""
Stage 2 – site selection.

Queries OpenStreetMap Overpass API for real open-land parcels near a city,
ranks them against humanitarian site-selection criteria, and presents
interactive candidate cards with map-derived pros/cons.

Projection helpers metres_to_latlon / latlon_to_metres are imported from
src.geocoding and re-exported from this module as required by the spec.
"""
from __future__ import annotations

from math import cos, radians, sqrt

import requests
import streamlit as st
import plotly.graph_objects as go

from src.geocoding import geocode_city, metres_to_latlon, latlon_to_metres  # noqa: F401

# ── Public re-exports (spec requirement) ──────────────────────────────────────
# metres_to_latlon(x_m, y_m, origin_lat, origin_lon) -> (lat, lon)
# latlon_to_metres(lat, lon, origin_lat, origin_lon) -> (x_m, y_m)
__all__ = ["metres_to_latlon", "latlon_to_metres", "find_candidates", "render_location_stage"]

# ── Constants ─────────────────────────────────────────────────────────────────
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_DEFAULT_RADIUS_KM = 10
_MAX_CANDIDATES = 5

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

# (border colour, fill RGBA)
_PALETTE = [
    ("#e63946", "rgba(230,57,70,0.18)"),
    ("#2a9d8f", "rgba(42,157,143,0.18)"),
    ("#e9c46a", "rgba(233,196,106,0.28)"),
    ("#f4a261", "rgba(244,162,97,0.22)"),
    ("#264653", "rgba(38,70,83,0.22)"),
]


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box centred at (lat, lon) with half-side radius_km."""
    dlat = radius_km * 1_000 / 111_320
    dlon = radius_km * 1_000 / (111_320 * cos(radians(lat)))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def _poly_area_m2(latlons: list[tuple[float, float]], ref_lat: float, ref_lon: float) -> float:
    """Shoelace area in m² after projecting polygon nodes to local metres."""
    if len(latlons) < 3:
        return 0.0
    pts = [latlon_to_metres(la, lo, ref_lat, ref_lon) for la, lo in latlons]
    n = len(pts)
    a = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )
    return abs(a) / 2.0


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
    """Equirectangular distance in metres."""
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * cos(radians((lat1 + lat2) / 2))
    return sqrt(dx * dx + dy * dy)


# ── Overpass API ──────────────────────────────────────────────────────────────

def _overpass(query: str, timeout_s: int = 65) -> tuple[dict | None, str]:
    """POST a query; return (parsed_json, error_message). Never raises."""
    headers = {"User-Agent": "refugee-camp-planner/1.0", "Accept": "application/json"}
    try:
        resp = requests.post(
            _OVERPASS_URL, data={"data": query},
            headers=headers, timeout=timeout_s + 10,
        )
        resp.raise_for_status()
        return resp.json(), ""
    except requests.Timeout:
        return None, "Overpass API timed out. Try a smaller search radius or retry."
    except requests.RequestException as exc:
        return None, f"Network error contacting Overpass: {exc}"
    except Exception as exc:
        return None, f"Unexpected error: {exc}"


def _land_water_query(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> str:
    """
    Land parcels + water features with inline geometry (out geom).
    Capped at 1 000 elements to avoid gateway timeouts over dense areas.
    """
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


def _roads_near_query(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> str:
    """Key road types only, for road-proximity scores. Inline geometry, capped."""
    bb = f"{min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f}"
    return (
        "[out:json][timeout:30];\n"
        f'way[highway~"^(primary|secondary|tertiary|residential|unclassified|track|service|road)$"]({bb});\n'
        "out geom 500;"
    )


def _road_query(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> str:
    """All highway ways inside a parcel bounding box, with full node resolution."""
    bb = f"{min_lat:.6f},{min_lon:.6f},{max_lat:.6f},{max_lon:.6f}"
    return f"[out:json][timeout:25];\nway[highway]({bb});\n(._;>;);\nout body;"


# ── Element classification ────────────────────────────────────────────────────

def _classify(tags: dict) -> tuple[str, str]:
    """Return (category, sub_type) for a way's tags."""
    lu  = tags.get("landuse", "")
    nat = tags.get("natural", "")
    if lu in ("farmland", "meadow", "grass", "brownfield"):
        return "land", lu
    if lu == "industrial" and tags.get("disused") == "yes":
        return "land", "disused_industrial"
    if nat == "grassland":
        return "land", "grassland"
    if "highway" in tags:
        return "road", tags["highway"]
    if nat in ("water", "wetland") or "waterway" in tags:
        return "water", nat or tags.get("waterway", "")
    return "other", ""


# ── Core parcel search ────────────────────────────────────────────────────────

def find_candidates(
    city_lat: float,
    city_lon: float,
    radius_km: float,
    required_area_m2: float,
) -> tuple[list[dict], str]:
    """
    Query OSM for open-land parcels within radius_km of (city_lat, city_lon).
    Filter to area >= required_area_m2, compute map-derived metrics, rank, and
    return the top _MAX_CANDIDATES.  Returns (candidates, error_message).
    """
    min_lat, min_lon, max_lat, max_lon = _bbox(city_lat, city_lon, radius_km)

    # Two separate queries keep individual response sizes manageable
    land_data, err = _overpass(_land_water_query(min_lat, min_lon, max_lat, max_lon), timeout_s=65)
    if err:
        return [], err
    road_data, road_err = _overpass(_roads_near_query(min_lat, min_lon, max_lat, max_lon), timeout_s=35)
    # Road query failure is non-fatal: road distances become 99 999 m
    road_elements = road_data.get("elements", []) if road_data else []

    # Both land_data and road_data use "out geom" — geometry is inline per element.
    def _geom_latlons(el: dict) -> list[tuple[float, float]]:
        """Extract (lat, lon) list from an element's inline geometry array."""
        return [(g["lat"], g["lon"]) for g in el.get("geometry", [])]

    land_ways: list[dict] = []
    road_nodes: list[tuple[float, float]] = []
    water_bboxes: list[tuple[float, float, float, float]] = []

    for el in land_data.get("elements", []):
        if el["type"] != "way":
            continue
        tags = el.get("tags", {})
        cat, sub = _classify(tags)
        latlons = _geom_latlons(el)
        if len(latlons) < 3:
            continue
        if cat == "land":
            land_ways.append({"latlons": latlons, "sub": sub, "id": el["id"]})
        elif cat == "water":
            water_bboxes.append(_poly_bbox(latlons))

    for el in road_elements:
        if el["type"] == "way":
            road_nodes.extend(_geom_latlons(el))

    candidates: list[dict] = []
    for way in land_ways:
        latlons = way["latlons"]
        c_lat, c_lon = _centroid(latlons)
        area = _poly_area_m2(latlons, c_lat, c_lon)
        if area < required_area_m2:
            continue

        road_dist = (
            min(_dist_m(c_lat, c_lon, rlat, rlon) for rlat, rlon in road_nodes)
            if road_nodes else 99_999.0
        )
        city_dist = _dist_m(c_lat, c_lon, city_lat, city_lon)
        p_bbox = _poly_bbox(latlons)
        has_water = any(_bbox_overlaps(p_bbox, wb) for wb in water_bboxes)
        sub = way["sub"]

        candidates.append({
            "osm_id":           way["id"],
            "nodes_latlon":     latlons,
            "centroid_lat":     c_lat,
            "centroid_lon":     c_lon,
            "area_m2":          area,
            "area_ha":          area / 10_000,
            "land_type":        sub,
            "land_label":       _LAND_TYPES.get(sub, sub.replace("_", " ").title()),
            "road_dist_m":      road_dist,
            "city_dist_m":      city_dist,
            "has_water":        has_water,
            "required_area_m2": required_area_m2,
            "margin_pct":       (area / required_area_m2 - 1.0) * 100,
            "bbox":             p_bbox,
        })

    def _score(c: dict) -> float:
        s = 0.0
        if not c["has_water"]:
            s += 100.0
        if c["land_type"] in _OPEN_LAND:
            s += 50.0
        # Prefer close to a road, up to 40 pts
        s += max(0.0, 40.0 - c["road_dist_m"] / 100.0)
        # Prefer large area margin, up to 30 pts
        s += min(30.0, c["margin_pct"] / 10.0)
        # Prefer sensible distance from city (1–8 km)
        city_km = c["city_dist_m"] / 1_000.0
        if 1.0 < city_km < 8.0:
            s += 20.0
        return s

    candidates.sort(key=_score, reverse=True)
    top = candidates[:_MAX_CANDIDATES]
    for i, c in enumerate(top):
        c["label"] = _LABELS[i]
    return top, ""


def _fetch_parcel_roads(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
    origin_lat: float, origin_lon: float,
) -> tuple[list[list[tuple[float, float]]], str]:
    """Roads inside a parcel bounding box, converted to local metres."""
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
    pros: list[str] = []
    cons: list[str] = []

    m = c["margin_pct"]
    if m >= 50:
        pros.append(f"{c['area_ha']:.1f} ha — well above the {req_ha:.1f} ha needed ({m:.0f}% margin)")
    elif m >= 10:
        pros.append(f"{c['area_ha']:.1f} ha — above the {req_ha:.1f} ha needed ({m:.0f}% margin)")
    else:
        cons.append(f"Only {m:.0f}% above the minimum area — tight fit")

    rd = c["road_dist_m"]
    if rd < 100:
        pros.append(f"Adjacent to an existing road (≈{rd:.0f} m)")
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
        cons.append("Overlaps or adjoins mapped water / wetland — flood risk assessment required")
    else:
        pros.append("No mapped water or wetland detected on site")

    if c["land_type"] in _OPEN_LAND:
        pros.append(f"Open land: {c['land_label'].lower()}")
    else:
        cons.append(f"Land type '{c['land_label']}' may require clearance or remediation")

    return pros, cons


# ── Plotly figures ────────────────────────────────────────────────────────────

def _candidates_fig(candidates: list[dict], city_lat: float, city_lon: float) -> go.Figure:
    traces: list = [go.Scattermapbox(
        lat=[city_lat], lon=[city_lon],
        mode="markers+text",
        marker=dict(size=10, color="black"),
        text=["City centre"], textposition="top right",
        name="City centre",
    )]
    for i, c in enumerate(candidates):
        line_col, fill_col = _PALETTE[i % len(_PALETTE)]
        lats = [p[0] for p in c["nodes_latlon"]] + [c["nodes_latlon"][0][0]]
        lons = [p[1] for p in c["nodes_latlon"]] + [c["nodes_latlon"][0][1]]
        traces.append(go.Scattermapbox(
            lat=lats, lon=lons,
            mode="lines",
            fill="toself", fillcolor=fill_col,
            line=dict(color=line_col, width=2),
            name=f"Site {c['label']}",
        ))
        traces.append(go.Scattermapbox(
            lat=[c["centroid_lat"]], lon=[c["centroid_lon"]],
            mode="markers+text",
            marker=dict(size=7, color=line_col),
            text=[f"  {c['label']}"], textposition="middle right",
            showlegend=False,
        ))

    all_lats = [city_lat] + [p[0] for c in candidates for p in c["nodes_latlon"]]
    all_lons = [city_lon] + [p[1] for c in candidates for p in c["nodes_latlon"]]
    mid_lat = (min(all_lats) + max(all_lats)) / 2
    mid_lon = (min(all_lons) + max(all_lons)) / 2
    span_km = max(
        _dist_m(min(all_lats), mid_lon, max(all_lats), mid_lon),
        _dist_m(mid_lat, min(all_lons), mid_lat, max(all_lons)),
    ) / 1_000
    zoom = 12 if span_km < 5 else 11 if span_km < 12 else 10 if span_km < 25 else 9

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=mid_lat, lon=mid_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0),
        height=500,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)"),
    )
    return fig


def _detail_fig(c: dict, origin_lat: float, origin_lon: float, roads_m: list) -> go.Figure:
    lats = [p[0] for p in c["nodes_latlon"]] + [c["nodes_latlon"][0][0]]
    lons = [p[1] for p in c["nodes_latlon"]] + [c["nodes_latlon"][0][1]]
    traces: list = [go.Scattermapbox(
        lat=lats, lon=lons,
        mode="lines",
        fill="toself", fillcolor="rgba(230,57,70,0.15)",
        line=dict(color="#e63946", width=3),
        name=f"Site {c['label']}",
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
            lat=[None], lon=[None],
            mode="lines",
            line=dict(color="#457b9d", width=2),
            name=f"Existing roads ({len(roads_m)})",
        ))

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=c["centroid_lat"], lon=c["centroid_lon"]),
            zoom=14,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=480,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)"),
    )
    return fig


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state(inputs: dict) -> None:
    """Seed ss2_* keys once per Stage-2 visit; geocodes the city from Stage 1."""
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
        "ss2_radius_km":     _DEFAULT_RADIUS_KM,
        "ss2_search_done":   False,
        "ss2_candidates":    [],
        "ss2_search_error":  "",
        "ss2_selected":      None,
        "ss2_roads_done":    False,
        "ss2_roads_m":       [],
        "ss2_roads_error":   "",
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
        if st.button("← Back to questions", key="btn_ss2_back"):
            st.session_state["stage"] = "input"
            st.rerun()
        st.divider()

    # Guard: required area must be known
    req_area = inputs.get("required_area_m2") or 0
    if req_area <= 0:
        st.warning(
            "Population has not been entered yet — site area cannot be computed. "
            "Please return to Stage 1 and enter the headcount via the Set button."
        )
        if st.button("← Back to questions", key="btn_ss2_nopop"):
            st.session_state["stage"] = "input"
            st.rerun()
        return

    # Initialise state (geocodes city; show spinner on first entry)
    if "ss2_init" not in st.session_state:
        with st.spinner(f"Locating {inputs.get('city', '')}…"):
            _init_state(inputs)
        st.rerun()

    # ── Geocoding error / search box ──────────────────────────────────────────
    if st.session_state["ss2_geocode_error"]:
        st.error(
            f"Could not geocode **{st.session_state['ss2_geocode_error']}**. "
            "Try a more specific name below."
        )

    with st.expander(
        "Search / change location",
        expanded=not st.session_state["ss2_geocoded"],
    ):
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
                    "ss2_roads_done":    False,
                    "ss2_roads_m":       [],
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
        f"City centre · {city_lat:.5f} N, {city_lon:.5f} E  |  "
        f"Required site area: **{req_ha:.1f} ha** (population × 45 m²)"
    )

    # ── Search controls ───────────────────────────────────────────────────────
    r_col, btn_col = st.columns([3, 1])
    # key stores value in session_state["ss2_radius_km"]; initialised in _init_state
    r_col.slider(
        "Search radius (km)", min_value=3, max_value=30, step=1,
        key="ss2_radius_km",
    )
    radius = st.session_state["ss2_radius_km"]

    search_clicked = btn_col.button(
        "Find candidate sites", type="primary",
        use_container_width=True, key="btn_ss2_search",
    )

    if search_clicked:
        st.session_state.update({
            "ss2_selected": None, "ss2_roads_done": False,
            "ss2_roads_m": [], "ss2_roads_error": "",
        })
        with st.spinner(
            f"Querying OpenStreetMap for open land within {radius} km of "
            f"{st.session_state['ss2_city']}…"
        ):
            candidates, err = find_candidates(city_lat, city_lon, radius, req_area)
        st.session_state.update({
            "ss2_search_done":  True,
            "ss2_candidates":   candidates,
            "ss2_search_error": err,
        })
        st.rerun()

    if not st.session_state["ss2_search_done"]:
        return

    # ── Search error ──────────────────────────────────────────────────────────
    if st.session_state["ss2_search_error"]:
        st.error(st.session_state["ss2_search_error"])
        if st.button("Retry search", key="btn_ss2_retry"):
            st.session_state["ss2_search_done"] = False
            st.rerun()
        return

    candidates: list[dict] = st.session_state["ss2_candidates"]

    if not candidates:
        st.warning(
            f"No open-land parcels of at least {req_ha:.1f} ha were found within {radius} km. "
            "Try a larger search radius."
        )
        return

    if len(candidates) < 3:
        st.info(
            f"Only {len(candidates)} qualifying parcel(s) found within {radius} km. "
            "Consider widening the search radius for more options."
        )

    st.subheader(f"{len(candidates)} candidate site(s) found — {radius} km radius")
    st.plotly_chart(_candidates_fig(candidates, city_lat, city_lon), use_container_width=True)

    # ── Candidate cards ───────────────────────────────────────────────────────
    for c in candidates:
        is_selected = st.session_state["ss2_selected"] == c["label"]
        with st.container(border=True):
            h_col, b_col = st.columns([5, 1])
            h_col.markdown(
                f"**Site {c['label']}** · {c['land_label']} · "
                f"{c['area_ha']:.1f} ha · "
                f"{c['city_dist_m'] / 1_000:.1f} km from city centre"
            )
            clicked = b_col.button(
                "✓ Selected" if is_selected else "Select this site",
                key=f"btn_sel_{c['label']}",
                use_container_width=True,
                type="primary" if is_selected else "secondary",
            )

            pros, cons = _pros_cons(c)
            p_col, n_col = st.columns(2)
            with p_col:
                if pros:
                    st.markdown("**Pros**")
                    for p in pros:
                        st.markdown(f"✓ {p}")
            with n_col:
                if cons:
                    st.markdown("**Cons**")
                    for con in cons:
                        st.markdown(f"✗ {con}")

            st.caption(f"⚠ {_DISCLAIMER}")

            if clicked and not is_selected:
                st.session_state.update({
                    "ss2_selected":  c["label"],
                    "ss2_roads_done": False,
                    "ss2_roads_m":   [],
                    "ss2_roads_error": "",
                })
                st.rerun()

    # ── Selected site detail ──────────────────────────────────────────────────
    sel_label: str | None = st.session_state["ss2_selected"]
    if sel_label is None:
        return

    sel = next((c for c in candidates if c["label"] == sel_label), None)
    if sel is None:
        return

    st.divider()
    st.subheader(f"Site {sel_label} — detailed view")

    bb = sel["bbox"]                                  # (min_lat, min_lon, max_lat, max_lon)
    origin_lat, origin_lon = bb[0], bb[1]             # SW corner = metre origin
    # E-W width: x-offset of SE corner (same lat, max lon)
    width_m  = abs(latlon_to_metres(bb[0], bb[3], origin_lat, origin_lon)[0])
    # N-S length: y-offset of NW corner (max lat, same lon)
    length_m = abs(latlon_to_metres(bb[2], bb[1], origin_lat, origin_lon)[1])

    # Road detection: runs once per selection, cached in session state
    if not st.session_state["ss2_roads_done"]:
        with st.spinner("Detecting existing roads inside the selected site…"):
            roads_m, roads_err = _fetch_parcel_roads(
                bb[0], bb[1], bb[2], bb[3], origin_lat, origin_lon
            )
        st.session_state.update({
            "ss2_roads_done":  True,
            "ss2_roads_m":     roads_m,
            "ss2_roads_error": roads_err,
        })
        st.rerun()

    roads_m   = st.session_state["ss2_roads_m"]
    roads_err = st.session_state["ss2_roads_error"]

    if roads_err:
        st.warning(f"Road detection issue: {roads_err}")
    if roads_m:
        st.success(f"✓ {len(roads_m)} road segment(s) detected within site boundary.")
    else:
        st.info("No existing roads detected within the site boundary (open land or unmapped area).")

    st.plotly_chart(_detail_fig(sel, origin_lat, origin_lon, roads_m), use_container_width=True)

    # Parcel polygon converted to metres relative to SW corner
    parcel_polygon_m = [
        latlon_to_metres(la, lo, origin_lat, origin_lon)
        for la, lo in sel["nodes_latlon"]
    ]

    # Keep site dict current so Confirm always reflects latest data
    st.session_state["site"] = {
        "origin_lat":       origin_lat,
        "origin_lon":       origin_lon,
        "width_m":          float(width_m),
        "length_m":         float(length_m),
        "centre_lat":       sel["centroid_lat"],
        "centre_lon":       sel["centroid_lon"],
        "parcel_polygon_m": parcel_polygon_m,
        "roads_m":          roads_m,
    }

    st.divider()
    if st.button(
        "Confirm site →", type="primary",
        use_container_width=True, key="btn_ss2_confirm",
    ):
        st.session_state["stage"] = "layout"
        st.rerun()
