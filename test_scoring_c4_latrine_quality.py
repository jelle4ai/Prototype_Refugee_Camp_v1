"""
Stage 4 test: _c4_latrine_quality()

Tests SA3 comfort margin (70%) and SA9 spread (30%) sub-scores.
Run from the project root:
    python test_scoring_c4_latrine_quality.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c4_latrine_quality

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


def _lat(cx, cy):
    return {"corners_m": [(cx-1,cy-1),(cx+1,cy-1),(cx+1,cy+1),(cx-1,cy+1)]}


PARCEL = Polygon([(0,0),(300,0),(300,300),(0,300)])   # diag = 424 m

# ── 1. Shelter at same location as latrine -> comfort = 50, comfort_score=10
#       1 latrine -> spread=2 -> sub = round(0.7*10 + 0.3*2) = round(7.6) = 8
sh = [_shelter(100, 100)]
lt1 = [_lat(100, 100)]
pts, expl = _c4_latrine_quality(sh, lt1, PARCEL)
check("Shelter at latrine, 1 latrine -> score = 8", pts == 8, f"got {pts} -- {expl}")

# ── 2. Shelter exactly 50 m from latrine -> comfort = 0 -> score depends only on spread
sh_far = [_shelter(150, 150)]
lt_50  = [_lat(150, 200)]   # 50 m away
pts, expl = _c4_latrine_quality(sh_far, lt_50, PARCEL)
check("Shelter 50 m from latrine -> comfort=0, score = round(0+0.3*2)=1", pts <= 2,
      f"got {pts} -- {expl}")

# ── 3. Multiple latrines well spread, shelters co-located with them -> high score
#       Shelters at latrines -> comfort=50 (max). Latrines spread across parcel ->
#       spread=10. sub = round(0.7*10 + 0.3*10) = 10.
sh_many = [_shelter(10,10), _shelter(290,290)]
lt_spread = [_lat(10, 10), _lat(290, 290)]
pts, expl = _c4_latrine_quality(sh_many, lt_spread, PARCEL)
check("Well-spread latrines with nearby shelters -> score = 10", pts == 10, f"got {pts} -- {expl}")

# ── 4. No latrines -> 0
pts, expl = _c4_latrine_quality([_shelter(100,100)], [], PARCEL)
check("No latrines -> 0", pts == 0, f"got {pts}")

# ── 5. No shelters -> 10 (N/A)
pts, expl = _c4_latrine_quality([], [_lat(100,100)], PARCEL)
check("No shelters -> 10", pts == 10, f"got {pts}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
