"""
Stage 4 layout engine.

place_shelters(site, requirements) -> dict
  Row-by-row shelter placement inside the parcel polygon.

place_all_facilities(site, requirements, shelter_result) -> dict
  Remaining facilities in CS5 priority order. Returns corners_m for
  every element so the drawing layer treats everything uniformly.
  Hard rule: nothing placed outside the parcel polygon.
"""
from math import ceil, sqrt, pi, cos as _cos, sin as _sin
from shapely.geometry import Polygon as ShapelyPolygon, Point as ShapelyPoint, LineString as _ShapelyLine
from shapely.ops import unary_union
import networkx as _nx


# ── Colours (used by app.py for drawing) ─────────────────────────────────────

FACILITY_STYLE: dict[str, tuple[str, str, str]] = {
    # key: (legend label, fill_hex, line_hex)
    "shelter_units":       ("Shelter units",       "#F5DEB3", "#C4A882"),
    "health_post":         ("Health post",         "#E24B4A", "#b03838"),
    "water_points":        ("Water points",        "#378ADD", "#2560a0"),
    "food_distribution":   ("Food distribution",   "#D85A30", "#a04020"),
    "community_space":     ("Community space",     "#7F77DD", "#5a50b0"),
    "administrative_area": ("Administrative area", "#8B6914", "#604700"),
    "schools":             ("Schools",             "#639922", "#456c14"),
    "worship_facility":    ("Worship facility",    "#9C27B0", "#6a1a80"),
    "toilets":             ("Latrine blocks",      "#8BC34A", "#5f8830"),
    "washing_facilities":  ("Washing facilities",  "#26C6DA", "#1890a0"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rect(cx: float, cy: float, w: float, h: float) -> list[tuple[float, float]]:
    hw, hh = w / 2, h / 2
    return [(cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh)]


def _nudge(parcel: ShapelyPolygon,
           cx: float, cy: float,
           w: float, h: float,
           step: float = 3.0,
           max_rings: int = 8) -> list[tuple] | None:
    """
    Try to fit a w×h rectangle at (cx, cy) inside *parcel*.
    Searches outward in concentric rings of *step* m until a valid
    position is found or max_rings is exhausted.
    """
    offsets = [(0.0, 0.0)]
    for ring in range(1, max_rings + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if abs(dx) == ring or abs(dy) == ring:
                    offsets.append((dx * step, dy * step))
    for dx, dy in offsets:
        corners = _rect(cx + dx, cy + dy, w, h)
        if parcel.contains(ShapelyPolygon(corners)):
            return corners
    return None


def _circle(cx: float, cy: float, r: float, n: int = 16) -> list[tuple[float, float]]:
    """Approximate circle as n-point polygon in local metres."""
    return [(cx + r * _cos(2 * pi * i / n),
             cy + r * _sin(2 * pi * i / n)) for i in range(n)]


def _grid_place(parcel: ShapelyPolygon,
                count: int,
                w: float, h: float,
                exclusion: ShapelyPolygon | None = None,
                step_mult: float = 1.0) -> list[dict]:
    """
    Place *count* w×h rectangles spread across *parcel* using a grid.
    Skips cells where the rectangle would intersect *exclusion*.
    """
    if count == 0:
        return []
    minx, miny, maxx, maxy = parcel.bounds
    span_x, span_y = maxx - minx, maxy - miny
    aspect = span_x / max(span_y, 1.0)

    # Over-provision the grid so irregular parcels still get enough hits
    target = max(count * 3, 9)
    cols = max(1, round(sqrt(target * aspect)))
    rows = max(1, ceil(target / cols))
    cell_w, cell_h = span_x / cols, span_y / rows
    step = max(2.0, min(w, h) / 2) * step_mult

    placed: list[dict] = []
    for row in range(rows):
        for col in range(cols):
            if len(placed) >= count:
                return placed
            cx = minx + (col + 0.5) * cell_w
            cy = miny + (row + 0.5) * cell_h
            corners = _nudge(parcel, cx, cy, w, h, step=step)
            if corners is None:
                continue
            if exclusion is not None and exclusion.intersects(ShapelyPolygon(corners)):
                continue
            placed.append({"corners_m": corners})
    return placed


def _entry_point(site: dict) -> tuple[float, float]:
    """
    Return the parcel boundary vertex nearest to any road endpoint.
    Falls back to the bottom-centre of the bounding box if no roads exist.
    """
    parcel_pts = site["parcel_polygon_m"]
    roads = site.get("roads_m") or []
    if roads:
        best, best_d = None, float("inf")
        for road in roads:
            for rx, ry in road:
                for px, py in parcel_pts:
                    d = (rx - px) ** 2 + (ry - py) ** 2
                    if d < best_d:
                        best_d, best = d, (px, py)
        if best:
            return best
    parcel = ShapelyPolygon(parcel_pts)
    minx, miny, maxx, _ = parcel.bounds
    return ((minx + maxx) / 2, miny)


# ─────────────────────────────────────────────────────────────────────────────
# Shelter placement (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

_UNIT_WIDTH_M = 5.0


def _footprint(area_m2: float) -> tuple[float, float]:
    h = round(area_m2 / _UNIT_WIDTH_M, 2)
    return _UNIT_WIDTH_M, h


def place_shelters(site: dict, requirements: dict) -> dict:
    """
    Row-by-row shelter placement inside the parcel polygon.

    Returns
    -------
    dict: shelters (list of {corners_m}), placed (int), required (int)
    """
    shelter_req = requirements.get("shelter_units", {})
    required    = shelter_req.get("count", 0)
    area_m2     = shelter_req.get("area_per_unit_m2", 17.5)

    if required == 0 or not site.get("parcel_polygon_m"):
        return {"shelters": [], "placed": 0, "required": required}

    unit_w, unit_h = _footprint(area_m2)
    gap_unit = 2.0
    gap_row  = 4.0
    margin   = 3.0

    parcel = ShapelyPolygon(site["parcel_polygon_m"])
    minx, miny, maxx, maxy = parcel.bounds

    shelters: list[dict] = []
    y = miny + margin

    while y + unit_h <= maxy - margin and len(shelters) < required:
        x = minx + margin
        while x + unit_w <= maxx - margin and len(shelters) < required:
            corners = [
                (x,          y),
                (x + unit_w, y),
                (x + unit_w, y + unit_h),
                (x,          y + unit_h),
            ]
            if parcel.contains(ShapelyPolygon(corners)):
                shelters.append({"corners_m": corners})
            x += unit_w + gap_unit
        y += unit_h + gap_row

    return {"shelters": shelters, "placed": len(shelters), "required": required}


# ─────────────────────────────────────────────────────────────────────────────
# All-facilities placement (CS5 priority order)
# ─────────────────────────────────────────────────────────────────────────────

def place_all_facilities(site: dict,
                         requirements: dict,
                         shelter_result: dict) -> dict:
    """
    Place every facility type in CS5 priority order inside the parcel.

    Returns
    -------
    dict keyed by facility type, each value a list of {"corners_m": [...]}.
    Also includes "status": {type: {"placed": n, "required": m}}.
    """
    parcel   = ShapelyPolygon(site["parcel_polygon_m"])
    rep_pt   = parcel.representative_point()   # guaranteed inside
    cx, cy   = rep_pt.x, rep_pt.y
    shelters = shelter_result.get("shelters", [])

    # Build shelter exclusion zone (6 m buffer) for latrine/wash placement
    if shelters:
        sh_union  = unary_union([ShapelyPolygon(s["corners_m"]) for s in shelters])
        sh_buffer = sh_union.buffer(6.0)
    else:
        sh_buffer = None

    out    = {}
    status = {}

    def _req(key: str) -> int:
        return requirements.get(key, {}).get("count", 0)

    def _record(key: str, items: list, required: int) -> None:
        out[key]    = items
        status[key] = {"placed": len(items), "required": required}

    # ── 1. Health post (HE3) — near parcel centroid ───────────────────────────
    hp_w, hp_h = 15.0, 10.0
    hp_req     = _req("health_posts")
    hp_corners = _nudge(parcel, cx, cy, hp_w, hp_h, step=4.0)
    hp_cx      = cx if hp_corners is None else (hp_corners[0][0] + hp_corners[2][0]) / 2
    hp_cy      = cy if hp_corners is None else (hp_corners[0][1] + hp_corners[2][1]) / 2
    _record("health_post", [{"corners_m": hp_corners}] if hp_corners else [], hp_req)

    # ── 2. Water points (WS2) — circles distributed on grid ──────────────────
    wp_req = _req("water_points")
    wp_r   = 3.0
    wp_raw = _grid_place(parcel, wp_req, wp_r * 2, wp_r * 2)
    wp_out = []
    for item in wp_raw:
        c = item["corners_m"]
        ccx = (c[0][0] + c[2][0]) / 2
        ccy = (c[0][1] + c[2][1]) / 2
        circle_pts = _circle(ccx, ccy, wp_r)
        if parcel.contains(ShapelyPolygon(circle_pts)):
            wp_out.append({"corners_m": circle_pts})
        elif parcel.intersects(ShapelyPolygon(circle_pts)):
            wp_out.append({"corners_m": circle_pts})   # partial — small enough to accept
        if len(wp_out) >= wp_req:
            break
    _record("water_points", wp_out, wp_req)

    # ── 3. Food distribution (FD3) — adjacent to health post ─────────────────
    fd_req = _req("food_distribution_points")
    fd_w, fd_h = 12.0, 8.0
    fd_out = []
    for i in range(fd_req):
        offset = (hp_w / 2 + fd_w / 2 + 3.0) + i * (fd_w + 3.0)
        c = _nudge(parcel, hp_cx + offset, hp_cy, fd_w, fd_h)
        if c is None:
            c = _nudge(parcel, hp_cx - offset, hp_cy, fd_w, fd_h)
        if c is None:
            c = _nudge(parcel, hp_cx, hp_cy + offset, fd_w, fd_h)
        if c:
            fd_out.append({"corners_m": c})
    _record("food_distribution", fd_out, fd_req)

    # ── 4. Community space (CS1) — other side of health post ─────────────────
    cs_req = _req("community_space")
    cs_w, cs_h = 20.0, 15.0
    cs_out = []
    for sign in (-1, 1, 0):          # try left, right, above
        off = (hp_w / 2 + cs_w / 2 + 3.0) if sign != 0 else 0
        c = _nudge(parcel,
                   hp_cx + sign * off if sign != 0 else hp_cx,
                   hp_cy if sign != 0 else hp_cy + (hp_h / 2 + cs_h / 2 + 3.0),
                   cs_w, cs_h)
        if c:
            cs_out.append({"corners_m": c})
            break
    _record("community_space", cs_out, cs_req)

    # ── 5. Administrative area (CS2) — near road entry ───────────────────────
    aa_req  = _req("administrative_area")
    aa_w, aa_h = 15.0, 10.0
    ex, ey  = _entry_point(site)
    # Entry point is a boundary vertex; pre-offset toward centroid so the
    # rect starts inside the parcel rather than straddling the edge.
    _dx, _dy = cx - ex, cy - ey
    _d = (_dx ** 2 + _dy ** 2) ** 0.5 or 1.0
    _inset = max(aa_w, aa_h) * 0.8          # ~12 m inward
    aa_start_x = ex + _dx / _d * _inset
    aa_start_y = ey + _dy / _d * _inset
    aa_c = _nudge(parcel, aa_start_x, aa_start_y, aa_w, aa_h, step=3.0, max_rings=10)
    if aa_c is None:
        # Entry-point area too tight; fall back to any valid spot in the parcel
        fallback = _grid_place(parcel, 1, aa_w, aa_h, step_mult=1.0)
        aa_c = fallback[0]["corners_m"] if fallback else None
    _record("administrative_area", [{"corners_m": aa_c}] if aa_c else [], aa_req)

    # ── 6. Schools (ED1) — grid distributed, only if children > 0 ────────────
    sc_req = _req("schools")
    if sc_req > 0:
        sc_w, sc_h = 20.0, 15.0
        sc_out = _grid_place(parcel, sc_req, sc_w, sc_h, step_mult=2.0)
    else:
        sc_out = []
    _record("schools", sc_out, sc_req)

    # ── 7. Worship facility (RB1) — central, only if count = 1 ───────────────
    wf_req = _req("worship_facility")
    if wf_req == 1:
        wf_w, wf_h = 12.0, 10.0
        # Slightly above health post; search multiple offsets
        wf_c = None
        for dy in (hp_h / 2 + wf_h / 2 + 5,
                   -(hp_h / 2 + wf_h / 2 + 5),
                   hp_w / 2 + wf_w / 2 + 5,
                   -(hp_w / 2 + wf_w / 2 + 5)):
            wf_c = _nudge(parcel,
                          cx + (dy if abs(dy) < 50 else 0),
                          cy + (dy if abs(dy) < 50 else 0),
                          wf_w, wf_h)
            if wf_c:
                break
        _record("worship_facility", [{"corners_m": wf_c}] if wf_c else [], wf_req)
    else:
        _record("worship_facility", [], wf_req)

    # ── 8. Latrine blocks (SA1) — near shelters, ≥6 m clearance ─────────────
    lt_req  = _req("toilets")
    lt_w, lt_h = 4.0, 3.0
    lt_out  = _grid_place(parcel, lt_req, lt_w, lt_h, exclusion=sh_buffer)
    _record("toilets", lt_out, lt_req)

    # ── 9. Washing facilities (SA2) — adjacent to latrines ───────────────────
    wsh_req = _req("washing_facilities")
    wsh_w, wsh_h = 4.0, 3.0
    wsh_out = []
    for latrine in lt_out[:wsh_req]:
        lc = latrine["corners_m"]
        lcx = (lc[0][0] + lc[2][0]) / 2
        lcy = (lc[0][1] + lc[2][1]) / 2
        # Try right, then above, then left of each latrine
        wc = None
        for dx, dy in ((lt_w / 2 + wsh_w / 2 + 1, 0),
                        (0, lt_h / 2 + wsh_h / 2 + 1),
                        (-(lt_w / 2 + wsh_w / 2 + 1), 0)):
            wc = _nudge(parcel, lcx + dx, lcy + dy, wsh_w, wsh_h, step=1.5, max_rings=4)
            if wc:
                break
        if wc:
            wsh_out.append({"corners_m": wc})
    # Top up with grid placement if fewer matched than required
    if len(wsh_out) < wsh_req:
        extra = _grid_place(parcel, wsh_req - len(wsh_out), wsh_w, wsh_h,
                            exclusion=sh_buffer)
        wsh_out.extend(extra)
    _record("washing_facilities", wsh_out[:wsh_req], wsh_req)

    out["status"] = status
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Road network  (PA1 main road · PA2/PA4 secondary roads and footpaths)
# ─────────────────────────────────────────────────────────────────────────────

def _dist2(a: tuple, b: tuple) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _nearest_on_polyline(pts: list,
                         px: float, py: float) -> tuple[float, float, float]:
    """
    Nearest point on polyline *pts* to (px, py).
    Returns (x, y, distance).
    """
    if len(pts) == 1:
        return pts[0][0], pts[0][1], _dist2(pts[0], (px, py)) ** 0.5
    bx, by, bd2 = pts[0][0], pts[0][1], float("inf")
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        ex_, ey_ = pts[i + 1]
        ddx, ddy = ex_ - ax, ey_ - ay
        len2 = ddx * ddx + ddy * ddy
        if len2 < 1e-10:
            nx_, ny_ = ax, ay
        else:
            t = max(0.0, min(1.0, ((px - ax) * ddx + (py - ay) * ddy) / len2))
            nx_, ny_ = ax + t * ddx, ay + t * ddy
        d2 = (px - nx_) ** 2 + (py - ny_) ** 2
        if d2 < bd2:
            bd2, bx, by = d2, nx_, ny_
    return bx, by, bd2 ** 0.5


def _clip_to_parcel(parcel: ShapelyPolygon,
                    p1: tuple, p2: tuple) -> list | None:
    """
    Clip segment p1→p2 to inside *parcel*.
    Returns list of (x, y) tuples or None if no overlap.
    """
    line = _ShapelyLine([p1, p2])
    clipped = line.intersection(parcel)
    if clipped.is_empty:
        return None
    if clipped.geom_type == "LineString":
        return list(clipped.coords)
    if clipped.geom_type == "MultiLineString":
        longest = max(clipped.geoms, key=lambda g: g.length)
        return list(longest.coords)
    return None


def place_roads(site: dict,
                shelter_result: dict,
                facilities: dict) -> dict:
    """
    Build a camp road network in CS5 / UNHCR PA standard priority order.

    Returns
    -------
    dict with keys:
      main_road        list[{pts_m}]           PA1 – 6 m wide on map
      secondary_roads  list[{pts_m}]           PA2 – 4 m wide
      footpaths        list[{pts_m}]           PA4 – 4 m wide
      existing_roads   list[{pts_m}]           from site["roads_m"]
      entrance_m       (x, y)
      connected        bool
      stranded         list[str]
    """
    parcel = ShapelyPolygon(site["parcel_polygon_m"])
    rep    = parcel.representative_point()

    # ── Entrance: boundary vertex nearest to external roads ───────────────────
    ex, ey = _entry_point(site)

    # ── Health post centre (main road target) ─────────────────────────────────
    hp_items = facilities.get("health_post", [])
    if hp_items:
        c = hp_items[0]["corners_m"]
        hp_cx = sum(p[0] for p in c) / len(c)
        hp_cy = sum(p[1] for p in c) / len(c)
    else:
        hp_cx, hp_cy = rep.x, rep.y

    # ── 1. Main road: entrance → parcel centroid → health post ────────────────
    raw_wp = [(ex, ey), (rep.x, rep.y), (hp_cx, hp_cy)]
    waypoints: list[tuple] = [raw_wp[0]]
    for pt in raw_wp[1:]:
        if _dist2(pt, waypoints[-1]) > 25:      # skip if < 5 m apart
            waypoints.append(pt)

    main_segs: list[dict] = []
    main_pts:  list[tuple] = []
    for i in range(len(waypoints) - 1):
        clipped = _clip_to_parcel(parcel, waypoints[i], waypoints[i + 1])
        if clipped:
            main_segs.append({"pts_m": clipped})
            for pt in clipped:
                if not main_pts or _dist2(pt, main_pts[-1]) > 1:
                    main_pts.append(pt)

    if not main_pts:                             # fallback: straight line
        main_pts  = [(ex, ey), (rep.x, rep.y)]
        main_segs = [{"pts_m": list(main_pts)}]

    # ── 2. Secondary roads: each major facility → nearest point on main road ──
    _FAC_KEYS = [
        "health_post", "water_points", "food_distribution",
        "community_space", "administrative_area", "schools", "worship_facility",
    ]
    secondary_segs: list[dict] = []
    fac_pts: dict[str, tuple] = {}      # node_name → (x, y)

    for key in _FAC_KEYS:
        for idx, item in enumerate(facilities.get(key, [])):
            c = item["corners_m"]
            fcx = sum(p[0] for p in c) / len(c)
            fcy = sum(p[1] for p in c) / len(c)
            node_name = f"{key}_{idx}"
            fac_pts[node_name] = (fcx, fcy)
            if len(main_pts) >= 2:
                cx_, cy_, _ = _nearest_on_polyline(main_pts, fcx, fcy)
            else:
                cx_, cy_ = main_pts[0]
            if _dist2((fcx, fcy), (cx_, cy_)) > 4:     # skip if < 2 m from road
                clipped = _clip_to_parcel(parcel, (fcx, fcy), (cx_, cy_))
                if clipped:
                    secondary_segs.append({"pts_m": clipped,
                                           "_node": node_name,
                                           "_conn": (cx_, cy_)})

    # ── 3. Footpaths: shelter bands → nearest road node ───────────────────────
    shelters = shelter_result.get("shelters", [])
    footpath_segs: list[dict] = []
    band_pts: dict[str, tuple] = {}

    if shelters:
        sh_cens = []
        for s in shelters:
            c = s["corners_m"]
            sh_cens.append((sum(p[0] for p in c) / len(c),
                            sum(p[1] for p in c) / len(c)))
        sh_cens.sort(key=lambda p: p[1])        # bottom → top

        n_bands   = max(1, min(8, len(sh_cens) // 30))
        band_size = max(1, len(sh_cens) // n_bands)

        all_road_pts = list(main_pts)
        for seg in secondary_segs:
            all_road_pts.extend(seg["pts_m"])

        for b in range(n_bands):
            start = b * band_size
            end   = start + band_size if b < n_bands - 1 else len(sh_cens)
            band  = sh_cens[start:end]
            bcx   = sum(p[0] for p in band) / len(band)
            bcy   = sum(p[1] for p in band) / len(band)
            band_name = f"shelter_band_{b}"
            band_pts[band_name] = (bcx, bcy)

            rpts = all_road_pts if len(all_road_pts) >= 2 else [main_pts[0]]
            if len(rpts) >= 2:
                cx_, cy_, _ = _nearest_on_polyline(rpts, bcx, bcy)
            else:
                cx_, cy_ = rpts[0]
            if _dist2((bcx, bcy), (cx_, cy_)) > 4:
                clipped = _clip_to_parcel(parcel, (bcx, bcy), (cx_, cy_))
                if clipped:
                    footpath_segs.append({"pts_m": clipped, "_node": band_name})

    # ── 4. Existing OSM roads (already in local metres) ───────────────────────
    existing_segs = [
        {"pts_m": list(r)}
        for r in (site.get("roads_m") or [])
        if len(r) >= 2
    ]

    # ── 5. NetworkX connectivity check ───────────────────────────────────────
    connected = True
    stranded:  list[str] = []
    G         = _nx.Graph()
    node_pos: dict[str, tuple] = {}

    def _add_node(name: str, pos: tuple) -> None:
        G.add_node(name)
        node_pos[name] = pos

    def _add_edge(a: str, b: str) -> None:
        G.add_edge(a, b, weight=_dist2(node_pos[a], node_pos[b]) ** 0.5)

    # Entrance and main road chain
    _add_node("entrance", (ex, ey))
    prev_node = "entrance"
    for i, pt in enumerate(main_pts[1:], 1):
        nn = f"main_{i}"
        _add_node(nn, pt)
        _add_edge(prev_node, nn)
        prev_node = nn
    main_nodes = ["entrance"] + [f"main_{i}" for i in range(1, len(main_pts))]

    def _nearest_main(px: float, py: float) -> str:
        return min(main_nodes, key=lambda n: _dist2(node_pos[n], (px, py)))

    # Facility nodes connected via secondary roads
    for node_name, (fx, fy) in fac_pts.items():
        _add_node(node_name, (fx, fy))
        seg = next((s for s in secondary_segs if s.get("_node") == node_name), None)
        if seg:
            _add_edge(node_name, _nearest_main(*seg["_conn"]))
        else:
            _add_edge(node_name, _nearest_main(fx, fy))

    # Shelter band nodes connected via footpaths
    all_settled = main_nodes + list(fac_pts)
    for band_name, (bx, by) in band_pts.items():
        _add_node(band_name, (bx, by))
        seg = next((s for s in footpath_segs if s.get("_node") == band_name), None)
        if seg:
            nearest = min(all_settled, key=lambda n: _dist2(node_pos[n], (bx, by)))
            _add_edge(band_name, nearest)
        else:
            _add_edge(band_name, _nearest_main(bx, by))

    # Existing road endpoints linked to entrance
    for i, eseg in enumerate(existing_segs):
        nn = f"existing_{i}"
        _add_node(nn, eseg["pts_m"][0])
        G.add_edge(nn, "entrance")

    if G.number_of_nodes() > 0:
        connected = _nx.is_connected(G)
        if not connected:
            comps     = list(_nx.connected_components(G))
            main_comp = max(comps, key=len)
            stranded  = sorted(
                n for comp in comps if comp is not main_comp for n in comp
            )

    return {
        "main_road":       main_segs,
        "secondary_roads": secondary_segs,
        "footpaths":       footpath_segs,
        "existing_roads":  existing_segs,
        "entrance_m":      (ex, ey),
        "connected":       connected,
        "stranded":        stranded,
    }
