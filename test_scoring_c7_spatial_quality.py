"""
Stage 7 test: _c7_spatial_quality()

Tests community completeness (50%) and open-space integrity (50%) sub-scores.
Run from the project root:
    python test_scoring_c7_spatial_quality.py
"""
import sys
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from src.scoring import _c7_spatial_quality

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


# 16x20 open space (Appendix F standard) -> area = 320 m^2
def _open_corners(ox, oy):
    return [(ox, oy), (ox+16, oy), (ox+16, oy+20), (ox, oy+20)]


def _make_community(ox, oy, n_shelters=16):
    """Synthetic community at origin (ox, oy) with full open space."""
    from shapely.geometry import Polygon as P
    from shapely.ops import unary_union
    open_corners = _open_corners(ox, oy)
    shelters = [_shelter(ox + 2 + i*5, oy + 25) for i in range(n_shelters)]
    hull_polys = [P(open_corners)] + [P(s["corners_m"]) for s in shelters]
    community_poly = unary_union(hull_polys).convex_hull
    return {
        "shelters": shelters,
        "open_corners": open_corners,
        "community_poly": community_poly,
    }


PARCEL = Polygon([(0,0),(500,0),(500,500),(0,500)])

# ── 1. All communities placed, full open space -> score = 10
#       required = 32 (2 communities), placed = 2, completeness = 1.0 -> 10
#       open_adequacy = 320/320 = 1.0 -> open_score=10
#       sub = round(0.5*10 + 0.5*10) = 10
comm1 = _make_community(10, 10)
comm2 = _make_community(200, 10)
sr_full = {"shelters": comm1["shelters"] + comm2["shelters"],
           "required": 32, "communities": [comm1, comm2]}
pts, expl = _c7_spatial_quality(sr_full, PARCEL)
check("All communities placed, full open space -> 10", pts == 10, f"got {pts} -- {expl}")

# ── 2. Half communities placed -> completeness=5/10 -> sub = round(0.5*5 + 0.5*10) = 8
#       required=32 (2 comms), placed=1
sr_half = {"shelters": comm1["shelters"], "required": 32, "communities": [comm1]}
pts, expl = _c7_spatial_quality(sr_half, PARCEL)
check("Half communities placed -> score = 8", pts == 8, f"got {pts} -- {expl}")

# ── 3. No communities -> 0
sr_nocomm = {"shelters": [_shelter(100,100)], "required": 16, "communities": []}
pts, expl = _c7_spatial_quality(sr_nocomm, PARCEL)
check("No communities -> 0", pts == 0, f"got {pts}")

# ── 4. No shelters (required=0) -> 10
sr_empty = {"shelters": [], "required": 0, "communities": []}
pts, expl = _c7_spatial_quality(sr_empty, PARCEL)
check("No shelters -> 10 (N/A)", pts == 10, f"got {pts}")

# ── 5. Community with no open_corners -> open_adequacy = 0 -> sub degrades
comm_noopen = {"shelters": comm1["shelters"],
               "open_corners": None,
               "community_poly": comm1["community_poly"]}
sr_noopen = {"shelters": comm_noopen["shelters"], "required": 16,
             "communities": [comm_noopen]}
pts, expl = _c7_spatial_quality(sr_noopen, PARCEL)
# completeness=1->10; open=0; sub=round(0.5*10+0.5*0)=5
check("Community missing open space -> score = 5", pts == 5, f"got {pts} -- {expl}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
