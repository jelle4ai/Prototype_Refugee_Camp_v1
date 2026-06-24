"""
Scoring engine for refugee camp layouts.

Public API
----------
compliance_gate(layout, site, requirements) -> dict
  Hard pass/fail check of all binding constraints.
  Returns {"pass": bool, "checks": [{"name", "pass", "detail"}, ...]}.
  A layout that fails any check is non-compliant regardless of quality score.

score_layout(layout, site, requirements) -> dict
  Returns {"gate": gate_result, "quality": quality_result,
           "total": int, "components": list}
  where "total" and "components" mirror quality_result for backward compatibility.

Quality score (0–100) is a weighted sum of 9 components (constraint_compliance
has been removed — it is now the gate):
  shelter_distribution ×4, site_utilisation ×4, overlap_avoidance ×2,
  all others ×1  (max weighted = 160, scaled to 100).
"""
from math import sqrt
from shapely.geometry import Polygon as _Poly, Point as _Pt
from shapely.ops import unary_union


_FAC_KEYS = [
    "health_post", "water_points", "food_distribution",
    "community_space", "administrative_area", "schools",
    "worship_facility", "toilets", "washing_facilities",
]

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

# Quality weights (constraint_compliance removed — now in compliance_gate).
# Sum = 16, max weighted = 160.
_WEIGHTS = {
    "shelter_distribution":    4,
    "water_coverage":          1,
    "sanitation_distribution": 1,
    "school_accessibility":    1,
    "road_connectivity":       1,
    "site_utilisation":        4,
    "entrance_quality":        1,
    "overlap_avoidance":       2,
    "expansion_buffer":        1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Shared geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _centroid(corners: list) -> tuple[float, float]:
    return (sum(p[0] for p in corners) / len(corners),
            sum(p[1] for p in corners) / len(corners))


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


def _compute_grid_fill(centroids: list, parcel: _Poly) -> tuple[float, int, int]:
    """Fraction of 3×3 grid cells containing ≥1 centroid."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Compliance gate  (hard pass/fail, separate from quality)
# ─────────────────────────────────────────────────────────────────────────────

def compliance_gate(layout: dict, site: dict, requirements: dict) -> dict:
    """
    Hard constraint gate.  Each check is independent pass/fail.
    A layout that fails any check is flagged non-compliant regardless of
    quality score.  Thresholds match Sphere / UNHCR standards.

    Checks performed:
      1. All required facility counts present
      2. No footprint overlaps (>1 m² tolerance)
      3. WS3: ≥95 % of shelters within 500 m of a water point
      4. SA3: ≥80 % of shelters within 50 m of a latrine
      5. ED3: ≥95 % of shelters within 1 km of a school (if schools required)
      6. SH6: shelter spacing ≥2 m (spot-check first 8)
      7. SA4: latrines ≥6 m from shelters (centroid proxy, first 15/8)
      8. PA3: road network fully connected
    """
    shelter_result = layout.get("shelter_result", {})
    facilities     = layout.get("facilities", {})
    roads          = layout.get("roads", {})
    shelters       = shelter_result.get("shelters", [])
    fac_status     = facilities.get("status", {})
    checks: list[dict] = []

    def _check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "pass": passed, "detail": detail})

    # ── 1. Facility counts ────────────────────────────────────────────────────
    for req_key, fac_key in _REQ_TO_FAC.items():
        req_n = requirements.get(req_key, {}).get("count", 0)
        if req_n == 0:
            continue
        placed = fac_status.get(fac_key, {}).get("placed", 0)
        _check(f"Count: {req_key.replace('_', ' ')}",
               placed >= req_n, f"{placed}/{req_n} placed")

    sh_req = shelter_result.get("required", 0)
    if sh_req > 0:
        sh_placed = shelter_result.get("placed", 0)
        _check("Count: shelter units", sh_placed >= sh_req,
               f"{sh_placed}/{sh_req} placed")

    # ── 2. No footprint overlaps (>1 m² tolerance) ───────────────────────────
    polys = _all_polys(shelters, facilities)
    if len(polys) >= 2:
        total_area = sum(p.area for p in polys)
        union_area = unary_union(polys).area
        overlap    = max(0.0, total_area - union_area)
        _check("No footprint overlaps", overlap <= 1.0,
               f"{overlap:.1f} m² total overlap")

    # ── 3. WS3: ≥95 % of shelters within 500 m of a water point ─────────────
    if shelters:
        wp = [_centroid(i["corners_m"]) for i in facilities.get("water_points", [])]
        if wp:
            sh_cens = [_centroid(s["corners_m"]) for s in shelters]
            n_cov   = sum(1 for s in sh_cens if any(_dist(s, w) <= 500 for w in wp))
            frac    = n_cov / len(shelters)
            _check("WS3: water within 500 m", frac >= 0.95,
                   f"{n_cov}/{len(shelters)} ({100*frac:.0f}%)")
        else:
            _check("WS3: water within 500 m", False, "no water points placed")

    # ── 4. SA3: ≥80 % of shelters within 50 m of a latrine ──────────────────
    if shelters:
        lt = [_centroid(i["corners_m"]) for i in facilities.get("toilets", [])]
        if lt:
            sh_cens = [_centroid(s["corners_m"]) for s in shelters]
            n_cov   = sum(1 for s in sh_cens if any(_dist(s, l) <= 50 for l in lt))
            frac    = n_cov / len(shelters)
            _check("SA3: sanitation within 50 m", frac >= 0.80,
                   f"{n_cov}/{len(shelters)} ({100*frac:.0f}%)")
        else:
            _check("SA3: sanitation within 50 m", False, "no latrines placed")

    # ── 5. ED3: ≥95 % of shelters within 1 km of a school ───────────────────
    sc_req = requirements.get("schools", {}).get("count", 0)
    if sc_req > 0 and shelters:
        sc = [_centroid(i["corners_m"]) for i in facilities.get("schools", [])]
        if sc:
            sh_cens = [_centroid(s["corners_m"]) for s in shelters]
            n_cov   = sum(1 for s in sh_cens if any(_dist(s, c) <= 1000 for c in sc))
            frac    = n_cov / len(shelters)
            _check("ED3: school within 1 km", frac >= 0.95,
                   f"{n_cov}/{len(shelters)} ({100*frac:.0f}%)")
        else:
            _check("ED3: school within 1 km", False, "no schools placed")

    # ── 6. SH6: shelter spacing ≥2 m (spot-check first 8) ───────────────────
    if len(shelters) >= 2:
        sample = [_Poly(s["corners_m"]) for s in shelters[:8]]
        ok     = all(sample[i].distance(sample[j]) >= 1.9
                     for i in range(len(sample))
                     for j in range(i + 1, len(sample)))
        _check("SH6: shelter spacing ≥2 m", ok, "spot-checked first 8 shelters")

    # ── 7. SA4: latrines ≥6 m from shelters (centroid proxy) ────────────────
    latrines = facilities.get("toilets", [])
    if shelters and latrines:
        sh_cens = [_centroid(s["corners_m"]) for s in shelters[:15]]
        lt_cens = [_centroid(l["corners_m"]) for l in latrines[:8]]
        min_d   = min(_dist(sc, lc) for sc in sh_cens for lc in lt_cens)
        _check("SA4: latrines ≥6 m from shelters", min_d >= 6.0,
               f"nearest centroid pair: {min_d:.1f} m")

    # ── 8. PA3: road network fully connected ─────────────────────────────────
    if roads:
        connected = roads.get("connected", False)
        stranded  = roads.get("stranded", [])
        _check("PA3: road network connected", connected,
               "fully connected" if connected else f"{len(stranded)} stranded node(s)")

    return {"pass": all(c["pass"] for c in checks), "checks": checks}


# ─────────────────────────────────────────────────────────────────────────────
# Quality score components  (9 components, constraint_compliance removed)
# ─────────────────────────────────────────────────────────────────────────────

def _c1_shelter_distribution(shelters, parcel, sh_gf, sh_occ, sh_valid):
    if not shelters:
        return 0, "No shelters placed"
    pts   = round(sh_gf * 10)
    label = "good" if sh_gf >= 0.7 else "moderate" if sh_gf >= 0.4 else "poor"
    return pts, f"Shelters in {sh_occ}/{sh_valid} grid zones — {label} spread"


def _c2_water_coverage(shelters, water_pts):
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
    return round(frac * 10), (
        f"{covered}/{len(shelters)} shelters within 500 m of a water point "
        f"({100*frac:.0f}% — WS3)"
    )


def _c3_sanitation_distribution(shelters, latrines, parcel):
    if not shelters:
        return 10, "No shelters to evaluate (SA3 n/a)"
    if not latrines:
        return 0, "No latrine blocks placed — SA3 violated"
    lat_cens = [_centroid(l["corners_m"]) for l in latrines]
    covered  = sum(
        1 for s in shelters
        if any(_dist(_centroid(s["corners_m"]), lc) <= 50 for lc in lat_cens)
    )
    frac_cov = covered / len(shelters)
    if len(lat_cens) > 1:
        xs = [p[0] for p in lat_cens]
        ys = [p[1] for p in lat_cens]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        std    = sqrt(sum((x - mx) ** 2 + (y - my) ** 2
                          for x, y in lat_cens) / len(lat_cens))
        bx0, by0, bx1, by1 = parcel.bounds
        diag  = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2)
        spread = min(1.0, std / max(1.0, diag * 0.20))
    else:
        spread = 0.2
    combined = 0.7 * frac_cov + 0.3 * spread
    label    = ("well spread" if spread >= 0.6
                else "loosely spread" if spread >= 0.3 else "clustered")
    return round(combined * 10), (
        f"{covered}/{len(shelters)} shelters within 50 m of latrines; "
        f"blocks {label} (SA3/SA9)"
    )


def _c4_school_accessibility(shelters, schools, requirements):
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
    return round(frac * 10), (
        f"{covered}/{len(shelters)} shelters within 1 km of a school "
        f"({100*frac:.0f}% — ED3)"
    )


def _c5_road_connectivity(roads):
    if not roads:
        return 5, "Road data unavailable — scored conservatively"
    if roads.get("connected", False):
        return 10, "Road network fully connected — PA3/PA4 satisfied"
    stranded = roads.get("stranded", [])
    n   = len(stranded)
    pts = max(0, 8 - min(8, n * 2))
    sample = ", ".join(stranded[:3]) + (f" (+{n-3} more)" if n > 3 else "")
    return pts, f"Network not fully connected; {n} stranded: {sample}"


def _c6_site_utilisation(shelters, facilities, siting_gf, siting_occ, sh_valid):
    if not shelters and not any(facilities.get(k) for k in _SITING_KEYS):
        return 0, "No elements placed — parcel entirely unused"
    pts   = round(siting_gf * 10)
    label = ("good" if siting_gf >= 0.7
             else "moderate — several zones empty" if siting_gf >= 0.4
             else "poor — most of parcel unused")
    return pts, f"{siting_occ}/{sh_valid} zones contain shelters/key facilities — {label} (SH10)"


def _c7_entrance_quality(site, roads):
    pts   = 0
    notes: list[str] = []
    if site.get("roads_m"):
        pts += 3
        notes.append("external roads present near entrance")
    else:
        notes.append("no external roads detected")
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


def _c8_overlap_avoidance(shelters, facilities):
    polys = _all_polys(shelters, facilities)
    if len(polys) < 2:
        return 10, "Fewer than 2 elements — no overlaps possible"
    total_area  = sum(p.area for p in polys)
    if total_area < 0.01:
        return 10, "Negligible footprints — no overlaps"
    union_area  = unary_union(polys).area
    overlap_area = max(0.0, total_area - union_area)
    if overlap_area < 0.5:
        return 10, "No significant footprint overlaps (< 0.5 m²)"
    overlap_frac = overlap_area / total_area
    pts = max(0, round((1.0 - overlap_frac / 0.05) * 10))
    return pts, (
        f"{overlap_area:.1f} m² overlapping "
        f"({100*overlap_frac:.1f}% of total footprint)"
    )


def _c9_expansion_buffer(shelters, facilities, parcel, sh_gf):
    polys = _all_polys(shelters, facilities)
    if not polys:
        return 5, "No elements placed — scored conservatively"
    used      = unary_union(polys).buffer(3.0)
    leftover  = parcel.difference(used)
    if leftover.is_empty:
        return 0, "No free space remaining"
    largest = (max(leftover.geoms, key=lambda g: g.area)
               if hasattr(leftover, "geoms") else leftover)
    leftover_frac = largest.area / parcel.area
    if sh_gf < 0.40:
        pts   = max(0, round(sh_gf * 5))
        label = "emptiness from incomplete placement, not a usable buffer"
    elif sh_gf < 0.70:
        q_pts = round((sh_gf - 0.40) / 0.30 * 6)
        l_pts = round(min(leftover_frac, 0.20) / 0.20 * 2)
        pts   = q_pts + l_pts
        label = "partial spread — free area partly unplanned"
    else:
        l_pts = round(min(leftover_frac, 0.30) / 0.30 * 10)
        pts   = max(5, l_pts)
        label = ("good planned expansion reserve" if leftover_frac >= 0.15
                 else "limited expansion space — camp is dense")
    return pts, (
        f"Largest free area: {largest.area:.0f} m² "
        f"({100*leftover_frac:.0f}% of parcel) — {label} (SH10)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Appendix E quality components (new — not yet wired into score_layout)
# ─────────────────────────────────────────────────────────────────────────────

def _c1_health_post_centrality(shelters, health_posts, parcel):
    """Component 1 (weight 7): how central the health post is to all shelters (HE3)."""
    if not shelters:
        return 10, "No shelters placed (N/A)"
    if not health_posts:
        return 0, "No health post placed — HE3"
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    shelter_cx = sum(p[0] for p in sh_cens) / len(sh_cens)
    shelter_cy = sum(p[1] for p in sh_cens) / len(sh_cens)
    hp_cens = [_centroid(h["corners_m"]) for h in health_posts]
    hp_dist = min(_dist((shelter_cx, shelter_cy), hc) for hc in hp_cens)
    bx0, by0, bx1, by1 = parcel.bounds
    half_diag = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2) / 2
    score = max(0, round((1 - hp_dist / max(1.0, half_diag)) * 10))
    label = "central" if score >= 8 else "moderate" if score >= 5 else "peripheral"
    return score, (
        f"Health post {hp_dist:.0f} m from shelter centroid "
        f"(half-diagonal {half_diag:.0f} m) — {label} (HE3)"
    )


def _c2_water_quality(shelters, water_pts, parcel):
    """Component 2 (weight 6): comfort margin below WS3 (500 m) + even spread (WS6)."""
    if not shelters:
        return 10, "No shelters (N/A)"
    if not water_pts:
        return 0, "No water points placed — WS3/WS6"
    wp_cens = [_centroid(w["corners_m"]) for w in water_pts]
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    dists   = [min(_dist(sc, wc) for wc in wp_cens) for sc in sh_cens]
    mean_comfort  = sum(max(0.0, 500 - d) for d in dists) / len(dists)
    comfort_score = mean_comfort / 500 * 10
    gf, occ, valid = _compute_grid_fill(wp_cens, parcel)
    spread_score   = gf * 10
    sub = max(0, min(10, round(0.6 * comfort_score + 0.4 * spread_score)))
    return sub, (
        f"Mean comfort margin {mean_comfort:.0f} m below WS3 (500 m); "
        f"water points in {occ}/{valid} grid zones (WS3/WS6)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public scoring entry point
# ─────────────────────────────────────────────────────────────────────────────

def score_layout(layout: dict, site: dict, requirements: dict) -> dict:
    """
    Score a camp layout.

    Returns
    -------
    {
      "gate":       compliance_gate result (pass/fail + check list),
      "quality":    {"total": int, "components": [...]},
      "total":      int   # alias for quality["total"] — backward compat
      "components": list  # alias for quality["components"] — backward compat
    }

    Quality is 9 weighted components (0–100, max weighted = 160).
    constraint_compliance has been removed from quality; it is now the gate.
    """
    shelter_result = layout.get("shelter_result", {})
    facilities     = layout.get("facilities", {})
    roads          = layout.get("roads", {})
    shelters       = shelter_result.get("shelters", [])
    parcel         = _Poly(site["parcel_polygon_m"])

    # Pre-compute grid fills shared by multiple components
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    siting_cens = list(sh_cens)
    for k in _SITING_KEYS:
        for item in facilities.get(k, []):
            try:
                siting_cens.append(_centroid(item["corners_m"]))
            except Exception:
                pass

    sh_gf,  sh_occ,  sh_valid = _compute_grid_fill(sh_cens, parcel)
    sit_gf, sit_occ, _        = _compute_grid_fill(siting_cens, parcel)

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
        ("site_utilisation",        lambda: _c6_site_utilisation(
                                        shelters, facilities, sit_gf, sit_occ, sh_valid)),
        ("entrance_quality",        lambda: _c7_entrance_quality(site, roads)),
        ("overlap_avoidance",       lambda: _c8_overlap_avoidance(shelters, facilities)),
        ("expansion_buffer",        lambda: _c9_expansion_buffer(
                                        shelters, facilities, parcel, sh_gf)),
    ]

    components   = []
    weighted_sum = 0
    max_weighted = sum(_WEIGHTS.values()) * 10  # = 160

    for name, fn in scorers:
        pts, expl = fn()
        pts = max(0, min(10, int(round(pts))))
        w   = _WEIGHTS[name]
        weighted_sum += pts * w
        components.append({
            "name":        name,
            "points":      pts,
            "max":         10,
            "weight":      w,
            "explanation": expl,
        })

    total   = round(weighted_sum / max_weighted * 100)
    gate    = compliance_gate(layout, site, requirements)
    quality = {"total": total, "components": components}

    return {
        "gate":       gate,
        "quality":    quality,
        # Backward-compatible aliases
        "total":      total,
        "components": components,
    }
