"""
Stage 5 test: move_facility()

Exercises the directional single-instance move against synthetic sites:
  - default distance travels a visible ~25-30 m, not a tiny step (success)
  - an explicit distance is honoured (success)
  - a partially-blocked direction lands at the furthest valid position
    short of the requested distance, rather than failing outright (success)
  - parcel boundary leaves no room in any direction (rejected)
  - a road occupies the entire band in the requested direction (rejected)
  - shelters occupy the entire band in the requested direction (rejected)
  - another facility occupies the entire band (rejected, names that facility)
  - a candidate position is blocked by more than one thing at once (rejected,
    names all of them — not just the highest-priority one)

Run from the project root:
    python test_move_facility.py
"""
import sys

sys.path.insert(0, ".")
from src.layout_engine import move_facility, _rect, MOVE_DEFAULT_DISTANCE_M


def _check(label, cond, detail=""):
    cond = bool(cond)
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond


def _square_parcel(size):
    return [(0, 0), (size, 0), (size, size), (0, size)]


def _band_lines(x_start, x_end, y_lo, y_hi, spacing=2.0):
    """Parallel vertical lines spaced so their 1 m road buffers merge into a
    continuous wall from x_start to x_end, covering the full y_lo..y_hi span."""
    lines, x = [], x_start + 1.0
    while x < x_end:
        lines.append([(x, y_lo), (x, y_hi)])
        x += spacing
    return lines


all_ok = True

# ── 1. Default distance: ample room, no explicit distance given ────────────
print("=" * 60)
print("Test 1 -- default distance is a visible ~25-30 m, not a tiny step")
print("=" * 60)
site1 = {"parcel_polygon_m": _square_parcel(100.0), "roads_m": []}
facilities1 = {"health_post": [{"corners_m": _rect(50, 50, 8, 8)}]}
shelter_result1 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site1, facilities1, shelter_result1, {}, "health_post", "east"
)
all_ok &= _check("move accepted", reason is None, f"reason={reason}")
all_ok &= _check("moved the full default distance",
                 moved_m is not None and abs(moved_m - MOVE_DEFAULT_DISTANCE_M) < 0.01,
                 f"moved_m={moved_m}")
all_ok &= _check("default distance is in the 25-30 m range",
                 25.0 <= MOVE_DEFAULT_DISTANCE_M <= 30.0, f"{MOVE_DEFAULT_DISTANCE_M}")
all_ok &= _check("no partial-block note on a full move", blocked_by is None, f"{blocked_by}")

# ── 2. Explicit distance is honoured ────────────────────────────────────────
print("=" * 60)
print("Test 2 -- explicit distance is honoured")
print("=" * 60)
site2 = {"parcel_polygon_m": _square_parcel(100.0), "roads_m": []}
facilities2 = {"health_post": [{"corners_m": _rect(20, 50, 8, 8)}]}
shelter_result2 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site2, facilities2, shelter_result2, {}, "health_post", "east", distance_m=40.0
)
all_ok &= _check("move accepted", reason is None, f"reason={reason}")
all_ok &= _check("moved the requested 40 m",
                 moved_m is not None and abs(moved_m - 40.0) < 0.01, f"moved_m={moved_m}")
all_ok &= _check("no partial-block note on a full move", blocked_by is None, f"{blocked_by}")

# ── 3. Partially blocked: lands as far as it can, short of the target ──────
print("=" * 60)
print("Test 3 -- partially blocked direction moves as far as it can")
print("=" * 60)
site3 = {
    "parcel_polygon_m": _square_parcel(100.0),
    "roads_m": _band_lines(30.0, 200.0, 0.0, 100.0),
}
facilities3 = {"health_post": [{"corners_m": _rect(10, 50, 8, 8)}]}
shelter_result3 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site3, facilities3, shelter_result3, {}, "health_post", "east"
)
all_ok &= _check("move accepted despite the road further along", reason is None,
                 f"reason={reason}")
all_ok &= _check("moved less than the default distance (blocked partway)",
                 moved_m is not None and 0 < moved_m < MOVE_DEFAULT_DISTANCE_M,
                 f"moved_m={moved_m}")
all_ok &= _check("names what blocked it from going further",
                 blocked_by is not None and "the road network" in blocked_by,
                 f"blocked_by={blocked_by}")

