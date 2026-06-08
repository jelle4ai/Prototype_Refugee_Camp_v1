"""
Scoring engine for refugee camp layouts.

score_layout(layout, site, requirements) -> dict

Total (0-100) is a weighted sum:
  shelter_distribution ×4, site_utilisation ×4, overlap_avoidance ×2,
  all other components ×1  (max weighted score = 170, scaled to 100).
"""
from math import sqrt
from shapely.geometry import Polygon as _Poly, Point as _Pt
from shapely.ops import unary_union


_FAC_KEYS = [
    "health_post", "water_points", "food_distribution",
    "community_space", "administrative_area", "schools",
    "worship_facility", "toilets", "washing_facilities",
]

# Facilities placed in deliberate locations (not grid-spread).
# Used for site_utilisation so that grid-spread latrines/schools don't
# artificially fill all zones and inflate the score.
_SITING_KEYS = [
    "health_post", "food_distribution", "community_space",
    "administrative_area", "worship_facility",
]

_REQ_TO_FAC = {
    "health_posts":             "health_post",
    "water_points":             "water_points",
    "food_distribution_points": "food_distribution",
    "schools":                  "schools",
    "community_space":          "community_space",
    "administrative_area":      "administrative_area",
    "worship_facility":         "worship_facility",
    "toilets":                  "toilets",
    "washing_facilities":       "washing_facilities",
}

# Component weights for the total (sum = 17).
_WEIGHTS = {
    "shelter_distribution":    4,
    "water_coverage":          1,
    "sanitation_distribution": 1,
    "school_accessibility":    1,
    "road_connectivity":       1,
    "constraint_compliance":   1,
    "site_utilisation":        4,
    "entrance_quality":        1,
    "overlap_avoidance":       2,
    "expansion_buffer":        1,
}


def _centroid(corners: list) -> tuple[float, float]:
    x = sum(p[0] for p in corners) / len(corners)
    y = sum(p[1] for p in corners) / len(corners)
    return x, y


def _dist(a: tuple, b: tuple) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _polys_from(items: list) -> list:
    out = []
    for item in items:
        try:
            out.append(_Poly(item["corners_m"]))
        except Exception:
            pass
    return out


def _all_polys(shelters: list, facilities: dict) -> list:
    polys = _polys_from(shelters)
    for key in _FAC_KEYS:
        polys.extend(_polys_from(facilities.get(key, [])))
    return polys


def _compute_grid_fill(centroids: list,
                       parcel: _Poly) -> tuple[float, int, int]:
    """
    Count how many cells in a 3×3 grid over the parcel contain at least
    one centroid.  Returns (fraction, occupied_count, valid_count).
    """
    minx, miny, maxx, maxy = parcel.bounds
    dx = (maxx - minx) / 3
    dy = (maxy - miny) / 3
    occupied: set = set()
    valid = 0
    for ci in range(3):
        for cj in range(3):
            cell = _Poly([
                (minx + ci * dx,       miny + cj * dy),
                (minx + (ci + 1) * dx, miny + cj * dy),
                (minx + (ci + 1) * dx, miny + (cj + 1) * dy),
                (minx + ci * dx,       miny + (cj + 1) * dy),
            ])
            if parcel.intersects(cell):
                valid += 1
                if any(cell.contains(_Pt(cx, cy)) for cx, cy in centroids):
                    occupied.add((ci, cj))
    if valid == 0:
        return 0.0, 0, 0
    return len(occupied) / valid, len(occupied), valid


# ── Component scorers ─────────────────────────────────────────────────────────

def _c1_shelter_distribution(shelters: list, parcel: _Poly,
                              sh_gf: float, sh_occ: int,
                              sh_valid: int) -> tuple[int, str]:
    """Are shelters spread across the buildable area?"""
    if not shelters:
        return 0, "No shelters placed"
    pts = round(sh_gf * 10)
    label = "good" if sh_gf >= 0.7 else "moderate" if sh_gf >= 0.4 else "poor"
    return pts, (
        f"Shelters in {sh_occ}/{sh_valid} grid zones — {label} spread across parcel"
    )


def _c2_water_coverage(shelters: list, water_pts: list) -> tuple[int, str]:
    """Fraction of shelters within 500 m of a water point (WS3)."""
    if not shelters:
        return 10, "No shelters to cover (WS3 n/a)"
    if not water_pts:
        return 0, "No water points placed — WS3 violated"
    wp_cens = [_centroid(w["corners_m"]) for w in water_pts]
    covered = sum(
        1 for s in shelters
        if any(_dist(_centroid(s["corners_m"]), wc) <= 500 for wc in wp_cens)
    )
    frac = covered / len(shelters)
    pts = round(frac * 10)
    return pts, (
        f"{covered}/{len(shelters)} shelters within 500 m of a water point "
        f"({100 * frac:.0f}% — WS3)"
    )


