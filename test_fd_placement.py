"""
test_fd_placement.py — Stage 1 regression
Food distribution: multiple points placed for large populations (FD3/FD4).
Run from project root: python test_fd_placement.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from math import sqrt, ceil
from shapely.geometry import Polygon

from src.layout_engine import place_all_facilities
from src.scoring import compliance_gate


def _parcel_site(w=400.0, h=300.0):
    pts = [(0,0),(w,0),(w,h),(0,h)]
    return {"parcel_polygon_m": pts, "roads_m": []}


def _reqs(population, fd_count=None):
    """Minimal requirements dict matching requirements_engine output structure."""
    shelter_count = ceil(population / 5)
    if fd_count is None:
        if population < 5000:
            fd_count = 1
        elif population <= 10000:
            fd_count = 2
        else:
            fd_count = 3
    return {
        "shelter_units":           {"count": shelter_count},
        "health_posts":            {"count": 1},
        "water_points":            {"count": 0},
        "food_distribution_points":{"count": fd_count},
        "schools":                 {"count": 0},
        "community_space":         {"count": 1},
        "administrative_area":     {"count": 1},
        "worship_facility":        {"count": 0},
        "toilets":                 {"count": 0},
        "washing_facilities":      {"count": 0},
    }


def _centroid(corners):
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (sum(xs)/len(xs), sum(ys)/len(ys))


def _dist(a, b):
    return sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


def test_pop1200_places_multiple_fd_points():
    """1200pp -> 240 shelters -> n_fd = max(1, min(ceil(240/120),6)) = 2."""
    site = _parcel_site()
    reqs = _reqs(1200)
    fac  = place_all_facilities(site, reqs)
    fd   = fac.get("food_distribution", [])
    assert len(fd) >= 2, (
        f"Expected >=2 FD points for 1200pp / 240 shelters, got {len(fd)}"
    )
    print(f"  PASS  1200pp -> {len(fd)} FD points placed")


def test_fd_points_not_clustered():
    """All FD points must be >=20 m apart (not stacked on top of each other)."""
    site = _parcel_site()
    reqs = _reqs(1200)
    fac  = place_all_facilities(site, reqs)
    fd   = fac.get("food_distribution", [])
    cens = [_centroid(f["corners_m"]) for f in fd]
    for i in range(len(cens)):
        for j in range(i+1, len(cens)):
            d = _dist(cens[i], cens[j])
            assert d >= 20, (
                f"FD points {i} and {j} too close: {d:.1f} m < 20 m"
            )
    print(f"  PASS  {len(fd)} FD points all >=20 m apart")


def test_compliance_gate_fd_still_passes():
    """Compliance gate FD count check must still pass (placed ≥ required)."""
    site = _parcel_site()
    reqs = _reqs(1200)
    fac  = place_all_facilities(site, reqs)
    fac.pop("_occupied_geo", None)
    layout = {"shelter_result": {"shelters": [], "required": 0, "placed": 0},
              "facilities": fac, "roads": {"connected": True, "stranded": []}}
    gate = compliance_gate(layout, site, reqs)
    # Find the FD count check
    fd_checks = [c for c in gate["checks"] if "food" in c["name"].lower()]
    for c in fd_checks:
        assert c["pass"], f"FD count compliance check failed: {c}"
    print(f"  PASS  Compliance gate FD check passes ({len(fac['food_distribution'])} placed)")


def test_pop4000_places_up_to_6_fd_points():
    """4000pp -> 800 shelters → ceil(800/80)=10, but capped at 6."""
    site = _parcel_site(600, 500)
    reqs = _reqs(4000)
    fac  = place_all_facilities(site, reqs)
    fd   = fac.get("food_distribution", [])
    assert len(fd) >= reqs["food_distribution_points"]["count"], (
        f"FD placed ({len(fd)}) < required ({reqs['food_distribution_points']['count']})"
    )
    assert len(fd) <= 6, f"FD points ({len(fd)}) exceeded cap of 6"
    print(f"  PASS  4000pp -> {len(fd)} FD points (capped at 6)")


def test_single_fd_still_places_when_pop_tiny():
    """Small population (<80 shelters) -> exactly 1 FD point, near HP (original behaviour)."""
    site = _parcel_site(200, 150)
    reqs = _reqs(300)  # 60 shelters -> n_fd = max(1, min(1,6)) = 1
    fac  = place_all_facilities(site, reqs)
    fd   = fac.get("food_distribution", [])
    assert len(fd) == 1, f"Expected 1 FD point for 300pp / 60 shelters, got {len(fd)}"
    print(f"  PASS  300pp -> 1 FD point (single-point path)")


if __name__ == "__main__":
    test_pop1200_places_multiple_fd_points()
    test_fd_points_not_clustered()
    test_compliance_gate_fd_still_passes()
    test_pop4000_places_up_to_6_fd_points()
    test_single_fd_still_places_when_pop_tiny()
    print("=" * 50)
    print("ALL TESTS PASSED")