# ── 4. Parcel boundary: item already fills the entire parcel ───────────────
print("=" * 60)
print("Test 4 -- parcel boundary blocks every direction")
print("=" * 60)
site4 = {"parcel_polygon_m": _square_parcel(8.0), "roads_m": []}
facilities4 = {"health_post": [{"corners_m": _rect(4, 4, 8, 8)}]}
shelter_result4 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site4, facilities4, shelter_result4, {}, "health_post", "north"
)
all_ok &= _check("move rejected", reason is not None, f"reason={reason}")
all_ok &= _check("names the parcel boundary", reason and "parcel boundary" in reason, reason)
all_ok &= _check("moved_m is None on rejection", moved_m is None, f"moved_m={moved_m}")
all_ok &= _check("blocked_by names the parcel boundary",
                 blocked_by is not None and "the parcel boundary" in blocked_by, f"{blocked_by}")
all_ok &= _check("unchanged on rejection",
                 fac_out["health_post"][0]["corners_m"] == facilities4["health_post"][0]["corners_m"])

# ── 5. Road occupies the entire eastward band ───────────────────────────────
print("=" * 60)
print("Test 5 -- road blocks the requested direction")
print("=" * 60)
site5 = {
    "parcel_polygon_m": _square_parcel(100.0),
    "roads_m": _band_lines(20.0, 200.0, 0.0, 100.0),
}
facilities5 = {"health_post": [{"corners_m": _rect(20, 50, 8, 8)}]}
shelter_result5 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site5, facilities5, shelter_result5, {}, "health_post", "east"
)
all_ok &= _check("move rejected", reason is not None, f"reason={reason}")
all_ok &= _check("names the road network", reason and "road network" in reason, reason)
all_ok &= _check("does not name shelters", reason and "shelters" not in reason, reason)
all_ok &= _check("blocked_by matches reason", blocked_by == ["the road network"], f"{blocked_by}")

# ── 6. Shelters occupy the entire eastward band ──────────────────────────────
print("=" * 60)
print("Test 6 -- shelters block the requested direction")
print("=" * 60)
site6 = {"parcel_polygon_m": _square_parcel(100.0), "roads_m": []}
facilities6 = {"health_post": [{"corners_m": _rect(20, 50, 8, 8)}]}
shelters6 = [{"corners_m": _rect(x, y, 1.9, 1.9)}
             for x in range(21, 100, 2) for y in range(1, 100, 2)]
shelter_result6 = {"shelters": shelters6}
fac_out, reason, moved_m, blocked_by = move_facility(
    site6, facilities6, shelter_result6, {}, "health_post", "east"
)
all_ok &= _check("move rejected", reason is not None, f"reason={reason}")
all_ok &= _check("names shelters", reason and "shelters" in reason, reason)
all_ok &= _check("does not name the road network", reason and "road network" not in reason, reason)
all_ok &= _check("blocked_by matches reason", blocked_by == ["shelters"], f"{blocked_by}")

# ── 7. Another facility occupies the entire eastward band ──────────────────
print("=" * 60)
print("Test 7 -- another facility blocks the requested direction")
print("=" * 60)
site7 = {"parcel_polygon_m": _square_parcel(100.0), "roads_m": []}
facilities7 = {
    "health_post": [{"corners_m": _rect(20, 50, 8, 8)}],
    "food_distribution": [{"corners_m": _rect(60, 50, 80, 100)}],
}
shelter_result7 = {"shelters": []}
fac_out, reason, moved_m, blocked_by = move_facility(
    site7, facilities7, shelter_result7, {}, "health_post", "east"
)
all_ok &= _check("move rejected", reason is not None, f"reason={reason}")
all_ok &= _check("names food distribution", reason and "food distribution" in reason, reason)
all_ok &= _check("blocked_by matches reason", blocked_by == ["food distribution"], f"{blocked_by}")

# ── 8. Nearest blocked candidate hits both a road and shelters ─────────────
print("=" * 60)
print("Test 8 -- candidate blocked by more than one thing at once")
print("=" * 60)
site8 = {
    "parcel_polygon_m": _square_parcel(100.0),
    "roads_m": _band_lines(20.0, 200.0, 0.0, 100.0),
}
facilities8 = {"health_post": [{"corners_m": _rect(20, 50, 8, 8)}]}
shelters8 = [{"corners_m": _rect(x, y, 1.9, 1.9)}
             for x in range(21, 100, 2) for y in range(1, 100, 2)]
shelter_result8 = {"shelters": shelters8}
fac_out, reason, moved_m, blocked_by = move_facility(
    site8, facilities8, shelter_result8, {}, "health_post", "east"
)
all_ok &= _check("move rejected", reason is not None, f"reason={reason}")
all_ok &= _check("names the road network", reason and "road network" in reason, reason)
all_ok &= _check("also names shelters", reason and "shelters" in reason, reason)
all_ok &= _check("blocked_by names both",
                 blocked_by is not None and "the road network" in blocked_by and "shelters" in blocked_by,
                 f"{blocked_by}")

print("=" * 60)
if all_ok:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
