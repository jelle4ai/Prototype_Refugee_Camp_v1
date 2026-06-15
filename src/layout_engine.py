"""
Stage 4 layout engine.

Public API
----------
place_all_facilities(site, requirements) -> dict
  CS5 priority order with full collision avoidance.
  Threads an 'occupied' geometry so nothing can stack.
  PA8: seeds occupied from 1 m road setback.
  Returns dict including '_occupied_geo' (shelter exclusion) for place_shelters.

place_shelters(site, requirements, occupied_geo=None) -> dict
  Row-by-row shelter placement AFTER facilities.
  SH6 (≥2 m between shelters): grid gap_unit=2.0 + sentinel 0.01 m buffer.
  SA4 (latrines ≥6 m from shelters): enforced via 6 m latrine buffer in occupied_geo.

optimise_facilities(site, reqs, facilities, shelter_result, roads, max_iter=10) -> (dict, list)
  Greedy coordinate-descent: nudges each movable facility, accepts if partial score improves.
  Deterministic (no randomness). Never introduces overlaps. Returns (facilities, move_log).

place_roads(site, shelter_result, facilities) -> dict
  Road network — unchanged from original.
"""
from math import ceil, sqrt, pi, cos as _cos, sin as _sin, radians as _rad
from shapely.geometry import Polygon as ShapelyPolygon, LineString as _ShapelyLine
from shapely.ops import unary_union
import networkx as _nx


# ── Colours ───────────────────────────────────────────────────────────────────

