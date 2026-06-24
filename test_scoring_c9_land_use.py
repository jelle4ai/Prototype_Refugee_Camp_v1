"""
Stage 9 test: _c9_land_use()

Tests SH10-compliant land-use scoring: leftover land = expansion reserve = good.
Score only drops when the built footprint exceeds comfortable density thresholds.
  <= 70% use -> score 10  (spacious, expansion reserve)
  70-85%     -> score 10 down to 5  (dense but not overcrowded)
  > 85%      -> score 5 down to 0  (overcrowded)
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
    side = target ** 0.5
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    hw, hh = min(side/2, (maxx-minx)/2 - 1), min(side/2, (maxy-miny)/2 - 1)
    return [{"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}]


PARCEL = Polygon([(0,0),(1000,0),(1000,1000),(0,1000)])   # 1 000 000 m^2

# ── 1. ~5% use -> score = 10 (sparse = expansion reserve, SH10)
shelters_5 = _fill_parcel(PARCEL, 0.05)
pts, expl = _c9_land_use(shelters_5, {}, PARCEL)
check("~5% use -> score = 10 (expansion reserve, SH10)", pts == 10, f"got {pts} -- {expl}")

# ── 2. ~25% use -> score = 10 (still spacious)
shelters_25 = _fill_parcel(PARCEL, 0.25)
pts, expl = _c9_land_use(shelters_25, {}, PARCEL)
check("~25% use -> score = 10 (spacious)", pts == 10, f"got {pts} -- {expl}")

# ── 3. ~50% use -> score = 10 (below 70% threshold)
shelters_50 = _fill_parcel(PARCEL, 0.50)
pts, expl = _c9_land_use(shelters_50, {}, PARCEL)
check("~50% use -> score = 10 (still spacious)", pts == 10, f"got {pts} -- {expl}")

# ── 4. ~70% use -> score = 10 (at boundary, still <=70%)
#       use_ratio ~= 0.700 -> score = 10 (boundary condition)
shelters_70 = _fill_parcel(PARCEL, 0.70)
pts, expl = _c9_land_use(shelters_70, {}, PARCEL)
check("~70% use -> score = 10 (at boundary)", pts == 10, f"got {pts} -- {expl}")

# ── 5. ~78% use -> score = 7 (dense range: 70-85%)
#       r=0.780: round(10 - (0.780-0.70)/0.15*5) = round(10-2.667) = round(7.333) = 7
shelters_78 = _fill_parcel(PARCEL, 0.78)
pts, expl = _c9_land_use(shelters_78, {}, PARCEL)
check("~78% use -> score = 7 (dense)", pts == 7, f"got {pts} -- {expl}")

# ── 6. ~85% use -> score = 5 (top of overcrowded threshold)
#       r=0.850: round(10 - (0.850-0.70)/0.15*5) = round(10-5) = 5
shelters_85 = _fill_parcel(PARCEL, 0.85)
pts, expl = _c9_land_use(shelters_85, {}, PARCEL)
check("~85% use -> score = 5 (dense/overcrowded boundary)", pts == 5, f"got {pts} -- {expl}")

# ── 7. ~95% use -> score = 2 (overcrowded)
#       r=0.950: round(max(0, 5-(0.950-0.85)/0.15*5)) = round(max(0,5-3.333)) = round(1.667) = 2
shelters_95 = _fill_parcel(PARCEL, 0.95)
pts, expl = _c9_land_use(shelters_95, {}, PARCEL)
check("~95% use -> score = 2 (overcrowded)", pts == 2, f"got {pts} -- {expl}")

# ── 8. ~100% use -> score = 0
#       r~=0.996: round(max(0, 5-(0.996-0.85)/0.15*5)) = round(max(0,5-4.867)) = round(0.133) = 0
shelters_100 = _fill_parcel(PARCEL, 0.999)
pts, expl = _c9_land_use(shelters_100, {}, PARCEL)
check("~100% use -> score = 0 (fully overcrowded)", pts == 0, f"got {pts} -- {expl}")

# ── 9. No elements -> 0
pts, expl = _c9_land_use([], {}, PARCEL)
check("No elements -> 0", pts == 0, f"got {pts}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
