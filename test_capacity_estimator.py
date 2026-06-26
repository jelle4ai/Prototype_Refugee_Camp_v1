"""
Regression tests for _estimate_capacity (site_search.py).

Verifies that the Phase-0 lattice count correctly signals which sites can
comfortably fit a given population using the slot-based buffer:
  fits iff n_phase0 >= ceil(pop / _PP_PER_COMMUNITY) + _MIN_SPARE_SLOTS

Uses synthetic rectangular polygons so results are deterministic.

Run from project root:
    python test_capacity_estimator.py
"""
import sys
from math import ceil
sys.path.insert(0, ".")

from src.site_search import _estimate_capacity, _MIN_SPARE_SLOTS, _PP_PER_COMMUNITY
from src.geocoding import metres_to_latlon

_REF_LAT, _REF_LON = 52.0, 6.0

all_ok = True


def _check(label: str, cond: bool, detail: str = "") -> bool:
    global all_ok
    ok = bool(cond)
    all_ok &= ok
    status = "[PASS]" if ok else "[FAIL]"
    print(f"  {status}  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def _rect_latlon(w_m: float, h_m: float) -> list[tuple[float, float]]:
    """Rectangle w×h metres centred at _REF_LAT/_REF_LON, as lat/lon list."""
    corners = [(-w_m / 2, -h_m / 2), (w_m / 2, -h_m / 2),
               (w_m / 2,  h_m / 2), (-w_m / 2,  h_m / 2)]
    return [metres_to_latlon(x, y, _REF_LAT, _REF_LON) for x, y in corners]


def _fits(n_slots: int, pop: int) -> bool:
    return n_slots >= ceil(pop / _PP_PER_COMMUNITY) + _MIN_SPARE_SLOTS


print("=" * 60)
print("Capacity estimator tests")
print("=" * 60)

# ── 1. Large rectangular parcel (600×400 m, 2500 pp) ─────────────────────────
# Inset 530×330 m: 10 cols × 7 rows = 70 slots → 5600 pp
# n_comm_needed = ceil(2500/80) = 32.  70 >= 33 → FITS
cap, n = _estimate_capacity(_rect_latlon(600, 400), _REF_LAT, _REF_LON)
print("\nCase 1: 600×400 m (2500 pp needed)")
_check("70 Phase-0 community slots", n == 70, f"n={n}")
_check("5600 pp estimated capacity",  cap == 5600, f"cap={cap}")
_check(f"FITS 2500 pp (n={n} >= {ceil(2500/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS} slots needed)",
       _fits(n, 2500))

# ── 2. Scenario E parcel (385×200 m, 1100 pp) ────────────────────────────────
# Inset 315×130 m: 6 cols × 3 rows = 18 slots → 1440 pp
# n_comm_needed = 14.  18 >= 15 → FITS
cap, n = _estimate_capacity(_rect_latlon(385, 200), _REF_LAT, _REF_LON)
print("\nCase 2: 385×200 m (1100 pp needed) — Scenario E parcel")
_check("18 Phase-0 community slots", n == 18, f"n={n}")
_check("1440 pp estimated capacity",  cap == 1440, f"cap={cap}")
_check(f"FITS 1100 pp (n={n} >= {ceil(1100/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS} slots needed)",
       _fits(n, 1100))

# ── 3. Scenario F parcel (450×130 m, 1100 pp) ────────────────────────────────
# Inset 380×60 m: 8 cols × 2 rows = 16 slots → 1280 pp
# n_comm_needed = 14.  16 >= 15 → FITS (borderline; fails at generation due to CS5)
cap, n = _estimate_capacity(_rect_latlon(450, 130), _REF_LAT, _REF_LON)
print("\nCase 3: 450×130 m (1100 pp needed) — Scenario F parcel (borderline)")
_check("16 Phase-0 community slots", n == 16, f"n={n}")
_check("1280 pp estimated capacity",  cap == 1280, f"cap={cap}")
_check(f"FITS 1100 pp (borderline, fails at generation) (n={n} >= {ceil(1100/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS})",
       _fits(n, 1100))

# ── 4. Too-small parcel (60×60 m) — inset is empty ───────────────────────────
cap, n = _estimate_capacity(_rect_latlon(60, 60), _REF_LAT, _REF_LON)
print("\nCase 4: 60×60 m (too small, inset empty)")
_check("0 Phase-0 slots (inset empty)", n == 0, f"n={n}")
_check("0 pp estimated capacity",        cap == 0, f"cap={cap}")

# ── 5. Very narrow parcel (200×80 m) ─────────────────────────────────────────
# Inset 130×10 m: 3 cols × 1 row = 3 slots → 240 pp
# n_comm_needed = ceil(500/80) = 7.  3 < 8 → TOO SMALL
cap, n = _estimate_capacity(_rect_latlon(200, 80), _REF_LAT, _REF_LON)
print("\nCase 5: 200×80 m — too narrow for pop=500")
_check("3 Phase-0 slots",             n == 3,   f"n={n}")
_check("240 pp estimated capacity",   cap == 240, f"cap={cap}")
_check(f"DOES NOT FIT 500 pp (n={n} < {ceil(500/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS} slots needed)",
       not _fits(n, 500))

# ── 6. Site-A-like proxy — 15 slots for 14 communities ───────────────────────
# Site A (Enschede) has 15 Phase-0 slots for pop=1100 (n_comm_needed=14).
# Must PASS with slot-based buffer (15 >= 14+1=15) even though a 15 %
# percentage buffer would incorrectly reject it (15×80=1200 < 1100×1.15=1265).
# Model as 830×115 m: inset 760×45 m → 15 cols (760/54=14.07→15) × 1 row
# (45 < 48 pitch → only 1 row) = 15 slots.
cap, n = _estimate_capacity(_rect_latlon(830, 115), _REF_LAT, _REF_LON)
print("\nCase 6: 830×115 m — Site-A-like proxy (exactly 15 slots, just barely fits)")
_check("15 Phase-0 slots",            n == 15, f"n={n}")
_check("1200 pp estimated capacity",  cap == 1200, f"cap={cap}")
_check(f"FITS 1100 pp with slot buffer (n={n} >= {ceil(1100/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS})",
       _fits(n, 1100))
_check("WOULD FAIL 15% percentage buffer (documenting why slot buffer chosen)",
       cap < 1100 * 1.15, f"cap={cap} < {1100*1.15:.0f}")

# ── 7. Site-D-like proxy — small irregular site ──────────────────────────────
# Simulate a 5.5 ha site similar to Site D: 350×100 m → 6 Phase-0 slots
# (inset 280×30 m → 6 cols × 1 row).  n_comm_needed=14.  6 < 15 → TOO SMALL.
cap, n = _estimate_capacity(_rect_latlon(350, 100), _REF_LAT, _REF_LON)
print("\nCase 7: 350×100 m — Site-D-like proxy (low Phase-0 count)")
_check("few Phase-0 slots (≤ 10)",    n <= 10, f"n={n}")
_check(f"DOES NOT FIT 1100 pp (n={n} < {ceil(1100/_PP_PER_COMMUNITY)+_MIN_SPARE_SLOTS} slots needed)",
       not _fits(n, 1100))

print()
print("=" * 60)
if all_ok:
    print("ALL CHECKS PASSED")
else:
    print("SOME CHECKS FAILED")
    sys.exit(1)
