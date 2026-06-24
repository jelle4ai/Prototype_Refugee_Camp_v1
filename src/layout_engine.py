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
from shapely.geometry import (Polygon as ShapelyPolygon,
                               LineString as _ShapelyLine,
                               Point as _ShapelyPoint)
from shapely.ops import unary_union, nearest_points
from shapely.prepared import prep
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

    # Spread the `count` placements evenly across the grid's cell indices
    # instead of filling row-major from the bottom-left corner. Row-major
    # order clusters every instance at the first corner tried for low
    # counts (e.g. 2 schools land at the first 1-2 cells scanned, which can
    # both be in the same corner of the populated area) -- the grid is
    # sized with headroom (target = count*3 for low counts) specifically so
    # not every cell is needed, but that headroom is wasted if scan order
    # never reaches the cells spread across the rest of the grid. Falls
    # back to every remaining cell in original row-major order if a
    # spread-out cell is blocked, so occluded grids are exactly as robust
    # as before.
    total_cells = rows * cols
    if count >= total_cells:
        primary = list(range(total_cells))
    else:
        primary = sorted({
            round(i * (total_cells - 1) / max(1, count - 1))
            for i in range(count)
        })
    primary_set = set(primary)
    cell_order = primary + [i for i in range(total_cells) if i not in primary_set]

    placed: list[dict] = []
    local_occ = occupied  # never mutates caller's reference

    for idx in cell_order:
        if len(placed) >= count:
            return placed
        row, col = divmod(idx, cols)
        cx = minx + (col + 0.5) * cell_w
        cy = miny + (row + 0.5) * cell_h
        corners = _nudge(parcel, cx, cy, w, h, step=step, occupied=local_occ)
        if corners is None:
            continue
        placed.append({"corners_m": corners})
        local_occ = _union_add(local_occ, ShapelyPolygon(corners),
                               clearance=intra_clearance)
    return placed


# Clear margin beyond a community's shared open space before its shelter
# ring (SH12). Module-level (not local to _place_community) because
# place_shelters()'s candidate-retry logic (see _COMM_RETRY_OFFSETS) needs
# the same value to explicitly check a shifted open space against already-
# placed geometry -- the two must stay in sync.
_COMM_OPEN_CLEARANCE_M = 4.0


