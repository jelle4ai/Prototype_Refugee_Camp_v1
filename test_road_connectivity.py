"""
Stage C test: every community is reachable from the entrance through the
road graph (PA3 connectivity, extended to per-community granularity).

Tertiary paths now connect each community individually (replacing the old
arbitrary "shelter band" grouping), so this checks the road network across
two different scenarios -- a small single-community parcel and the larger
irregular multi-community parcel from test_stage4.py scenario B -- to make
sure connectivity holds whether or not tertiary paths are even needed
(some communities sit close enough to the main/secondary network already).

Run from the project root:
    python test_road_connectivity.py
"""
import sys
from math import ceil

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters, place_roads


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def _run(label, parcel_pts, population):
    n_shelters = population // 5
    site = {"parcel_polygon_m": parcel_pts, "roads_m": []}
    reqs = {
        "shelter_units":            {"count": n_shelters, "area_per_unit_m2": 17.5},
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
    facilities = place_all_facilities(site, reqs)
    occupied_geo = facilities.pop("_occupied_geo", None)
    sr = place_shelters(site, reqs, occupied_geo=occupied_geo)
    facilities["water_points"].extend(sr.pop("community_water", []))
    facilities["toilets"].extend(sr.pop("community_latrines", []))
    facilities["washing_facilities"].extend(sr.pop("community_washing", []))
    roads = place_roads(site, sr, facilities)

    print(f"  {label}: {len(sr['communities'])} communities, "
          f"{len(roads['footpaths'])} tertiary path segment(s) drawn")
    ok = _check(f"{label}: at least one community placed",
                len(sr["communities"]) > 0)
    ok &= _check(f"{label}: road network fully connected",
                 roads["connected"], f"stranded={roads['stranded']}")
    ok &= _check(f"{label}: no stranded nodes", roads["stranded"] == [])
    return ok


all_ok = True

print("=" * 60)
print("Test -- every community reachable from the entrance")
print("=" * 60)

all_ok &= _run("Small (1 community)",
               [(0, 0), (200, 0), (200, 200), (0, 200)], population=80)
all_ok &= _run("Irregular multi-community (scenario B)",
               [(0, 0), (420, 0), (420, 200), (300, 350), (0, 350)],
               population=1500)

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
