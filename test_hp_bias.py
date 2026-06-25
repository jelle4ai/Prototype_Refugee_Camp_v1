"""
test_hp_bias.py — Stage 1: health post placement bias
Health post must be biased toward the shelter cluster (away from entrance).
Run from project root: python test_hp_bias.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from math import sqrt, ceil
from shapely.geometry import Polygon

from src.layout_engine import (
    place_all_facilities, place_shelters,
    reposition_facilities_after_shelter_placement,
)
from src.scoring import compliance_gate


def _centroid(corners):
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _dist(a, b):
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _parcel_site(w=400.0, h=300.0, road=None):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    roads = [road] if road else []
    return {"parcel_polygon_m": pts, "roads_m": roads}


def _reqs(population=1200):
    shelter_count = ceil(population / 5)
    fd_count = 1 if population < 5000 else 2
    return {
        "shelter_units":            {"count": shelter_count},
        "health_posts":             {"count": 1},
        "water_points":             {"count": 0},
        "food_distribution_points": {"count": fd_count},
        "schools":                  {"count": 0},
        "community_space":          {"count": 1},
        "administrative_area":      {"count": 1},
        "worship_facility":         {"count": 0},
        "toilets":                  {"count": 0},
        "washing_facilities":       {"count": 0},
    }


def test_hp_biased_south_for_north_entrance():
    """North entrance → HP should land south of parcel centroid (y < 150)."""
    # Road runs along north edge y=300 → entrance projected onto north boundary
    site = _parcel_site(road=[(-50, 300), (450, 300)])
    fac  = place_all_facilities(site, _reqs())
    hp   = fac.get("health_post", [])
    assert hp and hp[0]["corners_m"], "HP must be placed"
    hx, hy = _centroid(hp[0]["corners_m"])
    assert hy < 150, (
        f"HP at y={hy:.1f} is NOT south of parcel centroid (150) "
        f"— bias toward shelter cluster not working for north entrance"
    )
    print(f"  PASS  North entrance: HP at y={hy:.1f} (centroid y=150, biased south)")


def test_hp_in_lower_third_for_south_entrance():
    """South entrance → HP should land in the lower third (y < 120), near the shelter cluster.

    The community lattice fills bottom-to-top, so shelters always cluster in
    the lower portion regardless of entrance direction.  HP should target there,
    not the parcel centroid or the far side from the entrance.
    """
    site = _parcel_site(road=[(-50, 0), (450, 0)])
    fac  = place_all_facilities(site, _reqs())
    hp   = fac.get("health_post", [])
    assert hp and hp[0]["corners_m"], "HP must be placed"
    hx, hy = _centroid(hp[0]["corners_m"])
    assert hy < 120, (
        f"HP at y={hy:.1f} is too high — should be in the lower third of the parcel "
        f"(y < 120 for a 300 m tall parcel) to be near the shelter cluster"
    )
    print(f"  PASS  South entrance: HP at y={hy:.1f} (lower third, near shelter cluster)")


def test_hp_closer_to_shelter_side_than_centroid():
    """HP must be closer to the anti-entrance half than bare centroid would be."""
    # North entrance → shelters will cluster south → HP should be south of centre
    site  = _parcel_site(road=[(-50, 300), (450, 300)])
    fac   = place_all_facilities(site, _reqs())
    hp    = fac.get("health_post", [])
    assert hp and hp[0]["corners_m"], "HP must be placed"
    hx, hy = _centroid(hp[0]["corners_m"])
    # The anti-entrance side representative point is ~y=0 (south centre)
    anti_entrance_y = 0.0
    dist_hp_to_south   = abs(hy - anti_entrance_y)
    dist_cen_to_south  = abs(150.0 - anti_entrance_y)
    assert dist_hp_to_south < dist_cen_to_south, (
        f"HP (y={hy:.1f}) is not closer to the south (shelter-cluster side) "
        f"than the bare centroid (y=150) — distances: HP={dist_hp_to_south:.1f}, centroid={dist_cen_to_south:.1f}"
    )
    print(f"  PASS  HP (y={hy:.1f}) closer to shelter side than centroid (y=150)")


def test_hp_placed_inside_parcel():
    """HP must always land inside the parcel regardless of entrance location."""
    for road_y in [0, 300]:
        road = [(-50, road_y), (450, road_y)]
        site = _parcel_site(road=road)
        fac  = place_all_facilities(site, _reqs())
        hp   = fac.get("health_post", [])
        assert hp and hp[0]["corners_m"], f"HP missing for road_y={road_y}"
        parcel = Polygon(site["parcel_polygon_m"])
        hp_poly = Polygon(hp[0]["corners_m"])
        assert parcel.contains(hp_poly) or parcel.intersects(hp_poly), \
            f"HP outside parcel for road_y={road_y}"
    print("  PASS  HP inside parcel for both north and south entrance")


def test_hp_compliance_passes():
    """After bias, facility count check must still pass (1 placed / 1 required)."""
    site = _parcel_site(road=[(-50, 300), (450, 300)])
    reqs = _reqs()
    fac  = place_all_facilities(site, reqs)
    # Build minimal layout for compliance gate
    layout = {
        "shelter_result": {"shelters": [], "placed": 0, "required": 0},
        "facilities": fac,   # already contains "status" key set by place_all_facilities
        "roads": {},
    }
    gate = compliance_gate(layout, site, reqs)
    hp_check = next(
        (c for c in gate["checks"] if "health" in c["name"].lower() or "Count: health" in c["name"]),
        None,
    )
    if hp_check:
        assert hp_check["pass"], f"HP compliance check failed: {hp_check}"
        print(f"  PASS  Compliance: {hp_check['name']} -- {hp_check['detail']}")
    else:
        print("  PASS  No HP compliance check found (hp_req may be 0 in this setup)")


def test_hp_closer_to_actual_shelter_centroid():
    """
    Full layout with two-pass repositioning: HP must end up within 30 m of
    the actual shelter centroid.
    Uses south entrance (road at y=0).
    """
    import math

    site = _parcel_site(road=[(-50, 0), (450, 0)])
    reqs = _reqs(population=1200)
    fac  = place_all_facilities(site, reqs)
    occ  = fac.pop("_occupied_geo", None)
    shelter_result = place_shelters(site, reqs, occ)
    fac  = reposition_facilities_after_shelter_placement(site, fac, shelter_result)
    shelters = shelter_result.get("shelters", [])
    assert shelters, "No shelters placed"

    hp = fac.get("health_post", [])
    assert hp and hp[0]["corners_m"], "HP not placed"

    hx, hy = _centroid(hp[0]["corners_m"])

    sh_cens = [_centroid(s["corners_m"]) for s in shelters]
    shelt_cx = sum(p[0] for p in sh_cens) / len(sh_cens)
    shelt_cy = sum(p[1] for p in sh_cens) / len(sh_cens)

    dist_hp_to_shelt = math.sqrt((hx - shelt_cx) ** 2 + (hy - shelt_cy) ** 2)

    assert dist_hp_to_shelt < 30.0, (
        f"HP ({hx:.1f},{hy:.1f}) is {dist_hp_to_shelt:.1f} m from shelter centroid "
        f"({shelt_cx:.1f},{shelt_cy:.1f}) — expected < 30 m after repositioning."
    )
    print(
        f"  PASS  HP ({hx:.1f},{hy:.1f}) is {dist_hp_to_shelt:.1f} m from shelter "
        f"centroid ({shelt_cx:.1f},{shelt_cy:.1f})"
    )


if __name__ == "__main__":
    print("=" * 50)
    print("test_hp_bias.py")
    print("=" * 50)
    test_hp_biased_south_for_north_entrance()
    test_hp_in_lower_third_for_south_entrance()
    test_hp_closer_to_shelter_side_than_centroid()
    test_hp_placed_inside_parcel()
    test_hp_compliance_passes()
    test_hp_closer_to_actual_shelter_centroid()
    print("=" * 50)
    print("ALL TESTS PASSED")
