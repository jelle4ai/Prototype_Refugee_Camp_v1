"""
Stage B test: place_roads() main road starts at the (corrected) entrance
and spans the parcel, rather than radiating from the centroid alone.

Run from the project root:
    python test_main_road.py
"""
import sys

sys.path.insert(0, ".")
from src.layout_engine import place_roads, _entry_point


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


all_ok = True

print("=" * 60)
print("Test -- main road starts at the entrance and spans the parcel")
print("=" * 60)

# Same synthetic fixture as test_entrance.py: a 200x150 m parcel with a
# genuine frontage road running alongside the bottom edge and a corner
# stub that should NOT be selected.
PARCEL = [(0.0, 0.0), (200.0, 0.0), (200.0, 150.0), (0.0, 150.0)]
STUB_ROAD = [(-200.0, -200.0), (-1.0, -1.0)]
FRONTAGE_ROAD = [(-50.0, -5.0), (250.0, -5.0)]
site = {"parcel_polygon_m": PARCEL, "roads_m": [STUB_ROAD, FRONTAGE_ROAD]}

expected_entrance = _entry_point(site)
result = place_roads(site, {"shelters": []}, {})

main_road = result["main_road"]
all_ok &= _check("main road has at least one segment", len(main_road) > 0)

first_pt = main_road[0]["pts_m"][0] if main_road else None
all_ok &= _check(
    "main road's first point is the entrance",
    first_pt is not None and _dist(first_pt, expected_entrance) < 0.5,
    f"first_pt={first_pt}, entrance={expected_entrance}",
)
all_ok &= _check(
    "entrance_m returned matches _entry_point",
    _dist(result["entrance_m"], expected_entrance) < 0.5,
)

# Spans the parcel: the main road's overall extent should cover most of
# the parcel's diagonal, not just a short hop near the centroid.
all_pts = [pt for seg in main_road for pt in seg["pts_m"]]
xs = [p[0] for p in all_pts]
ys = [p[1] for p in all_pts]
main_extent = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
parcel_diag = _dist((0.0, 0.0), (200.0, 150.0))
all_ok &= _check(
    "main road spans most of the parcel's diagonal (>= 70%)",
    main_extent >= 0.7 * parcel_diag,
    f"main_extent={main_extent:.1f} m, parcel_diag={parcel_diag:.1f} m",
)

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
