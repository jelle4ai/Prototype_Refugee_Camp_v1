"""
Stage 3 test: _c3_food_distribution()

Tests FD3 proximity (site-relative) and FD4 capacity (crowding) sub-scores.
Run from the project root:
    python test_scoring_c3_food_distribution.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c3_food_distribution

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


def _fd(cx, cy):
    return {"corners_m": [(cx-4,cy-4),(cx+4,cy-4),(cx+4,cy+4),(cx-4,cy+4)]}


PARCEL = Polygon([(0,0),(400,0),(400,400),(0,400)])   # diag = 566 m

# ── 1. FD at shelter centroid, 50 shelters / 1 FD -> high prox + good capacity
shelters_50 = [_shelter(200, 200)] * 50   # all at centroid
fd_central  = [_fd(200, 200)]
pts, expl = _c3_food_distribution(shelters_50, fd_central, PARCEL)
check("FD at centroid, 50 shelters/1 -> score >= 7", pts >= 7, f"got {pts} -- {expl}")

# ── 2. FD at far corner, shelters at opposite corner, 250 shelters/1 FD -> low
shelters_corner = [_shelter(10, 10)] * 250
fd_corner = [_fd(390, 390)]   # ~537 m away; avg_d/diag = 537/566 ~ 0.95 -> prox~0
pts, expl = _c3_food_distribution(shelters_corner, fd_corner, PARCEL)
check("FD at far corner, 250 shelters -> score <= 2", pts <= 2, f"got {pts} -- {expl}")

# ── 3. No FD points -> 0
pts, expl = _c3_food_distribution([_shelter(100,100)], [], PARCEL)
check("No FD points -> 0", pts == 0, f"got {pts}")

# ── 4. No shelters -> 10 (N/A)
pts, expl = _c3_food_distribution([], [_fd(200,200)], PARCEL)
check("No shelters -> 10", pts == 10, f"got {pts}")

# ── 5. Capacity: 80 shelters/1 FD -> cap_score=10; 200 shelters/1 FD -> cap_score=0
fd1 = [_fd(200, 200)]
shelters_80  = [_shelter(200, 200)] * 80
shelters_200 = [_shelter(200, 200)] * 200
pts_80,  _  = _c3_food_distribution(shelters_80,  fd1, PARCEL)
pts_200, _  = _c3_food_distribution(shelters_200, fd1, PARCEL)
check("80 shelters/FD scores higher than 200 shelters/FD", pts_80 > pts_200,
      f"80={pts_80} 200={pts_200}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
