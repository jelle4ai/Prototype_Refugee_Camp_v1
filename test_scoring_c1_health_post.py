"""
Stage 1 test: _c1_health_post_centrality()

Health post at the shelter centroid -> 10; at the far edge -> low.
Run from the project root:
    python test_scoring_c1_health_post.py
"""
import sys
from math import sqrt
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c1_health_post_centrality


def _shelter(cx, cy, w=3.8, h=3.8):
    hw, hh = w / 2, h / 2
    return {"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}


def _fac(cx, cy, w=8, h=8):
    hw, hh = w / 2, h / 2
    return {"corners_m": [(cx-hw,cy-hh),(cx+hw,cy-hh),(cx+hw,cy+hh),(cx-hw,cy+hh)]}


PARCEL_200 = Polygon([(0,0),(200,0),(200,200),(0,200)])   # half-diag ≈ 141 m
PARCEL_400 = Polygon([(0,0),(400,0),(400,200),(0,200)])   # half-diag ≈ 224 m

all_ok = True


def check(name, cond, msg=""):
    global all_ok
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {msg}")
        all_ok = False


# ── 1. HP at exact shelter centroid -> score 10 ───────────────────────────────
shelters_sq = [_shelter(50,50), _shelter(150,50), _shelter(50,150), _shelter(150,150)]
hp_centre   = [_fac(100, 100)]   # centroid of the four shelters
pts, expl = _c1_health_post_centrality(shelters_sq, hp_centre, PARCEL_200)
check("HP at centroid -> 10", pts == 10, f"got {pts} — {expl}")

# ── 2. HP far in a corner, shelters clustered near centre -> score <= 3 ────────
shelters_mid = [_shelter(95,95), _shelter(105,95), _shelter(95,105), _shelter(105,105)]
hp_corner    = [_fac(5, 5)]
pts, expl = _c1_health_post_centrality(shelters_mid, hp_corner, PARCEL_200)
# distance ≈ sqrt(95²+95²) ≈ 134 m; half_diag ≈ 141 m -> score ≈ round((1-0.95)*10) = 1
check("HP at corner, shelters at centre -> score <= 3", pts <= 3, f"got {pts} — {expl}")

# ── 3. No health post -> 0 ────────────────────────────────────────────────────
pts, expl = _c1_health_post_centrality([_shelter(100,100)], [], PARCEL_200)
check("No HP -> 0", pts == 0, f"got {pts}")

# ── 4. No shelters -> 10 (N/A) ────────────────────────────────────────────────
pts, expl = _c1_health_post_centrality([], [_fac(100,100)], PARCEL_200)
check("No shelters -> 10 (N/A)", pts == 10, f"got {pts}")

# ── 5. HP midway between centroid and edge -> intermediate score ───────────────
shelters_mid2 = [_shelter(100, 100)]
hp_mid        = [_fac(200, 100)]   # 100 m away; half_diag ≈ 141 -> (1-100/141)*10 ≈ 2.9 -> 3
pts, expl = _c1_health_post_centrality(shelters_mid2, hp_mid, PARCEL_200)
check("HP midway -> intermediate (1–5)", 1 <= pts <= 5, f"got {pts} — {expl}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