def _c3_sanitation_distribution(shelters: list, latrines: list,
                                 parcel: _Poly) -> tuple[int, str]:
    """Fraction within 50 m of latrines; spread bonus (SA3/SA9)."""
    if not shelters:
        return 10, "No shelters to evaluate (SA3 n/a)"
    if not latrines:
        return 0, "No latrine blocks placed — SA3 violated"
    lat_cens = [_centroid(l["corners_m"]) for l in latrines]
    covered = sum(
        1 for s in shelters
        if any(_dist(_centroid(s["corners_m"]), lc) <= 50 for lc in lat_cens)
    )
    frac_cov = covered / len(shelters)
    if len(lat_cens) > 1:
        xs = [p[0] for p in lat_cens]
        ys = [p[1] for p in lat_cens]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        std = sqrt(sum((x - mx) ** 2 + (y - my) ** 2 for x, y in lat_cens) / len(lat_cens))
        bx0, by0, bx1, by1 = parcel.bounds
        diag = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2)
        spread = min(1.0, std / max(1.0, diag * 0.20))
    else:
        spread = 0.2
    combined = 0.7 * frac_cov + 0.3 * spread
    pts = round(combined * 10)
    spread_label = ("well spread" if spread >= 0.6
                    else "loosely spread" if spread >= 0.3 else "clustered")
    return pts, (
        f"{covered}/{len(shelters)} shelters within 50 m of latrines; "
        f"blocks {spread_label} (SA3/SA9)"
    )


def _c4_school_accessibility(shelters: list, schools: list,
                               requirements: dict) -> tuple[int, str]:
    """Fraction of shelters within 1 km of a school (ED3)."""
    sc_req = requirements.get("schools", {}).get("count", 0)
    if sc_req == 0:
        return 10, "No schools required — ED3 n/a"
    if not schools:
        return 0, f"{sc_req} school(s) required but none placed — ED3 violated"
    if not shelters:
        return 10, "No shelters to evaluate"
    sc_cens = [_centroid(s["corners_m"]) for s in schools]
    covered = sum(
        1 for sh in shelters
        if any(_dist(_centroid(sh["corners_m"]), sc) <= 1000 for sc in sc_cens)
    )
    frac = covered / len(shelters)
    pts = round(frac * 10)
    return pts, (
        f"{covered}/{len(shelters)} shelters within 1 km of a school "
        f"({100 * frac:.0f}% — ED3)"
    )


def _c5_road_connectivity(roads: dict) -> tuple[int, str]:
    """Single connected road graph reaching all facilities and shelter bands (PA3/PA4)."""
    if not roads:
        return 5, "Road data unavailable — scored conservatively"
    if roads.get("connected", False):
        return 10, "Road network fully connected — PA3/PA4 satisfied"
    stranded = roads.get("stranded", [])
    n = len(stranded)
    pts = max(0, 8 - min(8, n * 2))
    sample = ", ".join(stranded[:3]) + (f" (+{n - 3} more)" if n > 3 else "")
    return pts, f"Network not fully connected; {n} element(s) stranded: {sample}"


def _c6_constraint_compliance(shelter_result: dict,
                               facilities: dict,
                               requirements: dict) -> tuple[int, str]:
    """Hard count checks and key spacing rules (SH6, SA4)."""
    checks_pass = 0
    checks_total = 0
    fac_status = facilities.get("status", {})

    for req_key, fac_key in _REQ_TO_FAC.items():
        req_count = requirements.get(req_key, {}).get("count", 0)
        if req_count == 0:
            continue
        checks_total += 1
        if fac_status.get(fac_key, {}).get("placed", 0) >= req_count:
            checks_pass += 1

    sh_req = shelter_result.get("required", 0)
    if sh_req > 0:
        checks_total += 1
        if shelter_result.get("placed", 0) >= sh_req:
            checks_pass += 1

    # SH6: shelter spacing ≥ 2 m — spot-check first 8 shelters
    sh_list = shelter_result.get("shelters", [])
    if len(sh_list) >= 2:
        checks_total += 1
        sample = [_Poly(s["corners_m"]) for s in sh_list[:8]]
        ok = all(
            sample[i].distance(sample[j]) >= 1.9
            for i in range(len(sample))
            for j in range(i + 1, len(sample))
        )
        if ok:
            checks_pass += 1

    # SA4: latrines ≥ 6 m from shelters — centroid proxy (cen-cen ≥ 10 m)
    latrines = facilities.get("toilets", [])
    if sh_list and latrines:
        checks_total += 1
        sh_cens = [_centroid(s["corners_m"]) for s in sh_list[:15]]
        lt_cens = [_centroid(l["corners_m"]) for l in latrines[:8]]
        min_d = min(_dist(sc, lc) for sc in sh_cens for lc in lt_cens)
        if min_d >= 10.0:   # centroid gap ≥ 10 m ≈ edge gap ≥ 6 m for typical sizes
            checks_pass += 1

    if checks_total == 0:
        return 5, "Insufficient data to evaluate constraints"
    frac = checks_pass / checks_total
    pts = round(frac * 10)
    return pts, (
        f"{checks_pass}/{checks_total} checks pass "
        f"(facility counts, SH6 shelter spacing, SA4 latrine clearance)"
    )


