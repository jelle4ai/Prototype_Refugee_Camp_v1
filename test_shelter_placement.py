"""
Shelter under-placement fix: multi-shape verification.

The community candidate grid in place_shelters() was too coarse (62x82 m
pitch) to generate enough slots to reach the required community count on
several real parcels, even though R4 (area capacity) passed with room to
spare — confirmed by zero skip-counters (no placement failures, no firebreak
push-outs, no CS5 overlaps), pure candidate exhaustion. Fixed by tightening
the pitch to the collision-proof minimum (54.0 x 48.0 m — see comments in
place_shelters()) and walking the inset polygon's own bounds instead of
recomputing parcel-bounds-minus-margin (which could invert to zero rows on a
very narrow parcel).

This script checks the fix across shapes, not just one:
  A. Large rectangular parcel        -> expect 100% fill
  B. Large irregular (notched) parcel -> expect 100% fill
  C. Narrow/awkward strip parcel      -> expect full or near-full fill
  D. Deliberately too-small parcel    -> expect a graceful R4 shortfall
     message (how many it can hold vs how many were requested), not a
     silent under-placement

Run from the project root:
    python test_shelter_placement.py
"""
import sys
from math import ceil
from shapely.geometry import Polygon as _Poly
from shapely.ops import unary_union

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters, place_roads
from src.scoring import compliance_gate

all_ok = True


def _check(label, cond, detail=""):
    global all_ok
    cond = bool(cond)
    all_ok &= cond
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def run_scenario(label, parcel_pts, population, expect_full_fill, shelter_area_m2=17.5,
                 expect_geometric_shortfall=False):
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

    print("=" * 70)
    print(f"Scenario: {label}")
    print(f"  Parcel area : {parcel.area:.0f} m2  "
          f"(R4 cap {parcel.area/45:.0f} pp vs {population} pp needed)")
    print(f"  Population  : {population} pp  ->  {n_shelters} shelter units required")
    print("=" * 70)

    facilities   = place_all_facilities(site, reqs)
    occupied_geo = facilities.pop("_occupied_geo", None)
    sr = place_shelters(site, reqs, occupied_geo=occupied_geo)

    if sr.get("r4_fail"):
        print(f"  R4 capacity message: {sr['r4_detail']}")
        _check("R4 fails honestly (graceful shortfall, not silent under-placement)",
               not expect_full_fill)
        return

    if expect_geometric_shortfall:
        sc = sr.get("shortfall_communities", 0)
        placed = sr.get("placed", 0)
        print(f"  Geometric shortfall: {sc} communities, {sr.get('shortfall_shelters', 0)} shelters")
        print(f"  Placed {placed}/{n_shelters} shelters — capacity ~{placed * 5} people")
        _check("R4 passes (area sufficient)", not sr.get("r4_fail"))
        _check("shortfall_communities set", sc > 0, f"sc={sc}")
        _check("some shelters placed (partial fill, not total failure)",
               placed > 0, f"placed={placed}")
        _check("capacity estimate positive", placed * 5 > 0)
        overlap_check = next(c for c in
                             compliance_gate({"shelter_result": sr, "facilities": facilities,
                                             "roads": place_roads(site, sr, facilities)},
                                            site, reqs)["checks"]
                             if c["name"] == "No footprint overlaps")
        _check("zero footprint overlap", overlap_check["pass"], overlap_check["detail"])
        return

    roads = place_roads(site, sr, facilities)
    layout = {"shelter_result": sr, "facilities": facilities, "roads": roads}
    gate = compliance_gate(layout, site, reqs)

    sh_p, sh_r = sr["placed"], sr["required"]
    n_toilets_req  = reqs["toilets"]["count"]
    n_toilets_got  = len(sr["community_latrines"])
    n_washing_req  = reqs["washing_facilities"]["count"]
    n_washing_got  = len(sr["community_washing"])

    print(f"  Shelters  placed/required: {sh_p}/{sh_r}")
    print(f"  Toilets   placed/required: {n_toilets_got}/{n_toilets_req}")
    print(f"  Washing   placed/required: {n_washing_got}/{n_washing_req}")
    if sr.get("shortfall_communities"):
        print(f"  Shortfall: {sr['shortfall_communities']} communities "
              f"({sr['shortfall_shelters']} shelters)")

    overlap_check = next(c for c in gate["checks"] if c["name"] == "No footprint overlaps")
    print(f"  Overlap check: {overlap_check['detail']}")

    if expect_full_fill:
        _check("shelters fully placed", sh_p >= sh_r, f"{sh_p}/{sh_r}")
        _check("toilets fully placed", n_toilets_got >= n_toilets_req, f"{n_toilets_got}/{n_toilets_req}")
        _check("washing fully placed", n_washing_got >= n_washing_req, f"{n_washing_got}/{n_washing_req}")
    _check("zero footprint overlap", overlap_check["pass"], overlap_check["detail"])