def _place_community(
    parcel: ShapelyPolygon,
    cx: float,
    cy: float,
    n_families: int,
    shelter_w: float,
    shelter_h: float,
    occ,
) -> dict | None:
    """
    Place one community cluster of up to n_families shelter units arranged in
    an elliptical ring around a shared open space, with embedded latrines,
    a washing unit, and a water tap.

    Rules enforced
    --------------
    SH6   ≥ 2 m between shelter units (intra-clearance in occ)
    SH11  shelters in a ring — not military rows
    SH12  ring orientation keeps entrances facing the shared open space
    SA1   ceil(n_families × 5 / 20) latrine stalls  (≤ 20 pp per stall)
    SA2   ceil(n_families × 5 / 100) washing units  (1 per 100 pp)
    SA3   latrines within community; all shelter–latrine distances ≤ 50 m
    SA4   6 m shelter buffer applied before latrine search
    WS2   one water tap placed inside the shared open space
    WS3   all community shelters ≤ 200 m from tap (guaranteed by construction)
    WS5   tap ≥ 30 m from every placed latrine centroid

    Returns a dict on success:
        shelters       – list of {"corners_m": [...]}
        water_taps     – list of {"corners_m": [...]}  (circle polygon)
        latrines       – list of {"corners_m": [...]}
        washing        – list of {"corners_m": [...]}
        community_poly – ShapelyPolygon convex hull of all placed elements
        occ            – updated shared exclusion geometry

    Returns None if the community cannot be placed (open space outside parcel,
    zero shelters fit, or WS5 cannot be satisfied — caller should try a
    different centre point).
    """
    _AVG_PP   = 5          # Appendix F: ~5 persons per family
    _OPEN_W   = 20.0       # shared open space width  (m) — wide enough to read as open
    _OPEN_H   = 16.0       # shared open space height (m)
    _WP_R     = 3.0        # water-tap circle radius  (m)
    _LT_W     = 4.0        # latrine stall width  (m)
    _LT_H     = 3.0        # latrine stall height (m)
    _WSH_W    = 4.0        # washing unit width  (m)
    _WSH_H    = 3.0        # washing unit height (m)
    _SH_CLEAR = 2.01       # SH6: > 2 m gap; 0.01 sentinel avoids edge ambiguity
    _OPEN_CLR = _COMM_OPEN_CLEARANCE_M  # clear margin beyond open space before shelter ring (SH12)
    _LT_SOUTH = 34.0       # nominal distance south of centre for latrine block
    _WS5_MIN  = 30.0       # WS5: tap–latrine minimum separation (m)

    def _cen(corners):
        n = len(corners)
        return (sum(p[0] for p in corners) / n,
                sum(p[1] for p in corners) / n)

    def _d2d(a, b):
        return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    # ── 0. Shared open space ─────────────────────────────────────────────────
    open_corners = _rect(cx, cy, _OPEN_W, _OPEN_H)
    open_poly    = ShapelyPolygon(open_corners)
    if not parcel.contains(open_poly):
        return None
    # Add open space (+ clearance) to occ so shelters cannot enter it.
    # The tap is placed inside the open space and intentionally bypasses this check.
    occ = _union_add(occ, open_poly, clearance=_OPEN_CLR)

    # ── 1. Shelter ring (SH11: elliptical ring, not military rows) ───────────
    # Minimum radii to clear: open space half-extent + margin + SH6 gap
    # + half shelter dimension.  With _OPEN_CLR=4 m the inner ring sits well
    # clear of the open space so it stays visibly open (SH11, SH12).
    min_rx = _OPEN_W / 2 + _OPEN_CLR + _SH_CLEAR + shelter_w / 2   # ≈ 18.51 m
    min_ry = _OPEN_H / 2 + _OPEN_CLR + _SH_CLEAR + shelter_h / 2   # ≈ 15.76 m

    candidates: list[tuple[float, float]] = []
    for ring in range(1, 5):
        rx     = min_rx + (ring - 1) * (shelter_w + _SH_CLEAR)
        ry     = min_ry + (ring - 1) * (shelter_h + _SH_CLEAR)
        n_pts  = 8 + (ring - 1) * 4
        phase  = ring * pi / n_pts          # stagger successive rings
        for i in range(n_pts):
            angle = 2 * pi * i / n_pts + phase
            candidates.append((cx + rx * _cos(angle),
                               cy + ry * _sin(angle)))

    shelters: list[dict] = []
    local_occ = occ
    for scx, scy in candidates:
        if len(shelters) >= n_families:
            break
        corners = _nudge(parcel, scx, scy, shelter_w, shelter_h,
                         step=1.5, max_rings=4, occupied=local_occ)
        if corners is None:
            continue
        shelters.append({"corners_m": corners})
        local_occ = _union_add(local_occ, ShapelyPolygon(corners),
                               clearance=_SH_CLEAR)   # SH6
    if not shelters:
        return None
    occ = local_occ

    # ── 2. Latrines (SA1: ≤ 20 pp/stall; SA4: ≥ 6 m from all shelters) ─────
    n_latrines = ceil(n_families * _AVG_PP / 20)
    sh_union   = unary_union([ShapelyPolygon(s["corners_m"]) for s in shelters])
    # Build a latrine-search exclusion: main occ + 6 m shelter buffer (SA4).
    # This search zone is local; only placed latrine footprints go back into occ.
    lt_search  = _union_add(occ, sh_union, clearance=6.0)

    # Split latrines between south and north poles of the cluster (SA9: distributed
    # through residential zones).  This keeps all shelters within SA3's 50 m limit
    # even when the ring extends into a second row of candidates.
    n_south = (n_latrines + 1) // 2    # ceiling half goes south
    n_north = n_latrines - n_south      # remainder goes north

    latrines: list[dict] = []
    lt_local = lt_search
    for side_n, side_cy in ((n_south, cy - _LT_SOUTH), (n_north, cy + _LT_SOUTH)):
        for i in range(side_n):
            lt_cx = cx + (i - (side_n - 1) / 2) * (_LT_W + 1.0)
            corners = _nudge(parcel, lt_cx, side_cy, _LT_W, _LT_H,
                             step=3.0, max_rings=12, occupied=lt_local)
            if corners is None:
                continue
            latrines.append({"corners_m": corners})
            lt_local = _union_add(lt_local, ShapelyPolygon(corners), clearance=0.5)
    # Add only latrine footprints to main occ (not the 6 m search buffer)
    for lt in latrines:
        occ = _union_add(occ, ShapelyPolygon(lt["corners_m"]), clearance=0.5)

    # ── 3. Water tap (WS2: one per community; WS5: ≥ 30 m from latrines) ────
    # Tap lives inside the shared open space (centre of cluster).  Latrines are
    # _LT_SOUTH metres north and south, so WS5 is satisfied by construction.
    lt_cens = [_cen(l["corners_m"]) for l in latrines]

    tap_candidates = [
        (cx, cy),                              # open space centre
        (cx, cy + _OPEN_H / 2 - _WP_R),       # north edge
        (cx - _OPEN_W / 2 + _WP_R, cy),       # west edge
        (cx + _OPEN_W / 2 - _WP_R, cy),       # east edge
    ]
    tap_placed = None
    for tc in tap_candidates:
        if lt_cens and min(_d2d(tc, lc) for lc in lt_cens) < _WS5_MIN:
            continue   # WS5 not met at this position
        tap_corners = _circle(tc[0], tc[1], _WP_R)
        tap_poly    = ShapelyPolygon(tap_corners)
        if not parcel.contains(tap_poly):
            continue
        # Tap intentionally placed inside the reserved open space — occ
        # intersection check skipped here; tap is added to occ after.
        tap_placed = {"corners_m": tap_corners}
        occ = _union_add(occ, tap_poly, clearance=0.5)
        break

    if tap_placed is None:
        return None    # WS5 unresolvable — community placement fails (WS2)

    # ── 4. Washing facility (SA2: 1 per 100 pp) ─────────────────────────────
    n_washing   = max(1, ceil(n_families * _AVG_PP / 100))
    washing: list[dict] = []
    ref = latrines[0]["corners_m"] if latrines else _rect(cx, cy - _LT_SOUTH, _LT_W, _LT_H)
    rcx, rcy = _cen(ref)
    for _ in range(n_washing):
        for ddx, ddy in ((_LT_W + 1.0, 0), (0, _LT_H + 1.0),
                          (-(_LT_W + 1.0), 0)):
            wc = _nudge(parcel, rcx + ddx, rcy + ddy, _WSH_W, _WSH_H,
                        step=1.5, max_rings=4, occupied=occ)
            if wc:
                washing.append({"corners_m": wc})
                occ = _union_add(occ, ShapelyPolygon(wc), clearance=0.5)
                break

    # ── 5. Community convex hull (for block-level management and roads) ───────
    hull_polys = [open_poly] + [ShapelyPolygon(s["corners_m"]) for s in shelters]
    if latrines:
        hull_polys += [ShapelyPolygon(l["corners_m"]) for l in latrines]
    community_poly = unary_union(hull_polys).convex_hull

    return {
        "shelters":       shelters,
        "water_taps":     [tap_placed],
        "latrines":       latrines,
        "washing":        washing,
        "open_corners":   open_corners,   # for visualisation / block management
        "community_poly": community_poly,
        "occ":            occ,
    }


def _place_block(
    parcel: ShapelyPolygon,
    block_cx: float,
    block_cy: float,
    n_communities: int,
    shelter_w: float,
    shelter_h: float,
    occ,
) -> dict | None:
    """
    Place one block of up to n_communities community clusters in a compact grid.

    Rules enforced / tracked
    ------------------------
    SH7   built_width returned so the caller can insert a 30 m firebreak gap
          whenever a new block would push the continuous built band past 300 m.
          A single block is well below 300 m, so no internal firebreak is needed.
    SH14  block_poly returned so caller can align firebreak to block boundary.
    PA9   one side of the grid left accessible; road connection managed by
          place_roads in the existing road layer (no roads built here).

    Community pitches
    -----------------
    _COMM_PITCH_X / _Y set the centre-to-centre spacing.  Values are generous
    enough that each community's shelter ring, latrines, and clearance occ do
    not bleed into the adjacent community's reserved area.

    Returns a dict on success:
        shelters     – flat list of all shelter {"corners_m": [...]}
        water_taps   – flat list of all community taps
        latrines     – flat list of all community latrines
        washing      – flat list of all community washing units
        communities  – list of per-community result dicts (include community_poly)
        block_poly   – ShapelyPolygon convex hull of the whole block (SH14)
        built_width  – E-W span in metres  (SH7: caller checks vs 300 m limit)
        built_height – N-S span in metres
        placed       – number of communities actually placed
        occ          – updated shared exclusion geometry

    Returns None if no community could be placed at all.
    """
    _COMM_PITCH_X = 62.0   # community centre-to-centre E-W (m)
    _COMM_PITCH_Y = 82.0   # community centre-to-centre N-S (m)

    n_cols = max(1, round(sqrt(n_communities)))
    n_rows = ceil(n_communities / n_cols)

    # Build candidate community centres in row-major order (south → north,
    # west → east) so the southern row is always populated first, keeping
    # the block entry side clear (PA9).
    centres: list[tuple[float, float]] = []
    for row in range(n_rows):
        for col in range(n_cols):
            if len(centres) >= n_communities:
                break
            ox = (col - (n_cols - 1) / 2) * _COMM_PITCH_X
            oy = (row - (n_rows - 1) / 2) * _COMM_PITCH_Y
            centres.append((block_cx + ox, block_cy + oy))

    all_shelters:    list[dict] = []
    all_taps:        list[dict] = []
    all_latrines:    list[dict] = []
    all_washing:     list[dict] = []
    all_communities: list[dict] = []

    for ccx, ccy in centres:
        result = _place_community(
            parcel, ccx, ccy, 16, shelter_w, shelter_h, occ
        )
        if result is None:
            continue   # slot does not fit; skip without failing the whole block
        occ = result["occ"]
        all_shelters.extend(result["shelters"])
        all_taps.extend(result["water_taps"])
        all_latrines.extend(result["latrines"])
        all_washing.extend(result["washing"])
        all_communities.append(result)

    if not all_communities:
        return None

    # Block convex hull for SH14 firebreak alignment
    block_poly = unary_union(
        [c["community_poly"] for c in all_communities]
    ).convex_hull
    bminx, bminy, bmaxx, bmaxy = block_poly.bounds

    return {
        "shelters":    all_shelters,
        "water_taps":  all_taps,
        "latrines":    all_latrines,
        "washing":     all_washing,
        "communities": all_communities,
        "block_poly":  block_poly,
        "built_width":  bmaxx - bminx,   # SH7: check against 300 m
        "built_height": bmaxy - bminy,
        "placed":      len(all_communities),
        "occ":         occ,
    }


