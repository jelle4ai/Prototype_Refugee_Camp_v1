"""
Stage A test: _entry_point() road selection and boundary projection.

Reproduces the bug confirmed on two real sites (see PROGRESS.md): a road
that merely terminates near a parcel corner can win entrance selection by
single-point distance, even though a genuine frontage road running
alongside the parcel for hundreds of metres is the real access road.

Real OSM geometry from the live trace logging could not be captured without
a browser run, so this uses a SYNTHETIC fixture that reproduces the same
bug signature instead:
  - a "stub" road that approaches from far away and terminates ~1.4 m from
    the parcel's bottom-left corner (wins under naive nearest-point
    distance: 1.4 m vs the frontage road's 5 m)
  - a genuine "frontage" road running parallel to the parcel's bottom edge,
    5 m off it, for 300 m (the correct road: ~212 m of it falls within an
    8 m buffer of the parcel, vs. the stub's ~6.6 m)

Run from the project root:
    python test_entrance.py
"""
import sys

sys.path.insert(0, ".")
from src.layout_engine import _entry_point


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


# ── Synthetic fixture ───────────────────────────────────────────────────────
# Parcel: 200 x 150 m rectangle, bottom-left corner at the origin.
PARCEL = [(0.0, 0.0), (200.0, 0.0), (200.0, 150.0), (0.0, 150.0)]

# Stub road: approaches from far southwest, terminates 1.414 m from the
# (0,0) corner. Wins under old single-point-distance selection.
STUB_ROAD = [(-200.0, -200.0), (-1.0, -1.0)]

# Frontage road: runs alongside the bottom edge, 5 m below it, far longer
# than the parcel itself (-50 to 250 on x, i.e. 300 m). This is the real
# access road and should win under alongside-length-within-8m selection.
FRONTAGE_ROAD = [(-50.0, -5.0), (250.0, -5.0)]

SITE = {"parcel_polygon_m": PARCEL, "roads_m": [STUB_ROAD, FRONTAGE_ROAD]}

all_ok = True

print("=" * 60)
print("Test 1 -- entrance lands on the frontage road, not the corner stub")
print("=" * 60)
ex, ey = _entry_point(SITE)
print(f"  entrance returned = ({ex:.2f}, {ey:.2f})")
all_ok &= _check("entrance is away from either corner (20 <= x <= 180)",
                 20.0 <= ex <= 180.0, f"x={ex:.2f}")
all_ok &= _check("entrance sits on the bottom edge (y close to 0)",
                 abs(ey) < 1.0, f"y={ey:.2f}")

print("=" * 60)
print("Test 2 -- entrance sits near the MIDPOINT of the alongside stretch")
print("=" * 60)
# The frontage road's intersection with the parcel's 8 m buffer runs from
# about x=-6.24 to x=206.24 (computed where distance to the nearest corner
# equals 8 m), so its midpoint is close to x=100 -- not at either extreme
# corner-proximate end.
all_ok &= _check("entrance x is close to the alongside-stretch midpoint (~100 m)",
                 abs(ex - 100.0) <= 15.0, f"x={ex:.2f}")

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
