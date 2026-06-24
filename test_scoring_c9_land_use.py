"""
Stage 9 test: _c9_land_use()

Tests the goldilocks land-use curve: peaks at 50% use, penalises both
too sparse and too dense layouts.
Run from the project root:
    python test_scoring_c9_land_use.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c9_land_use

all_ok = True


def check(name, cond, msg=""):
    global all_ok
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {msg}")
        all_ok = False


def _fill_parcel(parcel, fraction):
    """Return a shelter list that fills `fraction` of the parcel area."""
    minx, miny, maxx, maxy = parcel.bounds
    total_area = parcel.area
    target = total_area * fraction
    # Use one big shelter that fills the target fraction
    side = target ** 0.5
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    hw, hh = min(side/2, (maxx-minx)/2 - 1), min(side/2, (maxy-miny)/2 - 1)
    return [{"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}]


PARCEL = Polygon([(0,0),(1000,0),(1000,1000),(0,1000)])   # 1 000 000 m^2

# ── 1. ~50% use -> score = 10
#       use_ratio = 0.50 -> formula: round((0.50-0.05)/0.45*10) = round(10) = 10
shelters_50 = _fill_parcel(PARCEL, 0.50)
pts, expl = _c9_land_use(shelters_50, {}, PARCEL)
check("~50% use -> score = 10", pts == 10, f"got {pts} -- {expl}")

# ── 2. ~5% use -> score = 0
shelters_5 = _fill_parcel(PARCEL, 0.05)
pts, expl = _c9_land_use(shelters_5, {}, PARCEL)
check("~5% use -> score = 0", pts == 0, f"got {pts} -- {expl}")

# ── 3. ~95% use -> score = 0
#       use_ratio=0.95 -> round(max(0, 7-(0.95-0.80)/0.20*7)) = round(max(0,7-5.25))=round(1.75)=2
#       Actually: (0.95-0.80)/0.20 = 0.75; 7*0.75=5.25; 7-5.25=1.75; round=2 -> score=2 which is <= 3
shelters_95 = _fill_parcel(PARCEL, 0.95)
pts, expl = _c9_land_use(shelters_95, {}, PARCEL)
check("~95% use -> score <= 2 (too dense)", pts <= 2, f"got {pts} -- {expl}")

# ── 4. ~25% use -> intermediate score (4-5)
#       use_ratio=0.25 -> round((0.25-0.05)/0.45*10) = round(4.44) = 4
shelters_25 = _fill_parcel(PARCEL, 0.25)
pts, expl = _c9_land_use(shelters_25, {}, PARCEL)
check("~25% use -> score 4-5", 4 <= pts <= 5, f"got {pts} -- {expl}")

# ── 5. No elements -> 0
pts, expl = _c9_land_use([], {}, PARCEL)
check("No elements -> 0", pts == 0, f"got {pts}")

# ── 6. ~70% use -> moderate density (score around 7-8)
#       use_ratio=0.70 -> at boundary of 0.50-0.80 range:
#       round(10 - (0.70-0.50)/0.30*3) = round(10-2) = 8
shelters_70 = _fill_parcel(PARCEL, 0.70)
pts, expl = _c9_land_use(shelters_70, {}, PARCEL)
check("~70% use -> score 7-8 (moderate)", 7 <= pts <= 8, f"got {pts} -- {expl}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
