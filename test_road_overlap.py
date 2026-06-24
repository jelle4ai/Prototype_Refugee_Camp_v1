"""
Stage C test: roads must not cut through shelters or facility footprints
(PA10-16 realism gap).

Runs the full placement pipeline on the same irregular parcel used in
test_stage4.py scenario B (1500 pp, 300 shelters -- the real reported bug
case for shelter under-placement), then checks road segments against every
shelter and facility footprint.

A road segment is allowed to touch the single facility/community it is
connecting TO (it necessarily starts there); it must not cut through any
OTHER shelter or facility.

Landed incrementally per road level (see PROGRESS.md Stage C): main,
secondary and tertiary (footpaths, one per community) are all wired in
and asserted here.

Run from the project root:
    python test_road_overlap.py
"""
import sys
from math import ceil
from shapely.geometry import Polygon as _Poly, LineString as _LS

sys.path.insert(0, ".")
from src.layout_engine import place_all_facilities, place_shelters, place_roads


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def _build_pipeline():
    parcel_pts = [(0, 0), (420, 0), (420, 200), (300, 350), (0, 350)]
    population = 1500
    n_shelters = population // 5
    site = {"parcel_polygon_m": parcel_pts, "roads_m": []}
    reqs = {
        "shelter_units":            {"count": n_shelters, "area_per_unit_m2": 17.5},
        "health_posts":             {"count": 1},
        "water_points":             {"count": max(1, population // 250)},
        "food_distribution_points": {"count": 1},
        "community_space":          {"count": 1},
        "administrative_area":      {"count": 1},
        "schools":                  {"count": max(1, population // 1000)},
        "worship_facility":         {"count": 1},
        "toilets":                  {"count": ceil(population / 20)},
        "washing_facilities":       {"count": ceil(population / 100)},
    }
    facilities = place_all_facilities(site, reqs)
    occupied_geo = facilities.pop("_occupied_geo", None)
    sr = place_shelters(site, reqs, occupied_geo=occupied_geo)
    facilities["water_points"].extend(sr.pop("community_water", []))
    facilities["toilets"].extend(sr.pop("community_latrines", []))
    facilities["washing_facilities"].extend(sr.pop("community_washing", []))
    roads = place_roads(site, sr, facilities)
    return site, sr, facilities, roads


def _obstacle_polys(sr, facilities):
    polys = [_Poly(s["corners_m"]) for s in sr["shelters"]]
    for key in ("health_post", "food_distribution", "community_space",
                "administrative_area", "schools", "worship_facility",
                "water_points", "toilets", "washing_facilities"):
        for item in facilities.get(key, []):
            polys.append(_Poly(item["corners_m"]))
    return polys


def _obstacle_entries(sr, facilities):
    """(id(corners_m), poly) for every shelter/facility footprint -- tagged
    the same way place_roads() tags its own obstacle set, so a community's
    own elements can be excluded precisely rather than by nearest-neighbour
    guessing (a tertiary spur legitimately passes close to several of its
    OWN community's latrines/shelters/tap, not just one)."""
    entries = [(id(s["corners_m"]), _Poly(s["corners_m"])) for s in sr["shelters"]]
    for key in ("health_post", "food_distribution", "community_space",
                "administrative_area", "schools", "worship_facility",
                "water_points", "toilets", "washing_facilities"):
        for item in facilities.get(key, []):
            entries.append((id(item["corners_m"]), _Poly(item["corners_m"])))
    return entries


def _community_own_ids(comm):
    ids = {id(s["corners_m"]) for s in comm.get("shelters", [])}
    ids |= {id(l["corners_m"]) for l in comm.get("latrines", [])}
    ids |= {id(w["corners_m"]) for w in comm.get("washing", [])}
    ids |= {id(t["corners_m"]) for t in comm.get("water_taps", [])}
    return ids


def _cuts_through(line, poly):
    """True if *line* passes through poly's interior for more than a graze."""
    if not line.intersects(poly):
        return False
    inter = line.intersection(poly)
    return inter.length > 0.5


all_ok = True

print("=" * 60)
print("Test -- no road segment cuts through a shelter or facility")
print("=" * 60)

site, sr, facilities, roads = _build_pipeline()
obstacles = _obstacle_polys(sr, facilities)


def _check_segments(label, segs):
    n_bad = 0
    for seg in segs:
        line = _LS(seg["pts_m"])
        # A segment is allowed to touch/originate inside the ONE
        # facility/community it connects -- exclude whichever obstacle is
        # closest to either endpoint (its own source/target) before judging.
        own_idx = min(range(len(obstacles)),
                      key=lambda i: min(obstacles[i].distance(_LS([line.coords[0], line.coords[0]])),
                                        obstacles[i].distance(_LS([line.coords[-1], line.coords[-1]]))))
        for i, poly in enumerate(obstacles):
            if i == own_idx:
                continue
            if _cuts_through(line, poly):
                n_bad += 1
                break
    return _check(f"{label}: no cut-through obstacles ({len(segs)} segments)",
                  n_bad == 0, f"{n_bad} bad segment(s)")


def _check_footpaths(label, segs, communities):
    entries = _obstacle_entries(sr, facilities)
    own_ids_by_comm = [_community_own_ids(c) for c in communities]
    n_bad = 0
    for seg in segs:
        node = seg.get("_node", "")
        ci = int(node.split("_")[1]) if node.startswith("community_") else None
        own_ids = own_ids_by_comm[ci] if ci is not None else set()
        line = _LS(seg["pts_m"])
        for cid, poly in entries:
            if cid in own_ids:
                continue
            if _cuts_through(line, poly):
                n_bad += 1
                break
    return _check(f"{label}: no cut-through obstacles ({len(segs)} segments)",
                  n_bad == 0, f"{n_bad} bad segment(s)")


all_ok &= _check_segments("main_road", roads["main_road"])
all_ok &= _check_segments("secondary_roads", roads["secondary_roads"])
all_ok &= _check_footpaths("footpaths", roads["footpaths"], sr["communities"])

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
