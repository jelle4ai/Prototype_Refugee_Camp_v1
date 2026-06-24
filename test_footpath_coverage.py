"""
Stage C test (24 June session, part 2): every community gets at least one
DRAWN tertiary path reaching it, and that path comes near its latrine
blocks -- not just an abstract graph edge for connectivity bookkeeping.

Before this fix, almost no footpaths were drawn at all (the router threads
through communities' open spaces on its way across the site, so most
communities were already "close enough" to the road network by the
distance-gated logic and got no explicit segment) -- communities were
graph-connected but the rendered map showed no path into them or their
latrine blocks.

Run from the project root:
    python test_footpath_coverage.py
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


def _cen(corners):
    return (sum(p[0] for p in corners) / len(corners),
            sum(p[1] for p in corners) / len(corners))


all_ok = True

print("=" * 60)
print("Test -- every community has a drawn footpath reaching its latrines")
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

communities = sr["communities"]
footpaths_by_comm: dict[int, list] = {}
for seg in roads["footpaths"]:
    node = seg.get("_node", "")
    if node.startswith("community_"):
        footpaths_by_comm.setdefault(int(node.split("_")[1]), []).append(seg)

print(f"  {len(communities)} communities, {len(roads['footpaths'])} footpath "
      f"segments drawn, {len(footpaths_by_comm)} communities have >=1 segment")

all_ok &= _check(
    "every community has at least one drawn footpath segment",
    len(footpaths_by_comm) == len(communities),
    f"{len(footpaths_by_comm)}/{len(communities)}",
)

_MARGIN = 15.0
n_missing_latrine_reach = 0
for ci, comm in enumerate(communities):
    lat_cens = [_cen(l["corners_m"]) for l in comm.get("latrines", [])]
    if not lat_cens:
        continue
    segs = footpaths_by_comm.get(ci, [])
    all_pts = [pt for seg in segs for pt in seg["pts_m"]]
    if not all_pts:
        n_missing_latrine_reach += 1
        continue
    for lc in lat_cens:
        if min(_dist(lc, p) for p in all_pts) > _MARGIN:
            n_missing_latrine_reach += 1
            break

all_ok &= _check(
    f"every community's footpath(s) come within {_MARGIN:.0f} m of all its latrine blocks",
    n_missing_latrine_reach == 0,
    f"{n_missing_latrine_reach} community(ies) failed",
)
all_ok &= _check(
    "PA3 connectivity still passes",
    roads["connected"], f"stranded={roads['stranded']}",
)

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
