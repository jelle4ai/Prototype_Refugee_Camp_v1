"""
Stage E / HP-bias regression test: confirms all communities place cleanly
on the 320x180m fixture now that the HP placement bias moves the HP off
the community-candidate row, resolving the CS5 collision that previously
caused a 224/240 shortfall.

Historical note: before the HP bias fix, the HP sat at (160, 90) --
exactly on a community-candidate position in the tight 5x3 grid -- blocking
one slot. The retry logic (_COMM_RETRY_OFFSETS) recovered 1 of 2 blocked
candidates (13 -> 14/15 communities). With HP now at ~(160, 112.5) due to
bias, the collision no longer occurs and all 15 communities place without
needing the retry path. Zero-overlap guarantee must still hold.

Run from the project root:
    python test_community_retry.py
"""
import sys
from math import ceil
from shapely.geometry import Polygon as _Poly
from shapely.ops import unary_union

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


all_ok = True

print("=" * 60)
print("Test -- community retry recovers a lattice candidate lost to a")
print("        CS5 facility, without overlap or silent over-claiming")
print("=" * 60)

parcel_pts = [(0, 0), (320, 0), (320, 180), (0, 180)]
population = 1200
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

print(f"  communities placed: {len(sr['communities'])} / 15 required")
print(f"  shelters placed: {sr['placed']} / {sr['required']}")
print(f"  latrines placed: {len(sr['community_latrines'])} / {ceil(population / 20)} required")

all_ok &= _check(
    "all communities placed (HP bias moves HP off the community-candidate row, "
    "resolving the CS5 collision that previously caused 13/15 -> 14/15)",
    len(sr["communities"]) == 15,
    f"{len(sr['communities'])}/15",
)
all_ok &= _check(
    "all shelters placed (224/240 shortfall no longer occurs with biased HP)",
    sr["placed"] == 240 and sr["required"] == 240,
    f"{sr['placed']}/{sr['required']}",
)
all_ok &= _check(
    "no shortfall reported (all communities successfully placed)",
    sr.get("shortfall_communities") is None or sr.get("shortfall_communities") == 0,
    f"shortfall_communities={sr.get('shortfall_communities')}",
)

# Zero-overlap guarantee: every footprint placed (shelters, CS5 facilities,
# and the recovered community's own latrines/washing/tap) must still be
# overlap-free -- the retry's explicit occ-check is what's responsible for
# this, not just the original lattice-spacing assumption.
polys = [_Poly(s["corners_m"]) for s in sr["shelters"]]
for key in ("health_post", "food_distribution", "community_space",
            "administrative_area", "schools", "worship_facility"):
    for item in facilities.get(key, []):
        polys.append(_Poly(item["corners_m"]))
for l in sr["community_latrines"]:
    polys.append(_Poly(l["corners_m"]))
for w in sr["community_washing"]:
    polys.append(_Poly(w["corners_m"]))
for t in sr["community_water"]:
    polys.append(_Poly(t["corners_m"]))
total_area = sum(p.area for p in polys)
union_area = unary_union(polys).area
overlap = max(0.0, total_area - union_area)
all_ok &= _check("zero footprint overlap preserved", overlap <= 0.01,
                 f"{overlap:.3f} m2 total overlap, {len(polys)} footprints")

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
