"""
Regression tests for _estimate_capacity (site_search.py).

Verifies that the Phase-0 lattice count correctly signals which sites can
comfortably fit a given population (est_capacity >= pop * _CAPACITY_BUFFER).
Uses synthetic rectangular polygons so results are deterministic.

Run from project root:
    python test_capacity_estimator.py
"""
import sys
sys.path.insert(0, ".")

from src.site_search import _estimate_capacity, _CAPACITY_BUFFER
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


print("=" * 60)
print("Capacity estimator tests")
print("=" * 60)

# ── 1. Large rectangular parcel (600×400 m, 2500 pp) ─────────────────────────
# Inset 530×330 m: 10 cols × 7 rows = 70 slots → 5600 pp
# Needs: 2500 × 1.15 = 2875 pp → clearly FITS
cap, n = _estimate_capacity(_rect_latlon(600, 400), _REF_LAT, _REF_LON)
print("\nCase 1: 600×400 m (2500 pp needed)")
_check("70 Phase-0 community slots", n == 70, f"n={n}")
_check("5600 pp estimated capacity",  cap == 5600, f"cap={cap}")
_check(f"FITS 2500 pp with {_CAPACITY_BUFFER}× buffer",
       cap >= 2500 * _CAPACITY_BUFFER, f"cap={cap} vs threshold={2500*_CAPACITY_BUFFER:.0f}")

# ── 2. Scenario E parcel (385×200 m, 1100 pp) ────────────────────────────────
# Inset 315×130 m: 6 cols × 3 rows = 18 slots → 1440 pp
# Needs: 1100 × 1.15 = 1265 pp → FITS
cap, n = _estimate_capacity(_rect_latlon(385, 200), _REF_LAT, _REF_LON)
print("\nCase 2: 385×200 m (1100 pp needed) — Scenario E parcel")
_check("18 Phase-0 community slots", n == 18, f"n={n}")
_check("1440 pp estimated capacity",  cap == 1440, f"cap={cap}")
_check(f"FITS 1100 pp with {_CAPACITY_BUFFER}× buffer",
       cap >= 1100 * _CAPACITY_BUFFER, f"cap={cap} vs threshold={1100*_CAPACITY_BUFFER:.0f}")

# ── 3. Scenario F parcel (450×130 m, 1100 pp) ────────────────────────────────
# Inset 380×60 m: 8 cols × 2 rows = 16 slots → 1280 pp
# Needs: 1265 pp → BARELY FITS (passes estimate, fails at generation with 880 pp;
# the existing shortfall message at generation handles it)
cap, n = _estimate_capacity(_rect_latlon(450, 130), _REF_LAT, _REF_LON)
print("\nCase 3: 450×130 m (1100 pp needed) — Scenario F parcel (borderline)")
_check("16 Phase-0 community slots", n == 16, f"n={n}")
_check("1280 pp estimated capacity",  cap == 1280, f"cap={cap}")
_check(f"FITS 1100 pp with {_CAPACITY_BUFFER}× buffer (borderline, fails at generation)",
       cap >= 1100 * _CAPACITY_BUFFER, f"cap={cap} vs threshold={1100*_CAPACITY_BUFFER:.0f}")

# ── 4. Too-small parcel (60×60 m) — inset is empty ───────────────────────────
cap, n = _estimate_capacity(_rect_latlon(60, 60), _REF_LAT, _REF_LON)
print("\nCase 4: 60×60 m (too small, inset empty)")
_check("0 Phase-0 slots (inset empty)", n == 0, f"n={n}")
_check("0 pp estimated capacity",        cap == 0, f"cap={cap}")

# ── 5. Very narrow parcel (200×80 m) ─────────────────────────────────────────
# Inset 130×10 m: 3 cols × 1 row = 3 slots → 240 pp
# Cannot fit even pop=500 (500 × 1.15 = 575 pp > 240 pp) → TOO SMALL
cap, n = _estimate_capacity(_rect_latlon(200, 80), _REF_LAT, _REF_LON)
print("\nCase 5: 200×80 m — too narrow for pop=500")
_check("3 Phase-0 slots",                     n == 3,   f"n={n}")
_check("240 pp estimated capacity",            cap == 240, f"cap={cap}")
_check(f"DOES NOT FIT 500 pp with {_CAPACITY_BUFFER}× buffer",
       cap < 500 * _CAPACITY_BUFFER, f"cap={cap} vs threshold={500*_CAPACITY_BUFFER:.0f}")

# ── 6. Site-D-like proxy — small irregular site ───────────────────────────────
# Simulate a 5.5 ha site similar to Site D: use a thin L-shape (two rectangles
# that together have ~5.5 ha but with few inset positions).
# Thin strip 350×80 m (2.8 ha) — inset 280×10 m: too tall to have a row,
# so test with 350×100 m: inset 280×30 m → 0 rows (30 < 48 pitch → only 1 row
# at y=gminy, y+48 > gmaxy).
# 350×100 m: Inset 280×30 m, gminy=-15, gmaxy=15.
# y=-15, y+48=33 > 15 → 1 row.  x: 7 cols. 7×1=7 slots → 560 pp.
# Does NOT fit 1100 pp (threshold=1265).
cap, n = _estimate_capacity(_rect_latlon(350, 100), _REF_LAT, _REF_LON)
print("\nCase 6: 350×100 m — Site-D-like proxy (low Phase-0 count)")
_check("few Phase-0 slots (≤ 10)",             n <= 10, f"n={n}")
_check(f"DOES NOT FIT 1100 pp with {_CAPACITY_BUFFER}× buffer",
       cap < 1100 * _CAPACITY_BUFFER, f"cap={cap} vs threshold={1100*_CAPACITY_BUFFER:.0f}")

print()
print("=" * 60)
if all_ok:
    print("ALL CHECKS PASSED")
else:
    print("SOME CHECKS FAILED")
    sys.exit(1)