FACILITY_STYLE: dict[str, tuple[str, str, str]] = {
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
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rect(cx: float, cy: float, w: float, h: float) -> list[tuple[float, float]]:
    hw, hh = w / 2, h / 2
    return [(cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh)]


def _circle(cx: float, cy: float, r: float, n: int = 16) -> list[tuple[float, float]]:
    return [(cx + r * _cos(2 * pi * i / n),
             cy + r * _sin(2 * pi * i / n)) for i in range(n)]


def _union_add(geo, poly, clearance: float = 0.0):
    """Return geo ∪ poly.buffer(clearance). Handles None geo."""
    buf = poly.buffer(clearance) if clearance > 0 else poly
    return buf if geo is None else geo.union(buf)


def _nudge(parcel: ShapelyPolygon,
           cx: float, cy: float,
           w: float, h: float,
           step: float = 3.0,
           max_rings: int = 8,
           occupied=None) -> list[tuple] | None:
    """
    Find a valid w×h rectangle position near (cx, cy).
    Must be fully inside parcel and must not intersect occupied.
    Searches outward in concentric rings of step metres.
    """
    offsets = [(0.0, 0.0)]
    for ring in range(1, max_rings + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if abs(dx) == ring or abs(dy) == ring:
                    offsets.append((dx * step, dy * step))
    for odx, ody in offsets:
        corners = _rect(cx + odx, cy + ody, w, h)
        poly = ShapelyPolygon(corners)
        if not parcel.contains(poly):
            continue
        if occupied is not None and poly.intersects(occupied):
            continue
        return corners
    return None


def _grid_place(parcel: ShapelyPolygon,
                count: int,
                w: float, h: float,
                occupied=None,
                step_mult: float = 1.0,
                intra_clearance: float = 0.0) -> list[dict]:
    """
    Place count w×h rectangles spread across parcel.
    Checks against occupied AND prevents intra-batch overlaps via a local copy.
    intra_clearance: buffer added around each placed item in the intra-batch
      exclusion (e.g. 2.0 for SH6 shelter spacing). Does not affect the caller's
      occupied geometry — caller adds items with their own clearance.
    """
    if count == 0:
        return []
    minx, miny, maxx, maxy = parcel.bounds
    span_x, span_y = maxx - minx, maxy - miny
    aspect = span_x / max(span_y, 1.0)
    # 1:1 grid for large counts: every cell holds exactly one item → even distribution.
    # Loose 3× grid for small counts: headroom for irregular parcels where many cells
    # may fall outside the boundary.
    target = max(count if count >= 15 else count * 3, 9)
    cols = max(1, round(sqrt(target * aspect)))
    rows = max(1, ceil(target / cols))
    cell_w, cell_h = span_x / cols, span_y / rows
    step = max(2.0, min(w, h) / 2) * step_mult

    placed: list[dict] = []
    local_occ = occupied  # never mutates caller's reference

    for row in range(rows):
        for col in range(cols):
            if len(placed) >= count:
                return placed
            cx = minx + (col + 0.5) * cell_w
            cy = miny + (row + 0.5) * cell_h
            corners = _nudge(parcel, cx, cy, w, h, step=step, occupied=local_occ)
            if corners is None:
                continue
            placed.append({"corners_m": corners})
            local_occ = _union_add(local_occ, ShapelyPolygon(corners),
                                   clearance=intra_clearance)
    return placed


def _entry_point(site: dict) -> tuple[float, float]:
    """Parcel boundary vertex nearest to any external road endpoint."""
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


def _footprint(area_m2: float) -> tuple[float, float]:
    unit_w = 5.0
    return unit_w, round(area_m2 / unit_w, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Facility placement  (CS5 priority order, collision-aware)
# ─────────────────────────────────────────────────────────────────────────────

def place_all_facilities(site: dict, requirements: dict) -> dict:
    """
    Place every non-shelter facility in CS5 priority order.

    Key differences from original:
    - No longer takes shelter_result (shelters placed AFTER facilities now).
    - 'occupied' geometry threads through every step — nothing can stack.
    - PA8: initial occupied seeded from 1 m road buffer.
    - Returns '_occupied_geo' (shelter exclusion) with latrines at 6 m (SA4).
    """
    parcel   = ShapelyPolygon(site["parcel_polygon_m"])
    rep_pt   = parcel.representative_point()
    cx, cy   = rep_pt.x, rep_pt.y

    # PA8: 1 m setback from existing OSM roads
    occ = None
    for road in (site.get("roads_m") or []):
        if len(road) >= 2:
            occ = _union_add(occ, _ShapelyLine(road).buffer(1.0))

    out    = {}
    status = {}

    def _req(key: str) -> int:
        return requirements.get(key, {}).get("count", 0)

    def _record(key: str, items: list, required: int) -> None:
        out[key]    = items
        status[key] = {"placed": len(items), "required": required}

    # ── 1. Health post — parcel centroid ─────────────────────────────────────
    hp_w, hp_h = 15.0, 10.0
    hp_req     = _req("health_posts")
    hp_corners = _nudge(parcel, cx, cy, hp_w, hp_h, step=4.0, max_rings=8, occupied=occ)
    hp_cx, hp_cy = cx, cy
    if hp_corners:
        hp_cx = (hp_corners[0][0] + hp_corners[2][0]) / 2
        hp_cy = (hp_corners[0][1] + hp_corners[2][1]) / 2
        occ = _union_add(occ, ShapelyPolygon(hp_corners), clearance=0.5)
    _record("health_post", [{"corners_m": hp_corners}] if hp_corners else [], hp_req)

    # ── 2. Water points — circles distributed across parcel ──────────────────
    wp_req = _req("water_points")
    wp_r   = 3.0
    wp_raw = _grid_place(parcel, wp_req, wp_r * 2, wp_r * 2, occupied=occ)
    wp_out = []
    for item in wp_raw:
        c = item["corners_m"]
        ccx = (c[0][0] + c[2][0]) / 2
        ccy = (c[0][1] + c[2][1]) / 2
        wp_out.append({"corners_m": _circle(ccx, ccy, wp_r)})
    for item in wp_out:
        occ = _union_add(occ, ShapelyPolygon(item["corners_m"]), clearance=0.5)
    _record("water_points", wp_out, wp_req)

    # ── 3. Food distribution — adjacent to health post ────────────────────────
    fd_req = _req("food_distribution_points")
    fd_w, fd_h = 12.0, 8.0
    fd_out = []
    for i in range(fd_req):
        offset = (hp_w / 2 + fd_w / 2 + 3.0) + i * (fd_w + 3.0)
        c = _nudge(parcel, hp_cx + offset, hp_cy, fd_w, fd_h, occupied=occ)
        if c is None:
            c = _nudge(parcel, hp_cx - offset, hp_cy, fd_w, fd_h, occupied=occ)
        if c is None:
            c = _nudge(parcel, hp_cx, hp_cy + offset, fd_w, fd_h, occupied=occ)
        if c:
            fd_out.append({"corners_m": c})
            occ = _union_add(occ, ShapelyPolygon(c), clearance=0.5)
    _record("food_distribution", fd_out, fd_req)

    # ── 4. Community space — opposite side of health post from food ───────────
    cs_req = _req("community_space")
    cs_w, cs_h = 20.0, 15.0
    cs_out = []
    for sign in (-1, 1, 0):
        off = (hp_w / 2 + cs_w / 2 + 3.0) if sign != 0 else 0
        c = _nudge(parcel,
                   hp_cx + sign * off if sign != 0 else hp_cx,
                   hp_cy if sign != 0 else hp_cy + (hp_h / 2 + cs_h / 2 + 3.0),
                   cs_w, cs_h, occupied=occ)
        if c:
            cs_out.append({"corners_m": c})
            occ = _union_add(occ, ShapelyPolygon(c), clearance=0.5)
            break
    _record("community_space", cs_out, cs_req)

    # ── 5. Administrative area — near road entry ──────────────────────────────
    aa_req  = _req("administrative_area")
    aa_w, aa_h = 15.0, 10.0
    ex, ey  = _entry_point(site)
    _dx, _dy = cx - ex, cy - ey
    _d  = (_dx ** 2 + _dy ** 2) ** 0.5 or 1.0
    aa_c = _nudge(parcel,
                  ex + _dx / _d * max(aa_w, aa_h) * 0.8,
                  ey + _dy / _d * max(aa_w, aa_h) * 0.8,
                  aa_w, aa_h, step=3.0, max_rings=10, occupied=occ)
    if aa_c is None:
        fallback = _grid_place(parcel, 1, aa_w, aa_h, occupied=occ)
        aa_c = fallback[0]["corners_m"] if fallback else None
    if aa_c:
        occ = _union_add(occ, ShapelyPolygon(aa_c), clearance=0.5)
    _record("administrative_area", [{"corners_m": aa_c}] if aa_c else [], aa_req)

    # ── 6. Schools — grid distributed ────────────────────────────────────────
    sc_req = _req("schools")
    if sc_req > 0:
        sc_w, sc_h = 20.0, 15.0
        sc_out = _grid_place(parcel, sc_req, sc_w, sc_h, occupied=occ, step_mult=2.0)
        for item in sc_out:
            occ = _union_add(occ, ShapelyPolygon(item["corners_m"]), clearance=0.5)
    else:
        sc_out = []
    _record("schools", sc_out, sc_req)

    # ── 7. Worship facility — near health post if needed ──────────────────────
    wf_req = _req("worship_facility")
    if wf_req == 1:
        wf_w, wf_h = 12.0, 10.0
        wf_c = None
        for _o in (hp_h / 2 + wf_h / 2 + 5,
                   -(hp_h / 2 + wf_h / 2 + 5),
                   hp_w / 2 + wf_w / 2 + 5,
                   -(hp_w / 2 + wf_w / 2 + 5)):
            trial_x = hp_cx + (_o if abs(_o) < 50 else 0)
            trial_y = hp_cy + (_o if abs(_o) < 50 else 0)
            wf_c = _nudge(parcel, trial_x, trial_y, wf_w, wf_h, occupied=occ)
            if wf_c:
                break
        if wf_c:
            occ = _union_add(occ, ShapelyPolygon(wf_c), clearance=0.5)
        _record("worship_facility", [{"corners_m": wf_c}] if wf_c else [], wf_req)
    else:
        _record("worship_facility", [], wf_req)

    # ── 8. Latrine blocks — grid distributed ─────────────────────────────────
    #   Shelters avoid latrines by 6 m (SA4) via '_occupied_geo' below.
    lt_req  = _req("toilets")
    lt_w, lt_h = 4.0, 3.0
    lt_out  = _grid_place(parcel, lt_req, lt_w, lt_h, occupied=occ)
    for item in lt_out:
        occ = _union_add(occ, ShapelyPolygon(item["corners_m"]), clearance=0.5)
    _record("toilets", lt_out, lt_req)

    # ── 9. Washing facilities — adjacent to latrines ──────────────────────────
    wsh_req = _req("washing_facilities")
    wsh_w, wsh_h = 4.0, 3.0
    wsh_out = []
    for latrine in lt_out[:wsh_req]:
        lc  = latrine["corners_m"]
        lcx = (lc[0][0] + lc[2][0]) / 2
        lcy = (lc[0][1] + lc[2][1]) / 2
        wc  = None
        for ddx, ddy in ((lt_w / 2 + wsh_w / 2 + 1, 0),
                          (0, lt_h / 2 + wsh_h / 2 + 1),
                          (-(lt_w / 2 + wsh_w / 2 + 1), 0)):
            wc = _nudge(parcel, lcx + ddx, lcy + ddy, wsh_w, wsh_h,
                        step=1.5, max_rings=4, occupied=occ)
            if wc:
                break
        if wc:
            wsh_out.append({"corners_m": wc})
            occ = _union_add(occ, ShapelyPolygon(wc), clearance=0.5)
    if len(wsh_out) < wsh_req:
        extra = _grid_place(parcel, wsh_req - len(wsh_out), wsh_w, wsh_h, occupied=occ)
        for item in extra:
            occ = _union_add(occ, ShapelyPolygon(item["corners_m"]), clearance=0.5)
        wsh_out.extend(extra)
    _record("washing_facilities", wsh_out[:wsh_req], wsh_req)

    out["status"] = status

    # ── Build shelter exclusion geometry ──────────────────────────────────────
    # Shelters must avoid:
    #   all non-sanitation facilities  → 0.5 m clearance
    #   latrines                       → 6.0 m  (SA4)
    #   washing facilities             → 0.5 m
    #   OSM roads                      → 1.0 m  (PA8)
    sh_geo = None
    for road in (site.get("roads_m") or []):
        if len(road) >= 2:
            sh_geo = _union_add(sh_geo, _ShapelyLine(road).buffer(1.0))
    for key in ("health_post", "water_points", "food_distribution",
                "community_space", "administrative_area", "schools",
                "worship_facility", "washing_facilities"):
        for item in out.get(key, []):
            try:
                sh_geo = _union_add(sh_geo, ShapelyPolygon(item["corners_m"]), clearance=0.5)
            except Exception:
                pass
    for item in out.get("toilets", []):
        try:
            sh_geo = _union_add(sh_geo, ShapelyPolygon(item["corners_m"]), clearance=6.0)
        except Exception:
            pass
    out["_occupied_geo"] = sh_geo

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Shelter placement  (after facilities, collision-aware)
# ─────────────────────────────────────────────────────────────────────────────

def place_shelters(site: dict,
                   requirements: dict,
                   occupied_geo=None) -> dict:
    """
    Grid-based shelter placement AFTER all facilities.

    occupied_geo : shelter exclusion from place_all_facilities.
                   Latrines already buffered 6 m (SA4) inside it.
    SH6 (≥2 m between shelters): intra_clearance=2.0 in _grid_place.
    Uses 1:1 grid (target=count) so shelters spread across the whole
    parcel rather than packing into the bottom-left corner.
    """
    shelter_req = requirements.get("shelter_units", {})
    required    = shelter_req.get("count", 0)
    area_m2     = shelter_req.get("area_per_unit_m2", 17.5)

    if required == 0 or not site.get("parcel_polygon_m"):
        return {"shelters": [], "placed": 0, "required": required}

    unit_w, unit_h = _footprint(area_m2)
    parcel = ShapelyPolygon(site["parcel_polygon_m"])

    items = _grid_place(
        parcel, required, unit_w, unit_h,
        occupied=occupied_geo,
        intra_clearance=2.0,   # SH6: ≥2 m edge-to-edge between shelters
    )
    return {"shelters": items, "placed": len(items), "required": required}


# ─────────────────────────────────────────────────────────────────────────────
# Optimiser  (greedy coordinate-descent, Step 2)
# ─────────────────────────────────────────────────────────────────────────────

_MOVABLE_KEYS = [
    "health_post", "water_points", "food_distribution",
    "community_space", "administrative_area", "schools", "worship_facility",
]

_NUDGE_DISTANCES = [5.0, 10.0, 15.0]
_NUDGE_ANGLES    = [0, 45, 90, 135, 180, 225, 270, 315]


def _partial_score(shelters: list, facilities: dict, parcel: ShapelyPolygon) -> float:
    """
    Fast position-only score used inside the optimiser loop (no roads, no Shapely ops).

    Components and raw weights (sum = 80):
      water_coverage        ×20  — fraction of shelters within 500 m of a water point
      sanitation_dist       ×10  — fraction within 50 m of a latrine
      school_accessibility  ×10  — fraction within 1 km of a school (if any required)
      site_utilisation      ×40  — fraction of 3×3 grid zones with shelters or key facilities
    """
    def _cen(corners):
        return (sum(p[0] for p in corners) / len(corners),
                sum(p[1] for p in corners) / len(corners))

    def _d(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    sh_cens = [_cen(s["corners_m"]) for s in shelters]
    score   = 0.0

    # Water coverage (WS3)
    wp = [_cen(i["corners_m"]) for i in facilities.get("water_points", [])]
    if wp and sh_cens:
        covered = sum(1 for s in sh_cens if any(_d(s, w) <= 500 for w in wp))
        score += covered / len(sh_cens) * 20

    # Sanitation distribution (SA3: 50 m)
    lt = [_cen(i["corners_m"]) for i in facilities.get("toilets", [])]
    if lt and sh_cens:
        covered = sum(1 for s in sh_cens if any(_d(s, l) <= 50 for l in lt))
        score += covered / len(sh_cens) * 10

    # School accessibility (ED3: 1 km)
    sc = [_cen(i["corners_m"]) for i in facilities.get("schools", [])]
    if sc and sh_cens:
        covered = sum(1 for s in sh_cens if any(_d(s, c) <= 1000 for c in sc))
        score += covered / len(sh_cens) * 10

    # Site utilisation — 3×3 grid zone occupancy
    siting_cens = list(sh_cens)
    for k in ("health_post", "food_distribution", "community_space",
              "administrative_area", "worship_facility"):
        for i in facilities.get(k, []):
            try:
                siting_cens.append(_cen(i["corners_m"]))
            except Exception:
                pass
    minx, miny, maxx, maxy = parcel.bounds
    dx = (maxx - minx) / 3 or 1.0
    dy = (maxy - miny) / 3 or 1.0
    occupied_cells: set = set()
    for c in siting_cens:
        ci = min(2, int((c[0] - minx) / dx))
        cj = min(2, int((c[1] - miny) / dy))
        occupied_cells.add((ci, cj))
    score += len(occupied_cells) / 9 * 40

    return score


def optimise_facilities(
        site:           dict,
        reqs:           dict,
        facilities:     dict,
        shelter_result: dict,
        roads:          dict,
        max_iter:       int = 10) -> tuple[dict, list[str]]:
    """
    Greedy coordinate-descent improvement of non-shelter facility positions.

    Algorithm
    ---------
    For each iteration, for each movable facility item:
      1. Remove it from the occupied geometry.
      2. Try 24 candidate positions (8 directions × 3 distances).
      3. Accept the position with the highest _partial_score if it exceeds the current score.
      4. Update occupied geometry with the accepted position.
    Repeat until no facility can be improved or max_iter is reached.

    Guarantees
    ----------
    - Deterministic: candidate order is fixed (no randomness).
    - No overlaps: every candidate is checked against the full occupied geometry.
    - Monotonic: score never decreases across accepted moves.
    """
    parcel   = ShapelyPolygon(site["parcel_polygon_m"])
    shelters = shelter_result.get("shelters", [])

    # Pre-compute road buffer (PA8) — static throughout loop
    road_buf = None
    for road in (site.get("roads_m") or []):
        if len(road) >= 2:
            road_buf = _union_add(road_buf, _ShapelyLine(road).buffer(1.0))

    # Pre-compute shelter union — shelters never move
    shelter_union = None
    for sh in shelters:
        shelter_union = _union_add(shelter_union, ShapelyPolygon(sh["corners_m"]),
                                   clearance=0.01)

    def _build_occ(skip_key: str | None = None, skip_idx: int | None = None):
        """Occupied geometry from roads + shelters + all facilities except one."""
        occ = road_buf
        if shelter_union is not None:
            occ = shelter_union if occ is None else occ.union(shelter_union)
        for k, items in facilities.items():
            if k in ("status", "_occupied_geo"):
                continue
            for i, item in enumerate(items):
                if k == skip_key and i == skip_idx:
                    continue
                try:
                    occ = _union_add(occ, ShapelyPolygon(item["corners_m"]), clearance=0.5)
                except Exception:
                    pass
        return occ

    log: list[str] = []
    current_score  = _partial_score(shelters, facilities, parcel)

    for iteration in range(max_iter):
        any_improvement = False

        for key in _MOVABLE_KEYS:
            items = facilities.get(key, [])
            if not items:
                continue

            for idx in range(len(items)):
                old_corners = items[idx]["corners_m"]
                xs = [p[0] for p in old_corners]
                ys = [p[1] for p in old_corners]
                old_cx  = sum(xs) / len(xs)
                old_cy  = sum(ys) / len(ys)
                fac_w   = max(xs) - min(xs)
                fac_h   = max(ys) - min(ys)

                occ_without = _build_occ(skip_key=key, skip_idx=idx)

                best_corners = None
                best_score   = current_score

                for dist in _NUDGE_DISTANCES:
                    for angle_deg in _NUDGE_ANGLES:
                        trial_cx = old_cx + dist * _cos(_rad(angle_deg))
                        trial_cy = old_cy + dist * _sin(_rad(angle_deg))
                        trial_corners = _nudge(
                            parcel, trial_cx, trial_cy, fac_w, fac_h,
                            step=2.0, max_rings=3, occupied=occ_without,
                        )
                        if trial_corners is None:
                            continue
                        # Temporarily apply the move to score it
                        items[idx]["corners_m"] = trial_corners
                        trial_score = _partial_score(shelters, facilities, parcel)
                        items[idx]["corners_m"] = old_corners  # always restore
                        if trial_score > best_score:
                            best_score   = trial_score
                            best_corners = trial_corners

                if best_corners is not None:
                    items[idx]["corners_m"] = best_corners
                    old_corners    = best_corners
                    current_score  = best_score
                    any_improvement = True
                    log.append(
                        f"iter {iteration + 1}: moved {key}[{idx}] "
                        f"→ partial score {best_score:.1f}"
                    )

        if not any_improvement:
            log.append(f"Converged after {iteration + 1} iteration(s)")
            break
    else:
        log.append(f"Reached iteration limit ({max_iter})")

    return facilities, log


# ─────────────────────────────────────────────────────────────────────────────
# Road network  (PA1 main road · PA2/PA4 secondary roads and footpaths)
# — unchanged from original —
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
    from shapely.geometry import LineString as _LS
    line    = _LS([p1, p2])
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
    fac_pts: dict[str, tuple] = {}

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
            if _dist2((fcx, fcy), (cx_, cy_)) > 4:
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
        sh_cens.sort(key=lambda p: p[1])

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

    for node_name, (fx, fy) in fac_pts.items():
        _add_node(node_name, (fx, fy))
        seg = next((s for s in secondary_segs if s.get("_node") == node_name), None)
        if seg:
            _add_edge(node_name, _nearest_main(*seg["_conn"]))
        else:
            _add_edge(node_name, _nearest_main(fx, fy))

    all_settled = main_nodes + list(fac_pts)
    for band_name, (bx, by) in band_pts.items():
        _add_node(band_name, (bx, by))
        seg = next((s for s in footpath_segs if s.get("_node") == band_name), None)
        if seg:
            nearest = min(all_settled, key=lambda n: _dist2(node_pos[n], (bx, by)))
            _add_edge(band_name, nearest)
        else:
            _add_edge(band_name, _nearest_main(bx, by))

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
