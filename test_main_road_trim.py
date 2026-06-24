"""
Stage B test (24 June session): the generated main road must stop near the
populated extent, not run on to the farthest geometric parcel vertex.

Before this fix, the main road's far end was the farthest boundary vertex
from the entrance regardless of where shelters/communities actually ended
up, so it could run into empty parcel beyond the last shelters (visible on
the left of the rendered layout). Confirmed cause: the
entrance -> centroid -> farthest-boundary-vertex waypoint logic in
place_roads().

This uses the same irregular-parcel scenario B as test_stage4.py (1500 pp,
300 shelters), where the parcel's farthest vertex from the entrance is
known to sit well past the populated area.

Run from the project root:
    python test_main_road_trim.py
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


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


all_ok = True

print("=" * 60)
print("Test -- generated main road far end stops near the populated extent")
print("=" * 60)

parcel_pts = [(0, 0), (420, 0), (420, 200), (300, 350), (0, 350)]
population = 1500
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

shelter_cens = [
    (sum(p[0] for p in s["corners_m"]) / len(s["corners_m"]),
     sum(p[1] for p in s["corners_m"]) / len(s["corners_m"]))
    for s in sr["shelters"]
]

main_pts = [pt for seg in roads["main_road"] for pt in seg["pts_m"]]
far_terminus = main_pts[-1]
entrance = roads["entrance_m"]

nearest_shelter_dist = min(_dist(far_terminus, c) for c in shelter_cens)
print(f"  far terminus = ({far_terminus[0]:.1f}, {far_terminus[1]:.1f})")
print(f"  nearest shelter centroid distance = {nearest_shelter_dist:.1f} m")

all_ok &= _check(
    "far terminus is within ~40 m of the nearest shelter/community",
    nearest_shelter_dist <= 40.0,
    f"{nearest_shelter_dist:.1f} m",
)
all_ok &= _check(
    "entrance end is unchanged (first main point == entrance)",
    _dist(main_pts[0], entrance) < 0.5,
)
all_ok &= _check(
    "PA3 connectivity still passes after trimming",
    roads["connected"], f"stranded={roads['stranded']}",
)

# Sanity: confirm the OLD farthest-vertex behaviour really would have been
# much farther away, so this test would have caught the original bug.
from shapely.geometry import Polygon as _Poly
parcel = _Poly(parcel_pts)
old_far = max(parcel.exterior.coords, key=lambda p: (p[0]-entrance[0])**2 + (p[1]-entrance[1])**2)
old_far_dist_to_shelters = min(_dist(old_far, c) for c in shelter_cens)
print(f"  (sanity) old farthest-vertex distance to nearest shelter would have "
      f"been {old_far_dist_to_shelters:.1f} m")
all_ok &= _check(
    "sanity: old geometric-vertex terminus would have been far from shelters",
    old_far_dist_to_shelters > 40.0,
    f"{old_far_dist_to_shelters:.1f} m",
)

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
