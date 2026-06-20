"""
Stage 2 test: _place_community()

Creates a synthetic 200 x 200 m parcel and places one community cluster of
16 families.  Prints geometry counts, rule-check results, and a compact
text map so the layout can be eyeballed without opening the Streamlit app.

Run from the project root:
    python test_community.py
"""
import sys
from math import sqrt, ceil
from shapely.geometry import Polygon as _Poly
from shapely.ops import unary_union

sys.path.insert(0, ".")
from src.layout_engine import _place_community, _footprint

# ── Synthetic site ────────────────────────────────────────────────────────────
PARCEL_W, PARCEL_H = 200.0, 200.0
parcel = _Poly([(0, 0), (PARCEL_W, 0), (PARCEL_W, PARCEL_H), (0, PARCEL_H)])

shelter_w, shelter_h = _footprint(17.5)   # 5.0 m x 3.5 m  (SH1 warm climate)
N_FAMILIES = 16                            # Appendix F: ~16 families per community
# Place community in the upper-centre so latrines (32 m south) stay in parcel
COM_CX, COM_CY = 100.0, 130.0

print("=" * 60)
print("Stage 2 — community module test")
print(f"Parcel        : {PARCEL_W:.0f} x {PARCEL_H:.0f} m")
print(f"Shelter size  : {shelter_w} x {shelter_h} m  (SH1, 17.5 m²)")
print(f"Families      : {N_FAMILIES}  (~{N_FAMILIES*5} people — Appendix F)")
print(f"Community ctr : ({COM_CX:.0f}, {COM_CY:.0f}) m")
print("=" * 60)

result = _place_community(
    parcel, COM_CX, COM_CY, N_FAMILIES,
    shelter_w, shelter_h, occ=None,
)

if result is None:
    print("\nFAIL: _place_community() returned None")
    sys.exit(1)

