"""
Stage 5 test: _c5_school_quality()

Tests ED3 comfort margin (60%) and ED5 spread (40%) sub-scores.
Run from the project root:
    python test_scoring_c5_school_quality.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c5_school_quality

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


def _school(cx, cy):
    return {"corners_m": [(cx-20,cy-20),(cx+20,cy-20),(cx+20,cy+20),(cx-20,cy+20)]}


PARCEL = Polygon([(0,0),(600,0),(600,600),(0,600)])
REQS_1 = {"schools": {"count": 1}}
REQS_2 = {"schools": {"count": 2}}
REQS_0 = {"schools": {"count": 0}}

# ── 1. Schools required=0 -> 10 (N/A)
pts, expl = _c5_school_quality([_shelter(100,100)], [], REQS_0, PARCEL)
check("No schools required -> 10", pts == 10, f"got {pts}")

# ── 2. Schools required but none placed -> 0
pts, expl = _c5_school_quality([_shelter(100,100)], [], REQS_1, PARCEL)
check("Required school, none placed -> 0", pts == 0, f"got {pts}")

# ── 3. School at shelter location, 1 school req -> comfort=1000, spread=5
#       sub = round(0.6*10 + 0.4*5) = round(6+2) = 8
sh = [_shelter(300, 300)]
sc1 = [_school(300, 300)]
pts, expl = _c5_school_quality(sh, sc1, REQS_1, PARCEL)
check("School at shelter, 1 req -> score = 8", pts == 8, f"got {pts} -- {expl}")

# ── 4. All shelters 1000 m from school -> comfort=0; 1 school -> spread=5
#       sub = round(0.6*0 + 0.4*5) = 2
PARCEL_TALL = Polygon([(0,0),(10,0),(10,1200),(0,1200)])
sh_far = [_shelter(5, 1100)]
sc_far = [_school(5, 100)]   # 1000 m away
pts, expl = _c5_school_quality(sh_far, sc_far, REQS_1, PARCEL_TALL)
check("Shelter 1000 m from school -> comfort=0, score = 2", pts == 2, f"got {pts} -- {expl}")

# ── 5. Two schools well spread -> spread score higher than 1 school case
sh2 = [_shelter(100,100), _shelter(500,500)]
sc2 = [_school(100,100), _school(500,500)]
pts_2, _ = _c5_school_quality(sh2, sc2, REQS_2, PARCEL)
pts_1, _ = _c5_school_quality(sh2, [_school(300,300)], REQS_1, PARCEL)
check("2 spread schools >= 1 central school", pts_2 >= pts_1,
      f"2-school={pts_2} 1-school={pts_1}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