def _c7_site_utilisation(shelters: list, facilities: dict,
                          siting_gf: float, siting_occ: int,
                          sh_valid: int) -> tuple[int, str]:
    """How evenly shelters and deliberately-placed facilities fill the parcel.

    Uses the same 3×3 grid as shelter_distribution, counting zones that
    contain at least one shelter centroid or a deliberately-sited facility
    centroid (health post, food, community space, admin, worship).
    Grid-spread elements (latrines, water points, schools) are excluded so
    they cannot artificially fill zones that are genuinely empty (SH10).
    """
    if not shelters and not any(facilities.get(k) for k in _SITING_KEYS):
        return 0, "No elements placed — parcel entirely unused"
    pts = round(siting_gf * 10)
    label = ("good" if siting_gf >= 0.7
             else "moderate — several zones empty" if siting_gf >= 0.4
             else "poor — most of parcel unused")
    return pts, (
        f"{siting_occ}/{sh_valid} zones contain shelters/key facilities — "
        f"{label} (SH10)"
    )


def _c8_entrance_quality(site: dict, roads: dict) -> tuple[int, str]:
    """Entrance on boundary near external road, connected to main road."""
    pts = 0
    notes: list[str] = []
    if site.get("roads_m"):
        pts += 3
        notes.append("external roads present near entrance")
    else:
        notes.append("no external roads detected in site data")
    if roads.get("main_road"):
        pts += 4
        notes.append("main road (PA1) links entrance into camp")
    else:
        notes.append("no main road from entrance")
    if roads.get("connected"):
        pts += 3
        notes.append("network fully connected")
    else:
        notes.append("network has stranded segments")
    return pts, "; ".join(notes)


def _c9_overlap_avoidance(shelters: list, facilities: dict) -> tuple[int, str]:
    """Penalise facilities/shelters whose footprints overlap."""
    polys = _all_polys(shelters, facilities)
    if len(polys) < 2:
        return 10, "Fewer than 2 elements — no overlaps possible"
    total_area = sum(p.area for p in polys)
    if total_area < 0.01:
        return 10, "Negligible footprints — no overlaps"
    union_area = unary_union(polys).area
    overlap_area = max(0.0, total_area - union_area)
    if overlap_area < 0.5:
        return 10, "No significant footprint overlaps detected (< 0.5 m²)"
    overlap_frac = overlap_area / total_area
    # 5 % overlap → 0 pts; linear (stricter than 10 % to catch clustered facilities)
    pts = max(0, round((1.0 - overlap_frac / 0.05) * 10))
    return pts, (
        f"{overlap_area:.1f} m² overlapping area "
        f"({100 * overlap_frac:.1f}% of total footprint)"
    )


