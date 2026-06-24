"""
Stage 2 test: _c2_water_quality()

Tests comfort margin (60%) and spread (40%) components.
Run from the project root:
    python test_scoring_c2_water_quality.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c2_water_quality

all_ok = True


def check(name, cond, msg=""):
    global all_ok
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {msg}")
        all_ok = False


def _shelter(cx, cy, w=3.8, h=3.8):
    hw, hh = w / 2, h / 2
    return {"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}


def _wp(cx, cy):
    return {"corners_m": [(cx-1,cy-1),(cx+1,cy-1),(cx+1,cy+1),(cx-1,cy+1)]}


PARCEL = Polygon([(0,0),(300,0),(300,300),(0,300)])

# ── 1. Shelter and water at same point -> comfort = 500, spread depends on single point
#       comfort_score = 10; spread: 1 wp in 9 zones -> gf=1/9->spread_score~1.1 -> sub=round(6+0.44)=6
sh_at_water = [_shelter(150, 150)]
wp_at_shelter = [_wp(150, 150)]
pts, expl = _c2_water_quality(sh_at_water, wp_at_shelter, PARCEL)
check("Shelter at water point -> score >= 6", pts >= 6, f"got {pts} -- {expl}")

# ── 2. All shelters exactly 500 m from water -> comfort = 0, score = 0
#       Use a tall parcel so 500 m is achievable: 10x600 parcel
PARCEL_TALL = Polygon([(0,0),(10,0),(10,600),(0,600)])
sh_far = [_shelter(5, 550)]
wp_far = [_wp(5, 50)]   # 500 m away
pts, expl = _c2_water_quality(sh_far, wp_far, PARCEL_TALL)
check("Shelter 500 m from water -> comfort=0 -> score <= 2", pts <= 2, f"got {pts} -- {expl}")

# ── 3. No water points -> 0
pts, expl = _c2_water_quality([_shelter(100, 100)], [], PARCEL)
check("No water points -> 0", pts == 0, f"got {pts}")

# ── 4. No shelters -> 10 (N/A)
pts, expl = _c2_water_quality([], [_wp(150, 150)], PARCEL)
check("No shelters -> 10", pts == 10, f"got {pts}")

# ── 5. Two water points spread across parcel -> higher score than single central point
#       Shelters spread across parcel -> multiple zone fill
PARCEL_L = Polygon([(0,0),(600,0),(600,600),(0,600)])
shelters_spread = [_shelter(100, 100), _shelter(500, 100), _shelter(100, 500), _shelter(500, 500)]
wp_single = [_wp(300, 300)]          # central, all shelters ~424 m away (comfort ~76 m each)
wp_spread = [_wp(100, 100), _wp(500, 500)]  # shelters nearby -> comfort ~499 + spread in 2 zones
pts_single, _ = _c2_water_quality(shelters_spread, wp_single, PARCEL_L)
pts_spread, _ = _c2_water_quality(shelters_spread, wp_spread, PARCEL_L)
check("Spread water points score >= single central point", pts_spread >= pts_single,
      f"spread={pts_spread} single={pts_single}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
