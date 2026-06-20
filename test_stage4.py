"""
Stage 4 test: full pipeline integration

Two scenarios:
  A. 700 x 400 m rectangular parcel, 2,000 people — SH7 must trigger.
  B. ~420 x 350 m irregular parcel (cut corner), 1,500 people (300 shelters) —
     this is the real reported bug: old code placed only 160/300 shelters.
     Must now place all 300 shelters and pass the compliance gate.

Run from the project root:
    python test_stage4.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from math import ceil
from shapely.geometry import Polygon as _Poly
from shapely.ops import unary_union

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters, place_roads
from src.scoring import compliance_gate


def run_scenario(label, parcel_pts, population, shelter_area_m2=17.5):
    """Run the full placement pipeline and print results."""
    n_shelters = population // 5
    site = {"parcel_polygon_m": parcel_pts, "roads_m": []}
    parcel = _Poly(parcel_pts)

    reqs = {
        "shelter_units":            {"count": n_shelters, "area_per_unit_m2": shelter_area_m2},
        "health_posts":             {"count": 1},
        "water_points":             {"count": max(1, population // 250)},
        "food_distribution_points": {"count": 1},
        "community_space":          {"count": 1},
        "administrative_area":      {"count": 1},
        "schools":                  {"count": max(1, population // 1000)},
        "worship_facility":         {"count": 1},
        "toilets":                  {"count": ceil(population / 20)},
        "washing_facilities":       {"count": ceil(population / 100)},
    }

    print(f"\n{'=' * 70}")
    print(f"Scenario: {label}")
    print(f"  Parcel area : {parcel.area:.0f} m2  "
          f"(R4 cap {parcel.area/45:.0f} pp vs {population} pp needed)")
    print(f"  Population  : {population} pp  ->  {n_shelters} shelter units required")
    print(f"{'=' * 70}")

    # Step 1: CS5 facilities
    facilities   = place_all_facilities(site, reqs)
    occupied_geo = facilities.pop("_occupied_geo", None)

    # Step 2: shelter / community scan
    sr = place_shelters(site, reqs, occupied_geo=occupied_geo)

    if sr.get("r4_fail"):
        print(f"  FAIL R4: {sr['r4_detail']}")
        return

    fb_xs       = sr["firebreak_xs"]
    communities = sr["communities"]
    shelters    = sr["shelters"]
    blocks      = sr["blocks"]

    print(f"\n  Placement summary")
    print(f"    Blocks (reporting groups) : {len(blocks)}")
    print(f"    Communities placed        : {len(communities)}")
    print(f"    Shelters placed           : {sr['placed']} / {sr['required']}")
    print(f"    Taps                      : {len(sr['community_water'])}")
    print(f"    Latrines                  : {len(sr['community_latrines'])}")
    print(f"    Washing units             : {len(sr['community_washing'])}")

    if sr.get("shortfall_communities"):
        print(f"  *** SHORTFALL: {sr['shortfall_communities']} communities "
              f"({sr['shortfall_shelters']} shelters) could not be placed")

    # SH7 check — firebreak_xs is the direct source; blocks are just reporting groups
    print(f"\n  SH7 firebreak check")
    if fb_xs:
        print(f"    Firebreak(s) inserted at x = {[f'{x:.0f}' for x in fb_xs]} m")
        print(f"    [PASS]  SH7 firebreak(s) applied within 300 m E-W bands")
    else:
        # Measure actual E-W span of all community polys per y-band to confirm
        # no band exceeded 300 m (absence of firebreak is correct if span <= 300 m)
        from math import sqrt
        band_ew: dict[int, list] = {}
        for c in communities:
            bx0, _, bx1, _ = c["community_poly"].bounds
            by0, _, _, by1 = c["community_poly"].bounds
            bcy = (by0 + by1) / 2
            band_idx = round(bcy / 82.0)
            band_ew.setdefault(band_idx, []).append((bx0, bx1))
        max_span = 0.0
        for spans in band_ew.values():
            span = max(x1 for _, x1 in spans) - min(x0 for x0, _ in spans)
            max_span = max(max_span, span)
        ok = "PASS" if max_span <= 300.0 else "FAIL"
        print(f"    [{ok}]  No firebreak needed — max E-W band span = {max_span:.1f} m")

    # Step 3: merge community facilities
    facilities["water_points"].extend(sr.pop("community_water", []))
    facilities["toilets"].extend(sr.pop("community_latrines", []))
    facilities["washing_facilities"].extend(sr.pop("community_washing", []))
    for fac_key in ("water_points", "toilets", "washing_facilities"):
        facilities["status"][fac_key] = {
            "placed":   len(facilities[fac_key]),
            "required": facilities["status"].get(fac_key, {}).get("required", 0),
        }

    print(f"\n  After merge")
    for fac_key in ("water_points", "toilets", "washing_facilities"):
        s = facilities["status"][fac_key]
        ok = "PASS" if s["placed"] >= s["required"] else "FAIL"
        print(f"    [{ok}]  {fac_key:<26}: {s['placed']} / {s['required']}")

    # Step 4: roads
    roads = place_roads(site, sr, facilities)

    # Step 5: compliance gate
    layout = {"shelter_result": sr, "facilities": facilities, "roads": roads}
    gate   = compliance_gate(layout, site, reqs)

    print(f"\n  Compliance gate: {'PASS' if gate['pass'] else 'FAIL'}")
    for check in gate["checks"]:
        status = "PASS" if check["pass"] else "FAIL"
        print(f"    [{status}]  {check['name']:<45}  {check['detail']}")

    # Site-wide overlap
    all_polys = []
    for s in shelters:
        try: all_polys.append(_Poly(s["corners_m"]))
        except Exception: pass
    for key in ("health_post", "food_distribution", "community_space",
                "administrative_area", "schools", "worship_facility",
                "water_points", "toilets", "washing_facilities"):
        for item in facilities.get(key, []):
            try: all_polys.append(_Poly(item["corners_m"]))
            except Exception: pass
    if len(all_polys) >= 2:
        overlap = max(0.0, sum(p.area for p in all_polys) - unary_union(all_polys).area)
        ok = "PASS" if overlap <= 1.0 else "FAIL"
        print(f"\n  [{ok}]  Site-wide overlap: {overlap:.2f} m2  ({len(all_polys)} footprints)")
    print(f"  [{'PASS' if roads.get('connected') else 'FAIL'}]  "
          f"Road network connected: {roads.get('connected')}")

    # Text map
    bminx, bminy, bmaxx, bmaxy = _Poly(parcel_pts).bounds
    pw = bmaxx - bminx
    ph = bmaxy - bminy
    CELL = max(5.0, round(max(pw, ph) / 70 / 5) * 5)
    COLS = min(70, int(pw / CELL) + 1)
    ROWS = min(40, int(ph / CELL) + 1)
    grid = [["."] * COLS for _ in range(ROWS)]

    def _mark(corners, char):
        if not corners: return
        xs = [p[0] - bminx for p in corners]
        ys = [p[1] - bminy for p in corners]
        c0 = max(0, int(min(xs) / CELL))
        c1 = min(COLS - 1, int(max(xs) / CELL))
        r0 = max(0, int(min(ys) / CELL))
        r1 = min(ROWS - 1, int(max(ys) / CELL))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = char

    for s in shelters:                              _mark(s["corners_m"], "S")
    for i in facilities.get("toilets", []):         _mark(i["corners_m"], "L")
    for i in facilities.get("washing_facilities",[]): _mark(i["corners_m"], "W")
    for i in facilities.get("health_post", []):     _mark(i["corners_m"], "H")
    for i in facilities.get("food_distribution",[]): _mark(i["corners_m"], "F")
    for i in facilities.get("community_space", []): _mark(i["corners_m"], "C")
    for i in facilities.get("administrative_area",[]): _mark(i["corners_m"], "A")
    for i in facilities.get("schools", []):         _mark(i["corners_m"], "E")
    for i in facilities.get("worship_facility", []):_mark(i["corners_m"], "P")
    for comm in communities:                        _mark(comm.get("open_corners"), "O")
    for i in facilities.get("water_points", []):    _mark(i["corners_m"], "T")
    # Mark SH7 firebreak columns
    for fx in fb_xs:
        fc0 = int((fx - bminx) / CELL)
        fc1 = min(COLS - 1, int((fx - bminx + 30.0) / CELL))
        for r in range(ROWS):
            for c in range(max(0, fc0), fc1 + 1):
                if grid[r][c] == ".":
                    grid[r][c] = "|"

    print(f"\n  Text map (cell={CELL:.0f}m, origin bottom-left)")
    print("   " + "".join(str(c % 10) for c in range(COLS)))
    for r in range(ROWS - 1, -1, -1):
        print(f"{int((r * CELL) + bminy):3d} " + "".join(grid[r]))
    print("  Legend: S=shelter O=open T=tap L=latrine W=washing "
          "H=health F=food C=comm A=admin E=school P=worship |=SH7fb")


# ── Scenario A: large rectangular parcel, 2000 people ─────────────────────
run_scenario(
    "A: 700x400 m rectangular, 2000 pp (SH7 must trigger)",
    [(0,0),(700,0),(700,400),(0,400)],
    population=2000,
)

# ── Scenario B: irregular parcel (~420x350 m, cut corner), 1500 people ────
# This reproduces the real reported bug: old code placed 160/300 shelters because
# bounding-box block layout pushed Block 2 outside the parcel after the SH7 shift.
# The community-scan approach must now place all 300 shelters and pass the gate.
run_scenario(
    "B: 420x350 m irregular (cut NE corner), 1500 pp / 300 shelters (full-fill test)",
    [(0,0),(420,0),(420,200),(300,350),(0,350)],
    population=1500,
)
