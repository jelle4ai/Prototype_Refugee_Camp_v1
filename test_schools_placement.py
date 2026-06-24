"""
Stage D test (24 June session): schools spread across the populated area
instead of clustering at one edge.

Diagnosis: _grid_place() (used for schools whenever count > 1) scanned
grid cells row-major from the bottom-left corner of the parcel's bounding
box and returned as soon as `count` schools were placed. For low counts
(schools is usually 1-3) this means every instance lands in the first 1-2
cells tried, all in the same corner -- confirmed by reverting the fix and
re-running this exact scenario: both schools landed at the same y (58.3),
near the bottom edge, even though shelters span y from 15 to 296.

Fix: _grid_place() now tries `count` evenly-spaced cell indices across the
grid first, falling back to the remaining cells in the original order if
a spread-out cell is blocked -- so 2 schools land at opposite ends of the
grid instead of adjacent cells in one corner.

Known limitation (not fixed here, documented in PROGRESS.md): schools are
placed in place_all_facilities(), before shelters/communities exist, so
this can only spread across the parcel's bounding box, not the eventual
populated extent. On a parcel where the populated area is a small
fraction of the bounding box (e.g. a large rectangle with a small
population), a spread-out cell can still land in genuinely empty parcel.
This test uses a realistic, fully-populated irregular parcel (512/500
shelters placed, communities filling most of the parcel) where that
limitation does not apply.

Run from the project root:
    python test_schools_placement.py
"""
import sys
from math import ceil

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters
from src.scoring import compliance_gate


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def _cen(corners):
    return (sum(p[0] for p in corners) / len(corners),
            sum(p[1] for p in corners) / len(corners))


all_ok = True

print("=" * 60)
print("Test -- schools spread across the populated area, not one edge")
print("=" * 60)

parcel_pts = [(0, 0), (420, 0), (420, 200), (300, 350), (0, 350)]
population = 2500
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

schools = facilities["schools"]
print(f"  {len(schools)} schools placed")
all_ok &= _check("exactly 2 schools required and placed", len(schools) == 2,
                 f"{len(schools)} placed")

shelter_cens = [_cen(s["corners_m"]) for s in sr["shelters"]]
xs = [p[0] for p in shelter_cens]
ys = [p[1] for p in shelter_cens]
margin = 40.0
bbox = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
print(f"  shelter extent (+{margin:.0f} m margin): "
      f"x [{bbox[0]:.0f}, {bbox[2]:.0f}], y [{bbox[1]:.0f}, {bbox[3]:.0f}]")

school_cens = [_cen(s["corners_m"]) for s in schools]
for i, (sx, sy) in enumerate(school_cens):
    print(f"  school {i}: ({sx:.1f}, {sy:.1f})")
    in_region = bbox[0] <= sx <= bbox[2] and bbox[1] <= sy <= bbox[3]
    all_ok &= _check(f"school {i} sits inside the populated region", in_region)

# The original bug: both schools land at the same row near one edge.
ys_schools = [sy for _, sy in school_cens]
all_ok &= _check(
    "schools are NOT clustered at the same row (spread, not stacked)",
    max(ys_schools) - min(ys_schools) > 50.0,
    f"y spread = {max(ys_schools) - min(ys_schools):.1f} m",
)

layout = {"shelter_result": sr, "facilities": facilities, "roads": {}}
gate = compliance_gate(layout, site, reqs)
ed3 = next((c for c in gate["checks"] if c["name"].startswith("ED3")), None)
all_ok &= _check("ED3: every shelter within 1 km of a school still passes",
                 ed3 is not None and ed3["pass"], ed3["detail"] if ed3 else "no ED3 check")

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