shelters = result["shelters"]
taps     = result["water_taps"]
latrines = result["latrines"]
washing  = result["washing"]
com_poly = result["community_poly"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _cen(corners):
    n = len(corners)
    return (sum(p[0] for p in corners) / n, sum(p[1] for p in corners) / n)

def _dist(a, b):
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond

# ── Counts ───────────────────────────────────────────────────────────────────
print(f"\nPlacement counts")
print(f"  Shelters  : {len(shelters)} / {N_FAMILIES} target")
print(f"  Latrines  : {len(latrines)} / {ceil(N_FAMILIES*5/20)} target  (SA1: <=20 pp/stall)")
print(f"  Taps      : {len(taps)} / 1 target  (WS2)")
print(f"  Washing   : {len(washing)} / {max(1, ceil(N_FAMILIES*5/100))} target  (SA2)")

# ── Rule checks ──────────────────────────────────────────────────────────────
print(f"\nRule checks")
ok = True

# SH6: >= 2 m between every pair of shelters (full check)
if len(shelters) >= 2:
    sh_polys = [_Poly(s["corners_m"]) for s in shelters]
    min_gap  = min(sh_polys[i].distance(sh_polys[j])
                   for i in range(len(sh_polys))
                   for j in range(i + 1, len(sh_polys)))
    ok &= _check("SH6  shelter spacing >= 2 m",  min_gap >= 2.0,
                 f"min gap {min_gap:.2f} m")

# WS5: tap >= 30 m from every latrine centroid
if taps and latrines:
    tap_cen  = _cen(taps[0]["corners_m"])
    lt_cens  = [_cen(l["corners_m"]) for l in latrines]
    min_tl   = min(_dist(tap_cen, lc) for lc in lt_cens)
    ok &= _check("WS5  tap >= 30 m from latrines", min_tl >= 30.0,
                 f"min dist {min_tl:.1f} m")

# SA4: every latrine >= 6 m from every shelter (edge-to-edge)
if shelters and latrines:
    sh_polys = [_Poly(s["corners_m"]) for s in shelters]
    lt_polys = [_Poly(l["corners_m"]) for l in latrines]
    min_sl   = min(sp.distance(lp) for sp in sh_polys for lp in lt_polys)
    ok &= _check("SA4  latrines >= 6 m from shelters (edge-to-edge)",
                 min_sl >= 6.0, f"min gap {min_sl:.1f} m")

# SA3: every shelter within 50 m of its nearest latrine (centroid)
if shelters and latrines:
    sh_cens = [_cen(s["corners_m"]) for s in shelters]
    lt_cens = [_cen(l["corners_m"]) for l in latrines]
    max_sl  = max(min(_dist(sc, lc) for lc in lt_cens) for sc in sh_cens)
    ok &= _check("SA3  all shelters <= 50 m from nearest latrine (centroid)",
                 max_sl <= 50.0, f"max dist {max_sl:.1f} m")

# WS3: every shelter within 200 m of tap (community-level)
if taps and shelters:
    tap_cen  = _cen(taps[0]["corners_m"])
    sh_cens  = [_cen(s["corners_m"]) for s in shelters]
    max_wdst = max(_dist(sc, tap_cen) for sc in sh_cens)
    ok &= _check("WS3  all shelters <= 200 m from tap",
                 max_wdst <= 200.0, f"max dist {max_wdst:.1f} m")

# All elements inside parcel
all_elems = ([_Poly(s["corners_m"]) for s in shelters]
             + [_Poly(l["corners_m"]) for l in latrines]
             + ([_Poly(taps[0]["corners_m"])] if taps else [])
             + [_Poly(w["corners_m"]) for w in washing])
contained = all(parcel.contains(e) for e in all_elems)
ok &= _check("All elements inside parcel", contained)

# No footprint overlaps between shelters
if len(shelters) >= 2:
    sh_polys   = [_Poly(s["corners_m"]) for s in shelters]
    total_area = sum(p.area for p in sh_polys)
    union_area = unary_union(sh_polys).area
    overlap    = max(0.0, total_area - union_area)
    ok &= _check("No shelter footprint overlaps", overlap < 0.1,
                 f"{overlap:.2f} m² overlap")

# ── Summary metrics ──────────────────────────────────────────────────────────
print(f"\nGeometry summary")
print(f"  Community convex hull : {com_poly.area:.0f} m²  "
      f"({N_FAMILIES*5} pp x 45 m²/pp target = {N_FAMILIES*5*45} m²)")
print(f"  Shelter footprint     : {sum(_Poly(s['corners_m']).area for s in shelters):.0f} m²")
if taps:
    tap_cen = _cen(taps[0]["corners_m"])
    print(f"  Tap position          : ({tap_cen[0]:.1f}, {tap_cen[1]:.1f}) m")
if latrines:
    lt_cens = [_cen(l["corners_m"]) for l in latrines]
    print(f"  Latrine centres       : "
          + ", ".join(f"({x:.1f},{y:.1f})" for x, y in lt_cens))

# ── Text map (80-col grid, 5 m/cell) ────────────────────────────────────────
print(f"\nText map  (each cell ~ 5 x 5 m; origin bottom-left)")
CELL = 5.0
COLS = int(PARCEL_W / CELL)
ROWS = int(PARCEL_H / CELL)

grid = [["."] * COLS for _ in range(ROWS)]

def _mark(corners, char):
    if not corners:
        return
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    c0 = max(0, int(min(xs) / CELL))
    c1 = min(COLS - 1, int(max(xs) / CELL))
    r0 = max(0, int(min(ys) / CELL))
    r1 = min(ROWS - 1, int(max(ys) / CELL))
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            grid[r][c] = char

# Draw order: shelters first, then sanitation, then open space + tap last
# so the shared open space overwrites any shelter that nudged too close (SH12).
for s in shelters:
    _mark(s["corners_m"], "S")
for l in latrines:
    _mark(l["corners_m"], "L")
for w in washing:
    _mark(w["corners_m"], "W")
# Open space and tap drawn last so they are always visible
_mark(result["open_corners"], "O")
if taps:
    _mark(taps[0]["corners_m"], "T")

# Print top-to-bottom (high y at top)
print("  " + "".join(str(c % 10) for c in range(COLS)))
for r in range(ROWS - 1, -1, -1):
    row_label = f"{int(r * CELL):3d}"
    print(f"{row_label} " + "".join(grid[r]))

print(f"\nLegend:  S=shelter  O=open space  T=tap  L=latrine  W=washing  .=empty")
print(f"\nOverall: {'ALL CHECKS PASS' if ok else 'ONE OR MORE CHECKS FAILED'}")
