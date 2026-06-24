"""
Stage 8 test: _c8_road_network()

Tests PA3 connectivity (5 pts), PA4 footpath coverage (3 pts), PA6 hierarchy (2 pts).
Run from the project root:
    python test_scoring_c8_road_network.py
"""
import sys

sys.path.insert(0, ".")
from src.scoring import _c8_road_network

all_ok = True


def check(name, cond, msg=""):
    global all_ok
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {msg}")
        all_ok = False


# Helper shelter_result: required=32 => required_comms=2
SR_32 = {"required": 32, "communities": []}

def _roads(connected=True, stranded=None, n_footpaths=4, n_secondary=3, main=True):
    return {
        "connected": connected,
        "stranded": stranded or [],
        "footpaths": [{"pts_m": []}] * n_footpaths,
        "secondary_roads": [{"pts_m": []}] * n_secondary,
        "main_road": [{"pts_m": []}] if main else [],
    }

# ── 1. Fully connected, many footpaths, main + secondary -> score = 10
#       PA3=5, PA4=round(4/2*3)=min(3,6)=3, PA6=2; total=10
roads_perfect = _roads(connected=True, n_footpaths=4, n_secondary=3, main=True)
pts, expl = _c8_road_network(roads_perfect, SR_32)
check("Fully connected + footpaths + hierarchy -> 10", pts == 10, f"got {pts} -- {expl}")

# ── 2. Disconnected (2 stranded) -> PA3=max(0,5-4)=1; + footpaths + hierarchy -> total<=6
roads_disconn = _roads(connected=False, stranded=["A","B"], n_footpaths=4, n_secondary=2, main=True)
pts, expl = _c8_road_network(roads_disconn, SR_32)
check("2 stranded nodes -> score <= 6", pts <= 6, f"got {pts} -- {expl}")

# ── 3. No roads -> 5 (conservative)
pts, expl = _c8_road_network({}, SR_32)
check("No roads -> 5", pts == 5, f"got {pts}")

# ── 4. Connected, no footpaths, main only -> PA3=5, PA4=0, PA6=1+0=1; total=6
roads_nofp = _roads(connected=True, n_footpaths=0, n_secondary=0, main=True)
pts, expl = _c8_road_network(roads_nofp, SR_32)
check("Connected, no footpaths, main only -> 6", pts == 6, f"got {pts} -- {expl}")

# ── 5. Connected, footpaths but no secondary, no main -> PA3=5, PA4=3, PA6=0; total=8
roads_nosc = _roads(connected=True, n_footpaths=4, n_secondary=0, main=False)
pts, expl = _c8_road_network(roads_nosc, SR_32)
check("Connected, footpaths, no hierarchy -> 8", pts == 8, f"got {pts} -- {expl}")

print("=" * 50)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
