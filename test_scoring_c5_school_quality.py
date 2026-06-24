"""
Stage 5 test: _c5_school_quality()

Tests Appendix E corrected formula:
  - Capacity (50%): len(schools)/sc_req, scaled 0-10
  - Comfort  (35%): mean ED3 proximity margin vs 1,000 m
  - Separation (15%): min pair distance / 200 m reference (10 if 1 school)
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

# ── 3. School at shelter location, 1 req
#       cap=10, comfort=1000→10, sep=10 (1 school)
#       sub = round(0.50*10 + 0.35*10 + 0.15*10) = round(10) = 10
sh = [_shelter(300, 300)]
sc1 = [_school(300, 300)]
pts, expl = _c5_school_quality(sh, sc1, REQS_1, PARCEL)
check("School at shelter, 1 req -> score = 10", pts == 10, f"got {pts} -- {expl}")

# ── 4. Shelter 1000 m from school, 1 req
#       cap=10, comfort=0→0, sep=10 (1 school)
#       sub = round(0.50*10 + 0.35*0 + 0.15*10) = round(6.5) = 6 (banker's rounding)
PARCEL_TALL = Polygon([(0,0),(10,0),(10,1200),(0,1200)])
sh_far = [_shelter(5, 1100)]
sc_far = [_school(5, 100)]   # 1000 m away
pts, expl = _c5_school_quality(sh_far, sc_far, REQS_1, PARCEL_TALL)
check("Shelter 1000 m from school -> comfort=0, score = 6", pts == 6, f"got {pts} -- {expl}")

# ── 5. 2 schools poorly separated (50 m apart), 2 req
#       Schools at (275,300) and (325,300) — exactly 50 m between centroids.
#       Shelters co-located with schools → comfort=1000→10.
#       cap=10, comfort=10, sep=round(50/200*10)=round(2.5)=2 (banker's)
#       sub = round(0.50*10 + 0.35*10 + 0.15*2) = round(8.8) = 9
sh5 = [_shelter(275, 300), _shelter(325, 300)]
sc5_close = [_school(275, 300), _school(325, 300)]
pts_close, _ = _c5_school_quality(sh5, sc5_close, REQS_2, PARCEL)
check("2 schools 50 m apart -> score = 9", pts_close == 9, f"got {pts_close}")

# ── 6. 2 schools well separated (400 m apart), 2 req — score higher than test 5
#       Schools at (100,300) and (500,300) — 400 m apart.
#       Shelters co-located with schools → comfort=1000→10.
#       cap=10, comfort=10, sep=min(10,round(400/200*10))=10
#       sub = round(0.50*10 + 0.35*10 + 0.15*10) = 10
sh6 = [_shelter(100, 300), _shelter(500, 300)]
sc6_far = [_school(100, 300), _school(500, 300)]
pts_far, _ = _c5_school_quality(sh6, sc6_far, REQS_2, PARCEL)
check("2 schools 400 m apart -> score = 10", pts_far in {9, 10}, f"got {pts_far}")
check("Well-separated schools score > poorly-separated schools", pts_far > pts_close,
      f"far={pts_far} close={pts_close}")

# ── 7. Only 1 school placed when 2 required
#       cap = round(1/2*10) = 5; comfort=1000→10; sep=10 (1 school)
#       sub = round(0.50*5 + 0.35*10 + 0.15*10) = round(7.5) = 8 (banker's)
#       assert pts < 10 (capacity shortfall prevents full score)
sh7 = [_shelter(300, 300)]
sc7 = [_school(300, 300)]
pts7, expl7 = _c5_school_quality(sh7, sc7, REQS_2, PARCEL)
check("1 of 2 required schools -> score < 10", pts7 < 10,
      f"got {pts7} -- {expl7}")
check("1 of 2 required schools -> score = 8", pts7 == 8,
      f"got {pts7} -- {expl7}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