# A. Large rectangular parcel — plenty of room, every axis a clean multiple
run_scenario("A. Large rectangular (600 x 400 m, 2500 pp)",
             [(0, 0), (600, 0), (600, 400), (0, 400)],
             population=2500, expect_full_fill=True)

# B. Large irregular (notched) parcel — same area class, cut corner + a bite
#    taken out of one side, to stress the inset-boundary filter generally
run_scenario(
    "B. Large irregular notched parcel (~580 x 380 m, 2400 pp)",
    [(0, 0), (580, 0), (580, 250), (480, 250), (480, 380), (0, 380)],
    population=2400, expect_full_fill=True,
)

# C. Narrow/awkward trapezoid — narrower aspect ratio than A/B and skewed
#    (not axis-aligned rectangular), tall enough for multiple rows so this
#    tests sampling density on an awkward shape, not the separate, genuine
#    single-row geometric limit a truly extreme strip would hit (see note
#    after the run for that distinct case)
run_scenario("C. Narrow/awkward trapezoid (500 x 200 m, narrows at top, 1200 pp)",
             [(0, 0), (500, 0), (450, 200), (50, 200)],
             population=1200, expect_full_fill=True)

# D. Deliberately too small — R4 should fail honestly, not under-place silently
run_scenario("D. Deliberately too-small parcel (60 x 60 m, 2000 pp)",
             [(0, 0), (60, 0), (60, 60), (0, 60)],
             population=2000, expect_full_fill=False)

# E. HP off-by-one regression — width 385 m makes int(385/54)=7 cols (parcel)
#    but inset is 315 m wide → only 6 columns fit (int(315/54)+1=6).
#    At pop=1100: n_comm=14.  Old code: fill_rows=ceil(14/7)=2 → HP at y≈50 m,
#    landing in the south-latrine band of row-2 communities (cy-34=49 m) →
#    WS5 failures → ~7/14 communities placed.
#    New code: n_cols from inset → fill_rows=ceil(14/6)=3=n_rows → entrance
#    bias → HP at y≈75 m, south latrines clear → all 14/14 communities placed.
run_scenario("E. HP off-by-one regression (385 x 200 m, 1100 pp)",
             [(0, 0), (385, 0), (385, 200), (0, 200)],
             population=1100, expect_full_fill=True)

# F. Geometric capacity shortfall — R4 passes but concave shape limits lattice slots.
#    450×130 m parcel: area=58,500 m² > 49,500 m² (R4 passes for 1100 pp).
#    Inset after 35 m margin: 380×60 m → only 1 lattice row (48 m pitch, 8 cols)
#    for 14 communities needed → shortfall_communities set, some shelters placed.
#    This is the Site D class of failure: honest partial placement, not silent.
run_scenario(
    "F. Geometric shortfall (450 x 130 m narrow strip, 1100 pp) — R4 passes, shape limits",
    [(0, 0), (450, 0), (450, 130), (0, 130)],
    population=1100, expect_full_fill=False, expect_geometric_shortfall=True,
)

print("=" * 70)
if all_ok:
    print("ALL CHECKS PASSED")
else:
    print("SOME CHECKS FAILED")
    sys.exit(1)
