"""
Stage 6 test: _c6_equity()

Tests P90 worst-served-shelter protection for water/sanitation/health.
Run from the project root:
    python test_scoring_c6_equity.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c6_equity

all_ok = True


def check(name, cond, msg=""):
    global all_ok
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {msg}")
        all_ok = False


def _shelter(cx, cy):
    return {"corners_m": [(cx-2,cy-2),(cx+2,cy-2),(cx+2,cy+2),(cx-2,cy+2)]}


def _fac(cx, cy, w=4, h=4):
    hw, hh = w/2, h/2
    return {"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}


PARCEL = Polygon([(0,0),(400,0),(400,400),(0,400)])   # half_diag = sqrt(320000)/2 = 283 m

# ── 1. All shelters at facilities -> P90 = 0 for all -> score = 10
sh_all = [_shelter(200, 200)]
wp  = [_fac(200, 200)]
lat = [_fac(200, 200)]
hp  = [_fac(200, 200)]
pts, expl = _c6_equity(sh_all, wp, lat, hp, PARCEL)
check("All at facilities -> score = 10", pts == 10, f"got {pts} -- {expl}")

# ── 2. All shelters >500 m from water -> equity_water = 0 -> score <= 7
PARCEL_TALL = Polygon([(0,0),(10,0),(10,700),(0,700)])   # half_diag ~ 350 m
sh_far_w = [_shelter(5, 600)]
wp_far   = [_fac(5, 50)]     # 550 m away, P90 = 550 -> equity_water = max(0,1-550/500)=0
lat_close = [_fac(5, 600)]
hp_close  = [_fac(5, 600)]
pts, expl = _c6_equity(sh_far_w, wp_far, lat_close, hp_close, PARCEL_TALL)
check("Shelter >500 m from water -> score <= 7 (water equity=0)", pts <= 7,
      f"got {pts} -- {expl}")

# ── 3. No health post -> equity_health = 0 -> max score <= 7 (= round((1+1+0)/3 * 10))
sh = [_shelter(200, 200)]
pts, expl = _c6_equity(sh, [_fac(200,200)], [_fac(200,200)], [], PARCEL)
check("No health post -> score <= 7", pts <= 7, f"got {pts} -- {expl}")

# ── 4. No shelters -> 10 (N/A)
pts, expl = _c6_equity([], [_fac(200,200)], [_fac(200,200)], [_fac(200,200)], PARCEL)
check("No shelters -> 10", pts == 10, f"got {pts}")

# ── 5. Robust to single outlier shelter: P90 doesn't penalise 1 outlier in large group
#       9 shelters at facility, 1 shelter far away (>500 m from water).
#       P90 with 10 items: idx = int(0.9*10)=9, s[9] is the outlier (far one).
#       So P90 would equal the outlier distance if it's the worst. For 1 outlier at
#       exactly the 90th pctile, equity_water ~ 0. Confirm this is the expected behaviour.
#       The key property: if the single outlier is below 90th pctile position (i.e. fewer
#       than 10% are outliers), the P90 is NOT penalised. Test 1 outlier in 20 shelters.
PARCEL2 = Polygon([(0,0),(10,0),(10,700),(0,700)])
shelters_20 = [_shelter(5, 600)] * 19 + [_shelter(5, 50)]  # 1 outlier far from water
wp2 = [_fac(5, 600)]    # water near 19 shelters, 550 m from outlier
lat2 = [_fac(5, 600)]
hp2  = [_fac(5, 600)]
pts_outlier, expl_out = _c6_equity(shelters_20, wp2, lat2, hp2, PARCEL2)
# P90 of 20 items: idx=int(0.9*20)=18; sorted dists: 19x~0 m + 1x~550 m -> s[18]=~0 m
# So equity_water should be high (not penalised by 1 outlier in 20)
check("1 outlier in 20 shelters: P90 not penalised (equity high)", pts_outlier >= 6,
      f"got {pts_outlier} -- {expl_out}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