def _entry_point(site: dict) -> tuple[float, float]:
    """
    Point on the parcel boundary nearest to the genuine frontage road.

    PA14: the entrance should connect to a real detected external road.
      1. Pick the ROAD by the length of its geometry that falls within an
         8 m buffer of the parcel, not by single nearest-point distance.
         Single-point distance lets a road that merely touches/terminates
         near a corner beat a road that runs alongside the parcel for
         hundreds of metres (confirmed wrong on two real sites — a trivial
         touch should not outrank genuine frontage).
      2. Project onto the boundary at the midpoint of that road's longest
         alongside-stretch (the piece of its buffer-intersection with the
         greatest length), not its single closest point, which can be
         corner-proximate even once the right road is selected.
    Falls back to nearest-point selection/projection only if no road has
    any stretch within the buffer (e.g. roads that only cross the parcel
    at isolated points).
    """
    parcel_pts = site["parcel_polygon_m"]
    roads = site.get("roads_m") or []
    parcel = ShapelyPolygon(parcel_pts)

    if roads:
        parcel_buf = parcel.buffer(8.0)
        best_road, best_len, best_piece = None, -1.0, None
        nearest_road, nearest_d = None, float("inf")
        for road in roads:
            if len(road) < 2:
                continue
            line = _ShapelyLine(road)
            d = line.distance(parcel)
            inter = line.intersection(parcel_buf)
            alongside_len = inter.length

            # A long road can border an irregular parcel along more than
            # one stretch; the entrance should anchor to the longest
            # contiguous one (the genuine frontage), not a scattered minor
            # touch elsewhere on the same road.
            longest_piece = None
            if not inter.is_empty:
                pieces = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
                for g in pieces:
                    if longest_piece is None or g.length > longest_piece.length:
                        longest_piece = g

            if alongside_len > best_len:
                best_len, best_road, best_piece = alongside_len, road, longest_piece
            if d < nearest_d:
                nearest_d, nearest_road = d, road

        if best_road is not None and best_piece is not None and best_piece.length > 0:
            mid = best_piece.interpolate(best_piece.length / 2)
            _, on_boundary = nearest_points(mid, parcel.exterior)
            return (on_boundary.x, on_boundary.y)
        if nearest_road is not None:
            _, on_boundary = nearest_points(_ShapelyLine(nearest_road), parcel.exterior)
            return (on_boundary.x, on_boundary.y)

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

    # ── 2. Water points — placed inside community modules (one tap per community)
    #       Merged back in _run_placement() so the compliance gate sees the count.
    wp_req = _req("water_points")
    _record("water_points", [], wp_req)

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

    # ── 8. Latrines — placed inside community modules (SA1, SA4 enforced there)
    #       Merged back in _run_placement() so the compliance gate sees the count.
    lt_req = _req("toilets")
    _record("toilets", [], lt_req)

    # ── 9. Washing — placed inside community modules (SA2 enforced there)
    #       Merged back in _run_placement() so the compliance gate sees the count.
    wsh_req = _req("washing_facilities")
    _record("washing_facilities", [], wsh_req)

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
    Community-scan shelter placement AFTER CS5 facilities.

    Hierarchy (Appendix F)
    ----------------------
    16 families → 1 community  (_place_community)
    Communities grouped into blocks of 16 for output / SH7 reporting.

    Strategy
    --------
    Candidate community centres are generated by walking the actual parcel
    interior on a COMM_PITCH_X × COMM_PITCH_Y grid, filtered by an inset
    polygon so every candidate is genuinely usable land.  This fills
    irregular parcels end-to-end rather than stopping when a bounding-box
    position falls outside the real polygon.

    Rules enforced here
    -------------------
    R4   45 m²/pp density gate — hard fail if site area genuinely too small.
    SH7  Per y-band cumulative E-W tracking: 30 m x-offset applied when a
         band reaches 300 m of continuous built area.

    Shortfall reporting
    -------------------
    If candidates are exhausted before n_communities are placed (awkward
    parcel shape, thin strips, etc.) the returned dict includes
    shortfall_communities and shortfall_shelters so the compliance gate
    and caller can surface the partial result explicitly.

    Returns
    -------
    dict: shelters, placed, required, community_water, community_latrines,
          community_washing, communities, blocks, firebreak_xs; and
          optionally r4_fail/r4_detail or shortfall_* keys.
    """
    shelter_req = requirements.get("shelter_units", {})
    required    = shelter_req.get("count", 0)
    area_m2     = shelter_req.get("area_per_unit_m2", 17.5)

    _empty = {
        "shelters": [], "placed": 0, "required": required,
        "community_water": [], "community_latrines": [],
        "community_washing": [], "communities": [], "blocks": [],
        "firebreak_xs": [],
    }

    if required == 0 or not site.get("parcel_polygon_m"):
        return _empty

    unit_w, unit_h = _footprint(area_m2)
    parcel = ShapelyPolygon(site["parcel_polygon_m"])
    minx, miny, maxx, maxy = parcel.bounds

    # R4: 45 m²/pp density gate (area capacity, not placement coverage).
    req_pop = required * 5
    if parcel.area / 45.0 < req_pop:
        return {**_empty,
                "r4_fail": True,
                "r4_detail": (f"site {parcel.area:.0f} m2 supports "
                              f"{parcel.area/45:.0f} pp; {req_pop} pp requested")}

    _N_FAM_PER_COMM   = 16     # Appendix F
    _N_COMM_PER_BLOCK = 16     # Appendix F (grouping only — not a placement limit)
    # Candidate pitch — the closest two community centres can sit while making
    # collision PROVABLY impossible, not just unlikely. _place_community checks
    # every element it places against occ via _nudge (self-protecting: a blocked
    # shelter/latrine/tap/washing is just skipped, never overlapped) EXCEPT the
    # shared open space, which is only checked against the parcel boundary and
    # then unioned into occ unconditionally. So the binding constraint is: a
    # neighbouring community's 20x16 m open space (half-extents 10/8 m) must
    # never be able to reach into this community's worst-case structural extent.
    # Worst case = ring 4 (the deepest the shelter-ring search ever goes) at the
    # largest shelter footprint in the codebase (22.5 m² cold-climate units,
    # 5.0 x 4.5 m): rx4 = 18.51 + 3*(5.0+2.01) = 39.54, ry4 = 16.26 + 3*(4.5+2.01)
    # = 35.79; outer edge from centre = 39.54+2.5 = 42.04 m (x), 35.79+2.25 =
    # 38.04 m (y) — both exceed the fixed-offset latrines (cy±34, outer edge
    # 35.5 m), so the shelter ring dominates on both axes.
    # Minimum collision-proof pitch = worst-case extent + neighbour's open-space
    # half-extent: x >= 42.04+10 = 52.04, y >= 38.04+8 = 46.04. Both chosen
    # values sit ~2 m above that ceiling — true regardless of climate, ring
    # depth, or obstruction pattern, not a probabilistic margin.
    _COMM_PITCH_X     = 54.0   # community centre-to-centre E-W (m)
    _COMM_PITCH_Y     = 48.0   # community centre-to-centre N-S (m)
    _SH7_LIMIT        = 300.0  # SH7: firebreak after 300 m continuous E-W built area
    _SH7_BREAK        = 30.0   # SH7: firebreak width (m)
    _COMM_W_EST       = 50.0   # approximate E-W footprint of one community for SH7
    # Inset margin — derived from community geometry, not a flat guess.
    # Binding constraint is WS5 (tap ≥ 30 m from latrines) on the N-S axis:
    #   South latrines sit at cy − 34 m; if cy < 34 m, _nudge places them near
    #   y = 0 (centroid ≈ 1.5 m), giving tap–latrine distance cy − 1.5 m.
    #   For WS5 to pass: cy ≥ 31.5 m → round up to 35 m.
    # E-W axis (ring-1 at cx ± 18.5 m, _nudge tolerance 6 m, half-shelter 2.5 m)
    #   requires only 15 m; 35 m covers both axes and is tighter than the old 50 m.
    _MARGIN           = 35.0
    # Community open-space half-extents (must match _place_community constants).
    _OPEN_HW, _OPEN_HH = 10.0, 8.0   # half of _OPEN_W = 20, _OPEN_H = 16
    # Small nearby shifts tried when a lattice candidate's open space hits a
    # CS5 facility (see below) -- axis-aligned first (cleanest for the
    # typically-rectangular CS5 footprints), closest distance first.
    _COMM_RETRY_OFFSETS = [
        (0, 20), (0, -20), (20, 0), (-20, 0),
        (0, 15), (0, -15), (15, 0), (-15, 0),
        (20, 20), (20, -20), (-20, 20), (-20, -20),
    ]

    n_communities = ceil(required / _N_FAM_PER_COMM)

    # Inset the parcel by _MARGIN so community centres stay clear of the boundary.
    inset = parcel.buffer(-_MARGIN)
    if inset.is_empty:
        inset = parcel   # very small / narrow parcel fallback

    # Capture CS5 exclusion geometry before any community is added.  Used below
    # to skip candidate positions where the community open space would land on a
    # CS5 facility — the tap inside the open space intentionally bypasses the occ
    # check, so we guard at candidate-selection level instead.
    cs5_geo = occupied_geo

    # Build candidate community centres: walk the inset's OWN bounds (not the
    # parcel's bounds with margin subtracted) on community pitch, south → north,
    # west → east (so SH7 tracks left-to-right within y-bands). Walking the
    # inset's bounds keeps this correct on the very-small/narrow fallback above:
    # if inset == parcel (no margin applied), the walk covers the full parcel
    # instead of parcel-bounds-minus-margin potentially inverting to zero rows.
    # Use intersects() rather than contains() so points on the inset boundary
    # (i.e. exactly _MARGIN from the parcel edge — valid minimum positions) are
    # included rather than silently dropped.
    gminx, gminy, gmaxx, gmaxy = inset.bounds
    candidates: list[tuple[float, float]] = []
    y = gminy
    while y <= gmaxy:
        x = gminx
        while x <= gmaxx:
            if inset.intersects(_ShapelyPoint(x, y)):
                candidates.append((x, y))
            x += _COMM_PITCH_X
        y += _COMM_PITCH_Y

    # SH7 tracking: per y-band cumulative E-W built width.
    # Communities within the same grid row share a y-band index.
    def _y_band(y_val: float) -> int:
        return round((y_val - miny) / _COMM_PITCH_Y)

    cum_ew: dict[int, float]  = {}
    x_off:  dict[int, float]  = {}
    firebreak_xs: list[float] = []

    occ             = occupied_geo
    all_communities: list[dict] = []
    all_shelters:    list[dict] = []
    all_water:       list[dict] = []
    all_latrines:    list[dict] = []
    all_washing:     list[dict] = []

    for cx_raw, cy in candidates:
        if len(all_communities) >= n_communities:
            break

        band     = _y_band(cy)
        band_cum = cum_ew.get(band, 0.0)
        band_off = x_off.get(band, 0.0)

        # SH7 span tracking.  The E-W extent of N communities on _COMM_PITCH_X centres
        # is: first community contributes _COMM_W_EST (its own width); each subsequent
        # one advances the right edge by _COMM_PITCH_X (the centre-to-centre pitch).
        # Using only _COMM_W_EST per community underestimates the span by the gap
        # (_COMM_PITCH_X − _COMM_W_EST = 12 m) per community and misses the SH7 trigger.
        span_add = _COMM_W_EST if band_cum == 0.0 else _COMM_PITCH_X
        if band_cum > 0.0 and band_cum + span_add > _SH7_LIMIT:
            firebreak_xs.append(cx_raw + band_off)
            band_off += _SH7_BREAK
            x_off[band]  = band_off
            band_cum     = 0.0           # reset span for this new sub-band
            span_add     = _COMM_W_EST   # this community is now first in the sub-band

        cx = cx_raw + band_off

        # Skip if the firebreak offset has pushed this position outside the inset.
        if not inset.intersects(_ShapelyPoint(cx, cy)):
            continue

        # Skip if the community open space (20 × 16 m) would land on a CS5
        # facility -- unless a small nearby shift clears it. The candidate
        # lattice is sized with NO redundancy (exactly n_communities points
        # for n_communities required, see the pitch derivation above), so
        # losing even one candidate to a CS5 facility -- typically much
        # smaller than the 54x48 m pitch -- makes an otherwise-genuinely-
        # fittable community permanently unplaceable, even with abundant
        # free space nearby (diagnosed and confirmed via instrumented trace:
        # see PROGRESS.md Stage E). The collision-proof pitch derivation only
        # holds for candidates exactly on the lattice, so any shifted centre
        # is explicitly re-checked against the CS5 geometry AND the already-
        # placed-community geometry (occ) before being accepted -- this is a
        # safety IMPROVEMENT (an explicit check) over the open space's normal
        # placement, which only relies on the lattice spacing and is never
        # itself checked against occ.
        def _open_rect(tx: float, ty: float) -> ShapelyPolygon:
            return ShapelyPolygon([
                (tx - _OPEN_HW, ty - _OPEN_HH), (tx + _OPEN_HW, ty - _OPEN_HH),
                (tx + _OPEN_HW, ty + _OPEN_HH), (tx - _OPEN_HW, ty + _OPEN_HH),
            ])

        if cs5_geo is not None and _open_rect(cx, cy).intersects(cs5_geo):
            shifted = None
            for ddx, ddy in _COMM_RETRY_OFFSETS:
                tx, ty = cx + ddx, cy + ddy
                if not inset.intersects(_ShapelyPoint(tx, ty)):
                    continue
                trial_open = _open_rect(tx, ty)
                if trial_open.intersects(cs5_geo):
                    continue
                if occ is not None and trial_open.buffer(_COMM_OPEN_CLEARANCE_M).intersects(occ):
                    continue
                shifted = (tx, ty)
                break
            if shifted is None:
                continue
            cx, cy = shifted

        result = _place_community(parcel, cx, cy, _N_FAM_PER_COMM, unit_w, unit_h, occ)
        if result is None:
            continue

        occ = result["occ"]
        all_communities.append(result)
        all_shelters.extend(result["shelters"])
        all_water.extend(result["water_taps"])
        all_latrines.extend(result["latrines"])
        all_washing.extend(result["washing"])
        cum_ew[band] = band_cum + span_add

    # Group placed communities into reporting blocks of up to _N_COMM_PER_BLOCK.
    all_blocks: list[dict] = []
    for i in range(0, len(all_communities), _N_COMM_PER_BLOCK):
        group = all_communities[i : i + _N_COMM_PER_BLOCK]
        block_poly = unary_union([c["community_poly"] for c in group]).convex_hull
        bminx_, bminy_, bmaxx_, bmaxy_ = block_poly.bounds
        all_blocks.append({
            "communities":  group,
            "block_poly":   block_poly,
            "built_width":  bmaxx_ - bminx_,
            "built_height": bmaxy_ - bminy_,
            "placed":       len(group),
        })

    placed_comms    = len(all_communities)
    shortfall_comms = n_communities - placed_comms
    shortfall_sh    = required - len(all_shelters)

    out = {
        "shelters":           all_shelters,
        "placed":             len(all_shelters),
        "required":           required,
        "community_water":    all_water,
        "community_latrines": all_latrines,
        "community_washing":  all_washing,
        "communities":        all_communities,
        "blocks":             all_blocks,
        "firebreak_xs":       firebreak_xs,
    }
    if shortfall_comms > 0:
        out["shortfall_communities"] = shortfall_comms
        out["shortfall_shelters"]    = shortfall_sh
    return out


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
# Single-instance facility move  (Step 5 feedback execution)
# ─────────────────────────────────────────────────────────────────────────────

_DIRECTION_VECTORS = {
    "north": (0.0, 1.0),
    "south": (0.0, -1.0),
    "east":  (1.0, 0.0),
    "west":  (-1.0, 0.0),
}
_MOVE_STEP             = 2.0    # search granularity, metres
MOVE_DEFAULT_DISTANCE_M = 26.0  # default travel when the planner names no distance (13 steps, exact)


def _blockers_for(candidate: ShapelyPolygon,
                   parcel: ShapelyPolygon,
                   road_buf,
                   shelter_union,
                   other_geo: dict[str, object]) -> list[str]:
    """Every piece of geometry *candidate* actually collides with, named for
    the planner — not just the first one in a fixed priority order."""
    blockers: list[str] = []
    if not parcel.contains(candidate):
        blockers.append("the parcel boundary")
    if road_buf is not None and candidate.intersects(road_buf):
        blockers.append("the road network")
    if shelter_union is not None and candidate.intersects(shelter_union):
        blockers.append("shelters")
    for k, geo in other_geo.items():
        if candidate.intersects(geo):
            blockers.append(FACILITY_STYLE[k][0].lower())
    return blockers or ["the available space in that direction"]


def move_facility(
        site:           dict,
        facilities:     dict,
        shelter_result: dict,
        roads:          dict,
        key:            str,
        direction:      str,
        distance_m:     float | None = None,
) -> tuple[dict, str | None, float | None, list[str] | None]:
    """
    Move the single instance of facility *key* in *direction* by
    *distance_m* (or MOVE_DEFAULT_DISTANCE_M if not given), against the real
    occupied geometry (roads, shelters, every other facility). Reuses the
    same occupied-geometry construction and _nudge() search the optimiser
    uses — just walked along one axis instead of searched in 8 directions.

    Searches from the full requested distance downward in _MOVE_STEP
    increments and lands at the FURTHEST valid position at or under that
    distance, so a partially-blocked direction still moves as far as it can
    rather than failing outright.

    Returns (facilities, reason, moved_m, blocked_by):
      - Full move: reason=None, moved_m=requested distance, blocked_by=None.
      - Partial move (blocked before reaching the requested distance):
        reason=None, moved_m=the shorter distance actually travelled,
        blocked_by=names of everything that stopped it going further.
      - Rejection (not even one step possible): *facilities* unchanged,
        reason names every piece of geometry the nearest attempted position
        collided with, moved_m=None, blocked_by=the same names as reason.

    Only valid for facility types with exactly one placed instance; callers
    must check that before calling.
    """
    parcel = ShapelyPolygon(site["parcel_polygon_m"])
    items  = facilities.get(key, [])
    if len(items) != 1:
        return (facilities,
                f"{FACILITY_STYLE[key][0]} has more than one instance — cannot target a single one yet.",
                None, None)

    target_distance = distance_m if distance_m and distance_m > 0 else MOVE_DEFAULT_DISTANCE_M

    old_corners = items[0]["corners_m"]
    xs = [p[0] for p in old_corners]
    ys = [p[1] for p in old_corners]
    old_cx, old_cy = sum(xs) / len(xs), sum(ys) / len(ys)
    fac_w, fac_h   = max(xs) - min(xs), max(ys) - min(ys)

    # Pre-compute the same sub-geometries optimise_facilities() unions into
    # occ_without, kept separate here so a rejection can name which one hit.
    road_buf = None
    for road in (site.get("roads_m") or []):
        if len(road) >= 2:
            road_buf = _union_add(road_buf, _ShapelyLine(road).buffer(1.0))

    shelter_union = None
    for sh in shelter_result.get("shelters", []):
        shelter_union = _union_add(shelter_union, ShapelyPolygon(sh["corners_m"]), clearance=0.01)

    other_geo: dict[str, object] = {}
    for k, fac_items in facilities.items():
        if k in ("status", "_occupied_geo") or k == key:
            continue
        geo = None
        for item in fac_items:
            try:
                geo = _union_add(geo, ShapelyPolygon(item["corners_m"]), clearance=0.5)
            except Exception:
                pass
        if geo is not None:
            other_geo[k] = geo

    occ_without = road_buf
    if shelter_union is not None:
        occ_without = shelter_union if occ_without is None else occ_without.union(shelter_union)
    for geo in other_geo.values():
        occ_without = geo if occ_without is None else occ_without.union(geo)

    dx, dy = _DIRECTION_VECTORS[direction]
    old_proj = old_cx * dx + old_cy * dy
    nearest_blocked: ShapelyPolygon | None = None

    # Walk DOWN from the full requested distance to the smallest step, so the
    # first hit found is the FURTHEST valid position at or under the target —
    # a partially-blocked direction still moves as far as it can rather than
    # failing outright. nearest_blocked is overwritten every iteration so
    # that, when a step succeeds, it holds the immediately-preceding failed
    # attempt (what stopped further progress) — or, if every distance fails,
    # the smallest one (closest to the facility's current position), the
    # most informative one to diagnose a total rejection against.
    n_target_steps = max(1, round(target_distance / _MOVE_STEP))
    for step_n in range(n_target_steps, 0, -1):
        dist = step_n * _MOVE_STEP
        trial_cx, trial_cy = old_cx + dx * dist, old_cy + dy * dist
        corners = _nudge(parcel, trial_cx, trial_cy, fac_w, fac_h,
                         step=2.0, max_rings=3, occupied=occ_without)
        if corners is not None:
            # _nudge's own ring search can land back near the starting point
            # (its search radius can exceed this step's distance) — only
            # accept a candidate that is genuine forward progress, so a
            # blocked direction can never be silently reported as a move.
            cxs = [p[0] for p in corners]
            cys = [p[1] for p in corners]
            new_cx, new_cy = sum(cxs) / len(cxs), sum(cys) / len(cys)
            if new_cx * dx + new_cy * dy > old_proj + 0.5:
                items[0]["corners_m"] = corners
                moved_dist = ((new_cx - old_cx) ** 2 + (new_cy - old_cy) ** 2) ** 0.5
                blocked_by = (
                    _blockers_for(nearest_blocked, parcel, road_buf, shelter_union, other_geo)
                    if step_n < n_target_steps and nearest_blocked is not None
                    else None
                )
                return facilities, None, moved_dist, blocked_by
        nearest_blocked = ShapelyPolygon(_rect(trial_cx, trial_cy, fac_w, fac_h))

    # No valid position found anywhere along that direction — diagnose the
    # nearest attempted candidate against every sub-geometry it could have
    # collided with, reporting all of them, not just the highest-priority one.
    blockers = _blockers_for(nearest_blocked, parcel, road_buf, shelter_union, other_geo) \
        if nearest_blocked is not None else ["the available space in that direction"]
    reason = (
        f"Move rejected — no valid position to the {direction}: "
        f"blocked by {', '.join(blockers)}."
    )
    return facilities, reason, None, blockers


# ─────────────────────────────────────────────────────────────────────────────
# Road network  (PA1 main road · PA2/PA4 secondary roads and footpaths)
# — unchanged from original —
# ─────────────────────────────────────────────────────────────────────────────

def _dist2(a: tuple, b: tuple) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _cluster_targets(points: list[tuple], radius: float = 12.0) -> list[tuple]:
    """
    Reduce *points* to a small set of representatives such that every input
    point is within *radius* of one of them -- greedy, deterministic
    (input order), not optimal clustering. Used so a single average point
    doesn't silently fail to represent an outlier (e.g. one latrine stall
    that landed well away from the rest of its row via a ring-search
    fallback) while still collapsing a normal tight row into one target.
    """
    targets: list[tuple] = []
    for p in points:
        if not any(_dist2(p, t) <= radius * radius for t in targets):
            targets.append(p)
    return targets


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


def _displace_from_obstacles(pt: tuple, obstacles: list,
                             clearance: float = 2.0) -> tuple:
    """
    If *pt* sits inside any obstacle (e.g. the parcel centroid landing
    inside the health post, which is placed there too), push it out to just
    outside that obstacle's boundary. A waypoint a road must actually reach
    can't be routed "around" if the point itself is the obstacle.
    """
    p = _ShapelyPoint(pt)
    for ob in obstacles:
        if ob.contains(p):
            nearest = nearest_points(p, ob.exterior)[1]
            ocx, ocy = ob.centroid.x, ob.centroid.y
            dx, dy = nearest.x - ocx, nearest.y - ocy
            dlen = (dx * dx + dy * dy) ** 0.5 or 1.0
            return (nearest.x + dx / dlen * clearance,
                    nearest.y + dy / dlen * clearance)
    return pt


def _route_around(p1: tuple, p2: tuple, obstacles: list,
                  corridor: float = 12.0, clearance: float = 1.5,
                  max_obstacles: int = 8,
                  _forced: tuple = (), _depth: int = 0) -> list[tuple]:
    """
    Polyline from p1 to p2 that avoids *obstacles* (a list of ShapelyPolygons
    the path must not cut through -- shelters, facility footprints), via a
    small local visibility graph. Falls back to the direct segment when it
    already clears every obstacle, which is the common case.

    Only obstacles within *corridor* metres of the direct line (plus any
    already known to matter from a prior retry, via *_forced*) become graph
    nodes, capped to the *max_obstacles* nearest -- a wide corridor through
    a dense shelter field would otherwise blow up the visibility graph
    (O(nodes^2) edges, each checked against every relevant obstacle). After
    finding a candidate path, it is re-checked against every obstacle (not
    just the nearby ones) -- a detour around one obstacle can pass close to
    a second one outside the original corridor -- and retried with that
    obstacle folded in if so, up to 3 times.
    """
    direct = _ShapelyLine([p1, p2])
    forced_ids = {id(o) for o in _forced}
    nearby = [o for o in obstacles
             if id(o) not in forced_ids and o.intersects(direct.buffer(corridor))]
    nearby.sort(key=lambda o: direct.distance(o))
    relevant = list(_forced) + nearby[:max_obstacles]
    blocking = [o for o in relevant
                if direct.intersects(o) and direct.intersection(o).length > 0.05]
    if not blocking:
        return [p1, p2]

    prepared = [(o, prep(o)) for o in relevant]

    def _blocked_by_any(seg) -> bool:
        for o, pr in prepared:
            if pr.intersects(seg) and seg.intersection(o).length > 0.05:
                return True
        return False

    nodes: dict[str, tuple] = {"_start": p1, "_end": p2}
    for k, ob in enumerate(relevant):
        buf = ob.buffer(clearance, join_style=2)
        for j, c in enumerate(list(buf.exterior.coords)[:-1]):
            nodes[f"o{k}_{j}"] = c

    names = list(nodes.keys())
    G = _nx.Graph()
    G.add_nodes_from(names)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b   = names[i], names[j]
            pa, pb = nodes[a], nodes[b]
            seg    = _ShapelyLine([pa, pb])
            if _blocked_by_any(seg):
                continue
            G.add_edge(a, b, weight=_dist2(pa, pb) ** 0.5)

    try:
        path_names = _nx.shortest_path(G, "_start", "_end", weight="weight")
    except (_nx.NetworkXNoPath, _nx.NodeNotFound):
        return [p1, p2]   # honest fallback: no clear route found
    path = [nodes[n] for n in path_names]

    relevant_ids = {id(o) for o in relevant}
    route_line = _ShapelyLine(path)
    missed = [o for o in obstacles
             if id(o) not in relevant_ids
             and route_line.intersects(o) and route_line.intersection(o).length > 0.05]
    if missed and _depth < 3:
        return _route_around(p1, p2, obstacles, corridor=corridor,
                             clearance=clearance, max_obstacles=max_obstacles,
                             _forced=tuple(relevant) + tuple(missed),
                             _depth=_depth + 1)
    return path


def _route_segment(parcel: ShapelyPolygon, p1: tuple, p2: tuple,
                   obstacles: list, corridor: float = 12.0) -> list | None:
    """_route_around() + clip every leg of the result to the parcel."""
    routed = _route_around(p1, p2, obstacles, corridor=corridor)
    pts: list[tuple] = []
    for i in range(len(routed) - 1):
        clipped = _clip_to_parcel(parcel, routed[i], routed[i + 1])
        if clipped:
            for pt in clipped:
                if not pts or _dist2(pt, pts[-1]) > 1:
                    pts.append(pt)
    return pts or None


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

    # ── Far point: parcel boundary vertex farthest from the entrance ──────────
    # PA1: the main road must run the length of the site, not just connect a
    # couple of clustered interior points. Previously the third waypoint was
    # the health post — itself usually placed near the centre, like most CS5
    # facilities — so all three waypoints (entrance, centroid, health post)
    # ended up clustered together, giving secondary/tertiary roads almost no
    # spread of attachment points to branch from (the "everything radiates
    # from one point" symptom). Using the farthest boundary vertex instead
    # gives a real backbone spanning two genuine extremities of the parcel,
    # with the centroid as a midpoint — three well-separated attachment
    # regions instead of one tight cluster. Facilities (including the health
    # post) still connect via the unchanged secondary-road logic below.
    far_x, far_y = max(parcel.exterior.coords, key=lambda p: _dist2(p, (ex, ey)))

    # ── Trim the far end to the populated extent, not the geometric vertex ────
    # PA1: the generated main road should stop a small margin past the
    # furthest placed shelter/community, not run on to the farthest parcel
    # boundary vertex regardless of where the camp actually ends up --
    # otherwise it crosses empty land beyond the last shelters. The
    # entrance end is untouched. This only affects the GENERATED main road
    # (main_segs below); existing OSM roads from site["roads_m"] are
    # tracked separately as "existing_roads" further down and are never
    # modified here.
    #
    # The farthest-projected shelter (along the entrance -> original far
    # vertex axis) identifies WHICH shelter marks the populated extent in
    # that general direction, but the final far point re-aims straight at
    # that specific shelter (not the original axis) before adding the
    # margin -- the populated area is rarely exactly on-axis with the
    # parcel's geometric far corner, and aiming at the actual shelter is
    # what keeps the terminus close to it rather than merely closer.
    _MAIN_ROAD_MARGIN_M = 35.0
    _dir_dx, _dir_dy = far_x - ex, far_y - ey
    _dir_len = (_dir_dx ** 2 + _dir_dy ** 2) ** 0.5 or 1.0
    _dir_dx, _dir_dy = _dir_dx / _dir_len, _dir_dy / _dir_len

    _populated_pts = [
        (sum(p[0] for p in s["corners_m"]) / len(s["corners_m"]),
         sum(p[1] for p in s["corners_m"]) / len(s["corners_m"]))
        for s in shelter_result.get("shelters", [])
    ]
    if _populated_pts:
        _tx, _ty = max(_populated_pts,
                       key=lambda p: (p[0] - ex) * _dir_dx + (p[1] - ey) * _dir_dy)
        _tdx, _tdy = _tx - ex, _ty - ey
        _tdist = (_tdx ** 2 + _tdy ** 2) ** 0.5
        if _tdist > 0.1:
            _tdx, _tdy = _tdx / _tdist, _tdy / _tdist
            _far_dist = min(_dir_len, _tdist + _MAIN_ROAD_MARGIN_M)
            far_x, far_y = ex + _tdx * _far_dist, ey + _tdy * _far_dist
    # else: no shelters placed (e.g. R4 failure) -- fall back to the
    # geometric farthest vertex computed above; there is no populated extent
    # to trim to.

    # ── Obstacles every road level must route around (PA10-16 realism): every
    # placed shelter and facility footprint. The health post in particular
    # sits at the parcel centroid -- exactly where the main road's middle
    # waypoint lands -- so a straight main road regularly cut through it
    # before this was wired in.
    #
    # Tagged by id(item["corners_m"]) rather than just the built polygon, so
    # a connector can cleanly exclude "the thing it's connecting to" -- a
    # facility for secondary roads, a community's own shelters/latrines/
    # washing/tap for tertiary paths -- even though the same underlying item
    # dict (e.g. a community's water tap) also appears in the flattened
    # facilities lists used to build this obstacle set.
    def _tag(items: list) -> list[tuple[int, ShapelyPolygon]]:
        return [(id(it["corners_m"]), ShapelyPolygon(it["corners_m"])) for it in items]

    _FAC_KEYS = [
        "health_post", "water_points", "food_distribution",
        "community_space", "administrative_area", "schools", "worship_facility",
    ]
    # Obstacle set is wider than the secondary-road facility list above: it
    # also includes toilets and washing units (community-level facilities,
    # routed to via tertiary paths below, not secondary roads), which are
    # just as real an obstacle to a road as any named CS5 facility.
    _OBSTACLE_FAC_KEYS = _FAC_KEYS + ["toilets", "washing_facilities"]

    shelter_entries:  list[tuple[int, ShapelyPolygon]] = _tag(shelter_result.get("shelters", []))
    facility_entries: list[tuple[int, ShapelyPolygon]] = [
        e for key in _OBSTACLE_FAC_KEYS for e in _tag(facilities.get(key, []))
    ]
    all_entries    = shelter_entries + facility_entries
    all_obstacles  = [p for _, p in all_entries]

    def _obstacles_excluding(exclude_ids: set) -> list:
        return [p for cid, p in all_entries if cid not in exclude_ids]

    fac_key_idx: list[tuple[str, int]] = [
        (key, idx)
        for key in _FAC_KEYS
        for idx in range(len(facilities.get(key, [])))
    ]

    # ── 1. Main road: entrance → parcel centroid → farthest boundary point ────
    raw_wp = [(ex, ey),
             _displace_from_obstacles((rep.x, rep.y), all_obstacles),
             (far_x, far_y)]
    waypoints: list[tuple] = [raw_wp[0]]
    for pt in raw_wp[1:]:
        if _dist2(pt, waypoints[-1]) > 25:      # skip if < 5 m apart
            waypoints.append(pt)

    main_segs: list[dict] = []
    main_pts:  list[tuple] = []
    for i in range(len(waypoints) - 1):
        routed = _route_segment(parcel, waypoints[i], waypoints[i + 1],
                                all_obstacles)
        if routed:
            main_segs.append({"pts_m": routed})
            for pt in routed:
                if not main_pts or _dist2(pt, main_pts[-1]) > 1:
                    main_pts.append(pt)

    if not main_pts:                             # fallback: straight line
        main_pts  = [(ex, ey), (rep.x, rep.y)]
        main_segs = [{"pts_m": list(main_pts)}]

    # ── 2. Secondary roads: each major facility → nearest point on main road ──
    secondary_segs: list[dict] = []
    fac_pts: dict[str, tuple] = {}

    for key, idx in fac_key_idx:
        item = facilities[key][idx]
        c = item["corners_m"]
        fcx = sum(p[0] for p in c) / len(c)
        fcy = sum(p[1] for p in c) / len(c)
        node_name = f"{key}_{idx}"
        fac_pts[node_name] = (fcx, fcy)
        if len(main_pts) >= 2:
            cx_, cy_, _d = _nearest_on_polyline(main_pts, fcx, fcy)
        else:
            cx_, cy_ = main_pts[0]
        if _dist2((fcx, fcy), (cx_, cy_)) > 4:
            # Exclude this facility's own footprint -- the connector
            # necessarily starts inside it -- from the obstacles it must
            # route around.
            obstacles = _obstacles_excluding({id(c)})
            routed = _route_segment(parcel, (fcx, fcy), (cx_, cy_), obstacles)
            if routed:
                secondary_segs.append({"pts_m": routed,
                                       "_node": node_name,
                                       "_conn": (cx_, cy_)})

    # ── 3. Tertiary paths: each community's open space → nearest road node ────
    # PA10-16: every community must be individually reachable, not just
    # "shelters in general" via an arbitrary band grouping -- a planner
    # needs to know THIS community connects, not that some unnamed cluster
    # of 30 shelters does. Source point is the community's own shared open
    # space centroid, which is guaranteed clear of its own shelters by
    # construction (_place_community reserves it before placing the ring).
    communities = shelter_result.get("communities", [])
    footpath_segs: list[dict] = []
    comm_pts: dict[str, tuple] = {}

    if communities:
        all_road_pts = list(main_pts)
        for seg in secondary_segs:
            all_road_pts.extend(seg["pts_m"])

        for ci, comm in enumerate(communities):
            open_corners = comm.get("open_corners")
            if open_corners:
                ocx = sum(p[0] for p in open_corners) / len(open_corners)
                ocy = sum(p[1] for p in open_corners) / len(open_corners)
            else:
                poly = comm.get("community_poly")
                if poly is None:
                    continue
                ocx, ocy = poly.centroid.x, poly.centroid.y

            comm_name = f"community_{ci}"
            comm_pts[comm_name] = (ocx, ocy)

            # Exclude this community's own shelters/latrines/washing/tap --
            # the path necessarily starts among them -- from what it must
            # route around.
            own_ids = {id(s["corners_m"]) for s in comm.get("shelters", [])}
            own_ids |= {id(l["corners_m"]) for l in comm.get("latrines", [])}
            own_ids |= {id(w["corners_m"]) for w in comm.get("washing", [])}
            own_ids |= {id(t["corners_m"]) for t in comm.get("water_taps", [])}
            obstacles = _obstacles_excluding(own_ids)

            rpts = all_road_pts if len(all_road_pts) >= 2 else [main_pts[0]]
            if len(rpts) >= 2:
                cx_, cy_, _d = _nearest_on_polyline(rpts, ocx, ocy)
            else:
                cx_, cy_ = rpts[0]

            # Entry spur: nearest road point -> this community's open space.
            # Always drawn (PA10-16 realism), even when short, so the map
            # shows a real path into every community rather than only an
            # abstract graph edge for connectivity bookkeeping -- skipped
            # only when the open space already sits right on the road.
            if _dist2((ocx, ocy), (cx_, cy_)) > 1:
                routed = _route_segment(parcel, (cx_, cy_), (ocx, ocy), obstacles)
                if routed:
                    footpath_segs.append({"pts_m": routed, "_node": comm_name})

            # Spurs reaching the community's interior near its latrine
            # blocks. _place_community splits latrines north and south of
            # the shared open space (Appendix F), so a planner needs the
            # path to actually reach those blocks, not stop at the open
            # space centre. A single spur per side isn't always enough: a
            # latrine stall that needed its ring-search fallback (blocked
            # by a firebreak/neighbour) can land well away from the rest of
            # its own row, so each side is clustered into 1+ targets rather
            # than averaged into one point that might reach none of them.
            lat_cens = [
                (sum(p[0] for p in l["corners_m"]) / len(l["corners_m"]),
                 sum(p[1] for p in l["corners_m"]) / len(l["corners_m"]))
                for l in comm.get("latrines", [])
            ]
            for side in (
                [c for c in lat_cens if c[1] < ocy],   # south
                [c for c in lat_cens if c[1] > ocy],   # north
            ):
                for ltx, lty in _cluster_targets(side, radius=12.0):
                    if _dist2((ocx, ocy), (ltx, lty)) > 1:
                        routed = _route_segment(parcel, (ocx, ocy), (ltx, lty), obstacles)
                        if routed:
                            footpath_segs.append({"pts_m": routed, "_node": comm_name})

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
    for comm_name, (cx2, cy2) in comm_pts.items():
        _add_node(comm_name, (cx2, cy2))
        seg = next((s for s in footpath_segs if s.get("_node") == comm_name), None)
        if seg:
            nearest = min(all_settled, key=lambda n: _dist2(node_pos[n], (cx2, cy2)))
            _add_edge(comm_name, nearest)
        else:
            _add_edge(comm_name, _nearest_main(cx2, cy2))

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