def _c10_expansion_buffer(shelters: list, facilities: dict,
                           parcel: _Poly, sh_gf: float) -> tuple[int, str]:
    """Reward expansion space only when the camp is already well-spread (SH10).

    A large empty area from poor placement is NOT a buffer — it is the same
    wasted space that shelter_distribution and site_utilisation already
    penalise.  The score scales with how well shelters fill the parcel first,
    then with how much contiguous free area remains.
    """
    polys = _all_polys(shelters, facilities)
    if not polys:
        return 5, "No elements placed — cannot evaluate (scored conservatively)"

    used = unary_union(polys).buffer(3.0)
    leftover = parcel.difference(used)
    if leftover.is_empty:
        return 0, "No free space remaining — no expansion possible"

    largest = (
        max(leftover.geoms, key=lambda g: g.area)
        if hasattr(leftover, "geoms") else leftover
    )
    leftover_frac = largest.area / parcel.area

    if sh_gf < 0.40:
        # Shelters barely spread — emptiness = incomplete placement, not reserve
        pts = max(0, round(sh_gf * 5))   # 0–2 pts
        label = (
            f"shelters occupy only {round(sh_gf * 9)}/9 zones; "
            f"free space is unplanned emptiness, not a buffer"
        )
    elif sh_gf < 0.70:
        # Partial spread — give limited credit, weighted toward camp quality
        quality_pts   = round((sh_gf - 0.40) / 0.30 * 6)          # 0–6
        leftover_pts  = round(min(leftover_frac, 0.20) / 0.20 * 2) # 0–2
        pts = quality_pts + leftover_pts                            # 0–8
        label = "partial spread — free area is partly unplanned"
    else:
        # Camp well-spread: score based on leftover size
        leftover_pts = round(min(leftover_frac, 0.30) / 0.30 * 10)
        pts = max(5, leftover_pts)
        label = ("good planned expansion reserve" if leftover_frac >= 0.15
                 else "limited expansion space — camp is dense")

    return pts, (
        f"Largest free area: {largest.area:.0f} m² "
        f"({100 * leftover_frac:.0f}% of parcel), "
        f"{round(sh_gf * 9)}/9 shelter zones occupied — {label} (SH10)"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def score_layout(layout: dict, site: dict, requirements: dict) -> dict:
    """
    Score a camp layout on ten 0–10 components, returning a 0–100 total.

    Parameters
    ----------
    layout : {"shelter_result": ..., "facilities": ..., "roads": ...}
    site   : site dict with parcel_polygon_m, roads_m, origin_lat, origin_lon
    requirements : output of compute_requirements()

    Returns
    -------
    {"total": int, "components": [{"name", "points", "max", "weight",
    "explanation"}, ...]}

    Scoring weights (see _WEIGHTS): shelter_distribution ×4,
    site_utilisation ×4, overlap_avoidance ×2, all others ×1.
    Total = round(weighted_sum / 170 * 100).
    """
    shelter_result = layout.get("shelter_result", {})
    facilities     = layout.get("facilities", {})
    roads          = layout.get("roads", {})
    shelters       = shelter_result.get("shelters", [])
    parcel         = _Poly(site["parcel_polygon_m"])

    # ── Pre-compute grid fills used by multiple components ────────────────────
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    siting_cens = list(sh_cens)
    for k in _SITING_KEYS:
        for item in facilities.get(k, []):
            try:
                siting_cens.append(_centroid(item["corners_m"]))
            except Exception:
                pass

    # Shelter-only grid fill → drives shelter_distribution and expansion_buffer
    sh_gf, sh_occ, sh_valid = _compute_grid_fill(sh_cens, parcel)
    # Shelter + deliberately-sited facilities → drives site_utilisation
    sit_gf, sit_occ, _ = _compute_grid_fill(siting_cens, parcel)

    scorers = [
        ("shelter_distribution",    lambda: _c1_shelter_distribution(
                                        shelters, parcel, sh_gf, sh_occ, sh_valid)),
        ("water_coverage",          lambda: _c2_water_coverage(
                                        shelters, facilities.get("water_points", []))),
        ("sanitation_distribution", lambda: _c3_sanitation_distribution(
                                        shelters, facilities.get("toilets", []), parcel)),
        ("school_accessibility",    lambda: _c4_school_accessibility(
                                        shelters, facilities.get("schools", []), requirements)),
        ("road_connectivity",       lambda: _c5_road_connectivity(roads)),
        ("constraint_compliance",   lambda: _c6_constraint_compliance(
                                        shelter_result, facilities, requirements)),
        ("site_utilisation",        lambda: _c7_site_utilisation(
                                        shelters, facilities, sit_gf, sit_occ, sh_valid)),
        ("entrance_quality",        lambda: _c8_entrance_quality(site, roads)),
        ("overlap_avoidance",       lambda: _c9_overlap_avoidance(shelters, facilities)),
        ("expansion_buffer",        lambda: _c10_expansion_buffer(
                                        shelters, facilities, parcel, sh_gf)),
    ]

    components = []
    weighted_sum = 0
    max_weighted = sum(_WEIGHTS.values()) * 10  # = 170

    for name, fn in scorers:
        pts, expl = fn()
        pts = max(0, min(10, int(round(pts))))
        w = _WEIGHTS[name]
        weighted_sum += pts * w
        components.append({
            "name":        name,
            "points":      pts,
            "max":         10,
            "weight":      w,
            "explanation": expl,
        })

    total = round(weighted_sum / max_weighted * 100)
    return {"total": total, "components": components}
