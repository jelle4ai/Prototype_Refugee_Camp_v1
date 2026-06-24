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

Quality score (0-100) is a weighted sum of 9 Appendix E components.
Weights sum to 34; max weighted = 340; total = weighted_sum / 340 * 100.

  health_post        x7   (HE3: centrality of health post to shelter centroid)
  water_quality      x6   (WS3 comfort margin + WS6 spread)
  food_distribution  x5   (FD3 proximity relative to site + FD4 capacity)
  latrine_quality    x4   (SA3 comfort margin + SA9 spread)
  school_quality     x3   (ED3 comfort margin + ED5 spread)
  equity             x3   (P90 worst-served across water/sanitation/health)
  spatial_quality    x3   (Appendix F community completeness + open space)
  road_network       x2   (PA3 connectivity + PA4 footpaths + PA6 hierarchy)
  land_use           x1   (share of buildable area sensibly used)

The compliance gate is entirely separate and unchanged.
Overlap avoidance, entrance quality, and expansion buffer were removed from
the quality score — they are compliance-gate items, not quality gradients.
"""
from math import sqrt, ceil
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

# Appendix E quality weights. Sum = 34, max weighted = 340, scaled to 0-100.
_WEIGHTS = {
    "health_post":        7,
    "water_quality":      6,
    "food_distribution":  5,
    "latrine_quality":    4,
    "school_quality":     3,
    "equity":             3,
    "spatial_quality":    3,
    "road_network":       2,
    "land_use":           1,
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
# Quality score components — Appendix E (nine components, weights sum to 34)
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


def _c3_food_distribution(shelters, food_dist_pts, parcel):
    """Component 3 (weight 5): proximity to FD (site-relative, FD3) + capacity (FD4)."""
    if not shelters:
        return 10, "No shelters (N/A)"
    if not food_dist_pts:
        return 0, "No food distribution points placed"
    fd_cens = [_centroid(f["corners_m"]) for f in food_dist_pts]
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    avg_d   = sum(min(_dist(sc, fc) for fc in fd_cens) for sc in sh_cens) / len(sh_cens)
    bx0, by0, bx1, by1 = parcel.bounds
    diag = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2)
    prox_score = max(0, round((1 - avg_d / max(1.0, diag)) * 10))
    ratio    = len(shelters) / len(food_dist_pts)
    cap_score = max(0, min(10, round((1 - max(0.0, ratio - 80) / 120) * 10)))
    sub = max(0, min(10, round(0.7 * prox_score + 0.3 * cap_score)))
    return sub, (
        f"Avg shelter-FD distance {avg_d:.0f} m ({avg_d/max(1.0,diag)*100:.0f}% of "
        f"site diagonal); {len(shelters)} shelters per {len(food_dist_pts)} point(s) (FD3/FD4)"
    )


def _c4_latrine_quality(shelters, latrines, parcel):
    """Component 4 (weight 4): comfort margin below SA3 (50 m) + spread across zones (SA9)."""
    if not shelters:
        return 10, "No shelters (N/A)"
    if not latrines:
        return 0, "No latrine blocks placed — SA3/SA9"
    lt_cens = [_centroid(l["corners_m"]) for l in latrines]
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    mean_comfort  = sum(max(0.0, 50 - min(_dist(sc, lc) for lc in lt_cens))
                        for sc in sh_cens) / len(sh_cens)
    comfort_score = mean_comfort / 50 * 10
    if len(lt_cens) == 1:
        spread_score = 2
    else:
        xs = [p[0] for p in lt_cens]
        ys = [p[1] for p in lt_cens]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        std = sqrt(sum((x - mx) ** 2 + (y - my) ** 2
                       for x, y in lt_cens) / len(lt_cens))
        bx0, by0, bx1, by1 = parcel.bounds
        diag = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2)
        spread_score = min(10, round(std / max(1.0, diag * 0.20) * 10))
    sub = max(0, min(10, round(0.7 * comfort_score + 0.3 * spread_score)))
    label = ("well spread" if spread_score >= 7
             else "loosely spread" if spread_score >= 4 else "clustered")
    return sub, (
        f"Mean SA3 comfort {mean_comfort:.1f} m margin; latrines {label} (SA3/SA9)"
    )


def _c5_school_quality(shelters, schools, requirements, parcel):
    """Component 5 (weight 3): capacity adequacy (ED1) + comfort margin (ED3) + separation (ED5)."""
    sc_req = requirements.get("schools", {}).get("count", 0)
    if sc_req == 0:
        return 10, "No schools required (ED3/ED5 N/A)"
    if not schools:
        return 0, f"{sc_req} school(s) required but none placed — ED3"
    if not shelters:
        return 10, "No shelters (N/A)"
    sc_cens = [_centroid(s["corners_m"]) for s in schools]
    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    cap_score = min(10, round(len(schools) / max(1, sc_req) * 10))
    mean_comfort  = sum(max(0.0, 1000 - min(_dist(sc, cc) for cc in sc_cens))
                        for sc in sh_cens) / len(sh_cens)
    comfort_score = mean_comfort / 1000 * 10
    if len(schools) == 1:
        sep_score     = 10
        min_pair_dist = None
        sep_note      = "1 school"
    else:
        min_pair_dist = min(_dist(a, b)
                            for i, a in enumerate(sc_cens)
                            for j, b in enumerate(sc_cens)
                            if j > i)
        sep_score = min(10, round(min_pair_dist / 200 * 10))
        sep_note  = f"min pair dist {min_pair_dist:.0f} m"
    sub = max(0, min(10, round(0.50 * cap_score + 0.35 * comfort_score + 0.15 * sep_score)))
    return sub, (
        f"Capacity {len(schools)}/{sc_req} schools; mean ED3 comfort {mean_comfort:.0f} m; "
        f"{sep_note} (ED1/ED3/ED5)"
    )


def _c6_equity(shelters, water_pts, latrines, health_posts, parcel):
    """Component 6 (weight 3): P90 worst-served protection across water/sanitation/health.

    Uses the 90th-percentile distance (worst 10% of shelters) rather than the single
    worst shelter, making the score robust to genuine perimeter edge cases on irregular
    parcels (Rawlsian basis: protect the worst-off group, not a statistical outlier).
    Water threshold: 500 m (WS3). Sanitation: 50 m (SA3).
    Health: site-relative (parcel half-diagonal), matching component 1.
    """
    if not shelters:
        return 10, "No shelters (N/A)"

    def _p90(dists):
        s   = sorted(dists)
        idx = int(0.9 * len(s))
        return s[min(idx, len(s) - 1)]

    sh_cens = [_centroid(s["corners_m"]) for s in shelters]

    # Water
    if not water_pts:
        equity_water, p90_w = 0.0, float("inf")
    else:
        wp_cens = [_centroid(w["corners_m"]) for w in water_pts]
        dists_w  = [min(_dist(sc, wc) for wc in wp_cens) for sc in sh_cens]
        p90_w    = _p90(dists_w)
        equity_water = max(0.0, 1.0 - p90_w / 500)

    # Sanitation
    if not latrines:
        equity_sanitation, p90_l = 0.0, float("inf")
    else:
        lt_cens = [_centroid(l["corners_m"]) for l in latrines]
        dists_l  = [min(_dist(sc, lc) for lc in lt_cens) for sc in sh_cens]
        p90_l    = _p90(dists_l)
        equity_sanitation = max(0.0, 1.0 - p90_l / 50)

    # Health (site-relative threshold = parcel half-diagonal)
    bx0, by0, bx1, by1 = parcel.bounds
    half_diag = sqrt((bx1 - bx0) ** 2 + (by1 - by0) ** 2) / 2
    if not health_posts:
        equity_health, p90_h = 0.0, float("inf")
    else:
        hp_cens = [_centroid(h["corners_m"]) for h in health_posts]
        dists_h  = [min(_dist(sc, hc) for hc in hp_cens) for sc in sh_cens]
        p90_h    = _p90(dists_h)
        equity_health = max(0.0, 1.0 - p90_h / max(1.0, half_diag))

    mean_equity = (equity_water + equity_sanitation + equity_health) / 3
    sub = max(0, min(10, round(mean_equity * 10)))
    p90_w_str = f"{p90_w:.0f}" if p90_w != float("inf") else "inf"
    p90_l_str = f"{p90_l:.0f}" if p90_l != float("inf") else "inf"
    p90_h_str = f"{p90_h:.0f}" if p90_h != float("inf") else "inf"
    return sub, (
        f"P90 distances — water {p90_w_str} m (vs 500 m), "
        f"latrines {p90_l_str} m (vs 50 m), "
        f"health post {p90_h_str} m (vs {half_diag:.0f} m half-diag); "
        f"equity score protects worst-served 10% (SA3/WS3/HE3)"
    )


def _c7_spatial_quality(shelter_result, parcel):
    """Component 7 (weight 3): community completeness + open-space integrity (Appendix F).

    Measures how well shelters form modular communities (16 families each around
    a shared 16x20 m open space) rather than a bare uniform grid.
    - Completeness: placed communities / required communities.
    - Open-space integrity: mean(min(1, open_poly.area / 320)) per community,
      where 320 m^2 = 16x20 m from Appendix F module geometry.
    """
    shelters    = shelter_result.get("shelters", [])
    communities = shelter_result.get("communities", [])
    if not communities:
        if not shelters:
            return 10, "No shelters (N/A)"
        return 0, "No community structure (Appendix F module not formed)"
    required          = shelter_result.get("required", 0)
    n_required_comms  = max(1, ceil(required / 16))
    placed_comms      = len(communities)
    completeness      = min(1.0, placed_comms / n_required_comms)
    completeness_score = round(completeness * 10)
    open_adequacies = []
    for c in communities:
        if c.get("open_corners"):
            open_area = _Poly(c["open_corners"]).area
        else:
            open_area = 0.0
        open_adequacies.append(min(1.0, open_area / 320.0))
    mean_adequacy = sum(open_adequacies) / len(open_adequacies)
    open_score    = round(mean_adequacy * 10)
    sub = max(0, min(10, round(0.5 * completeness_score + 0.5 * open_score)))
    return sub, (
        f"{placed_comms}/{n_required_comms} communities placed; "
        f"mean open-space {mean_adequacy*320:.0f}/{320} m^2 per community (Appendix F)"
    )


def _c8_road_network(roads, shelter_result):
    """Component 8 (weight 2): connectivity and hierarchy (PA3/PA4/PA6).

    PA3 (5 pts): network fully connected (hard gate already checks this, but
      quality grades partial connectivity by stranded count).
    PA4 (3 pts): footpath coverage — one path per community expected.
    PA6 (2 pts): main road spanning site + secondary roads to facility zones.
    """
    if not roads:
        return 5, "Road data unavailable — scored conservatively"
    required      = shelter_result.get("required", 0)
    required_comms = max(1, ceil(required / 16))
    connected = roads.get("connected", False)
    stranded  = roads.get("stranded", [])
    pa3_pts   = 5 if connected else max(0, 5 - min(5, len(stranded) * 2))
    footpaths = roads.get("footpaths", [])
    fp_ratio  = min(1.0, len(footpaths) / max(1, required_comms))
    pa4_pts   = round(fp_ratio * 3)
    secondary = roads.get("secondary_roads", [])
    main_road = roads.get("main_road", [])
    pa6_pts   = (1 if main_road else 0) + (1 if len(secondary) >= 1 else 0)
    sub = min(10, pa3_pts + pa4_pts + pa6_pts)
    return sub, (
        f"PA3: {'connected' if connected else f'{len(stranded)} stranded'} ({pa3_pts}/5 pts); "
        f"PA4: {len(footpaths)} footpaths ({pa4_pts}/3 pts); "
        f"PA6: main={'yes' if main_road else 'no'}, secondary={len(secondary)} ({pa6_pts}/2 pts)"
    )


def _c9_land_use(shelters, facilities, parcel):
    """Component 9 (weight 1): sensible density without overcrowding (SH10).

    Leftover land is treated as expansion reserve and NOT penalised.
    Score drops only when the built footprint becomes overcrowded.
    Threshold: <= 70% of parcel = sensible (score 10); 70-85% = dense (10->5);
    > 85% = overcrowded (5->0). Sparse layouts score 10 (SH10 expansion reserve).
    """
    polys = _all_polys(shelters, facilities)
    if not polys:
        return 0, "No elements placed — parcel unused"
    used_area   = unary_union(polys).area
    parcel_area = parcel.area
    use_ratio   = used_area / max(1.0, parcel_area)
    if use_ratio <= 0.70:
        score = 10
        label = "spacious — leftover area is expansion reserve (SH10)"
    elif use_ratio <= 0.85:
        score = round(10 - (use_ratio - 0.70) / 0.15 * 5)
        label = "dense — limited expansion reserve"
    else:
        score = round(max(0.0, 5 - (use_ratio - 0.85) / 0.15 * 5))
        label = "overcrowded — insufficient expansion reserve (SH10)"
    score = max(0, min(10, score))
    return score, (
        f"{use_ratio*100:.0f}% of parcel area used ({used_area:.0f}/{parcel_area:.0f} m^2) "
        f"— {label}"
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

    Quality is 9 Appendix E weighted components (0-100).
    Weights sum to 34; max weighted = 340; total = weighted_sum / 340 * 100.
    The compliance gate is separate and unaffected.
    """
    shelter_result = layout.get("shelter_result", {})
    facilities     = layout.get("facilities", {})
    roads          = layout.get("roads", {})
    shelters       = shelter_result.get("shelters", [])
    parcel         = _Poly(site["parcel_polygon_m"])

    scorers = [
        ("health_post",       lambda: _c1_health_post_centrality(
                                  shelters, facilities.get("health_post", []), parcel)),
        ("water_quality",     lambda: _c2_water_quality(
                                  shelters, facilities.get("water_points", []), parcel)),
        ("food_distribution", lambda: _c3_food_distribution(
                                  shelters, facilities.get("food_distribution", []), parcel)),
        ("latrine_quality",   lambda: _c4_latrine_quality(
                                  shelters, facilities.get("toilets", []), parcel)),
        ("school_quality",    lambda: _c5_school_quality(
                                  shelters, facilities.get("schools", []), requirements, parcel)),
        ("equity",            lambda: _c6_equity(
                                  shelters, facilities.get("water_points", []),
                                  facilities.get("toilets", []),
                                  facilities.get("health_post", []), parcel)),
        ("spatial_quality",   lambda: _c7_spatial_quality(shelter_result, parcel)),
        ("road_network",      lambda: _c8_road_network(roads, shelter_result)),
        ("land_use",          lambda: _c9_land_use(shelters, facilities, parcel)),
    ]

    components   = []
    weighted_sum = 0
    max_weighted = 340  # sum of weights (34) x max sub-score (10)

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
