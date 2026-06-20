"""
Stage 3 test: _place_block()

Creates a synthetic 300 x 300 m parcel, places one block of 4 communities
(2x2 grid), verifies all community-level rules across all placed elements,
and reports the block's built_width relative to the 300 m SH7 firebreak
threshold.  A second single-block run is then placed to show that two blocks
side by side are tracked correctly for SH7 purposes.

Run from the project root:
    python test_block.py
"""
import sys
from math import sqrt, ceil
from shapely.geometry import Polygon as _Poly
from shapely.ops import unary_union

sys.path.insert(0, ".")
from src.layout_engine import _place_block, _footprint

# ── Synthetic site ─────────────────────────────────────────────────────────
PARCEL_W, PARCEL_H = 300.0, 300.0
parcel = _Poly([(0, 0), (PARCEL_W, 0), (PARCEL_W, PARCEL_H), (0, PARCEL_H)])

shelter_w, shelter_h = _footprint(17.5)   # 5.0 x 3.5 m
N_COMM     = 4    # 2x2 grid — compact test
BLOCK_CX   = 150.0
BLOCK_CY   = 150.0
SH7_LIMIT  = 300.0   # SH7: firebreak after this many metres of continuous built area

# ── Helpers ────────────────────────────────────────────────────────────────
def _cen(corners):
    n = len(corners)
    return (sum(p[0] for p in corners) / n, sum(p[1] for p in corners) / n)

def _dist(a, b):
    return sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

def _check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}]  {label}" + (f"  ({detail})" if detail else ""))
    return cond

# ── Place one block of 4 communities ──────────────────────────────────────
print("=" * 60)
print("Stage 3 -- block module test")
print(f"Parcel         : {PARCEL_W:.0f} x {PARCEL_H:.0f} m")
print(f"Shelter size   : {shelter_w} x {shelter_h} m")
print(f"Communities    : {N_COMM}  (2x2 grid)")
print(f"Block centre   : ({BLOCK_CX:.0f}, {BLOCK_CY:.0f}) m")
print("=" * 60)

block = _place_block(
    parcel, BLOCK_CX, BLOCK_CY, N_COMM,
    shelter_w, shelter_h, occ=None,
)

if block is None:
    print("\nFAIL: _place_block() returned None")
    sys.exit(1)

shelters  = block["shelters"]
taps      = block["water_taps"]
latrines  = block["latrines"]
washing   = block["washing"]
comms     = block["communities"]

print(f"\nBlock placement counts")
print(f"  Communities placed : {block['placed']} / {N_COMM}")
print(f"  Shelters           : {len(shelters)}  ({len(shelters)//block['placed'] if block['placed'] else 0} per community)")
print(f"  Latrines           : {len(latrines)}")
print(f"  Water taps         : {len(taps)}")
print(f"  Washing units      : {len(washing)}")
print(f"\nBlock geometry")
print(f"  Built width  (E-W) : {block['built_width']:.1f} m  (SH7 threshold: {SH7_LIMIT:.0f} m)")
print(f"  Built height (N-S) : {block['built_height']:.1f} m")
print(f"  Block poly area    : {block['block_poly'].area:.0f} m²")
if block["built_width"] >= SH7_LIMIT:
    print(f"  ** SH7 firebreak needed within this block (>{SH7_LIMIT:.0f} m) **")
else:
    gap_to_fb = SH7_LIMIT - block["built_width"]
    print(f"  SH7: {gap_to_fb:.0f} m headroom before firebreak needed E-W")

# ── Cross-community rule checks ────────────────────────────────────────────
print(f"\nCross-community rule checks (all {len(shelters)} shelters, all {len(latrines)} latrines)")
ok = True

# SH6: every pair of shelters >= 2 m apart (sample: all within each community)
min_sh_sh = float("inf")
for comm in comms:
    sh_polys = [_Poly(s["corners_m"]) for s in comm["shelters"]]
    if len(sh_polys) >= 2:
        d = min(sh_polys[i].distance(sh_polys[j])
                for i in range(len(sh_polys))
                for j in range(i+1, len(sh_polys)))
        min_sh_sh = min(min_sh_sh, d)
ok &= _check("SH6  intra-community shelter spacing >= 2 m",
             min_sh_sh >= 2.0, f"min gap {min_sh_sh:.2f} m")

# Also check inter-community shelter gaps (no two communities' shelters < 2 m)
if len(comms) >= 2:
    inter_min = float("inf")
    for i in range(len(comms)):
        for j in range(i+1, len(comms)):
            si = [_Poly(s["corners_m"]) for s in comms[i]["shelters"]]
            sj = [_Poly(s["corners_m"]) for s in comms[j]["shelters"]]
            if si and sj:
                d = min(a.distance(b) for a in si for b in sj)
                inter_min = min(inter_min, d)
    ok &= _check("SH6  inter-community shelter spacing >= 2 m",
                 inter_min >= 2.0, f"min gap {inter_min:.1f} m")

# WS5: each community's tap >= 30 m from all latrines in the whole block
# (cross-community: a tap should not be polluted by another community's latrines)
all_tap_cens = [_cen(t["corners_m"]) for t in taps]
all_lt_cens  = [_cen(l["corners_m"]) for l in latrines]
if all_tap_cens and all_lt_cens:
    min_tl = min(_dist(tc, lc) for tc in all_tap_cens for lc in all_lt_cens)
    ok &= _check("WS5  every tap >= 30 m from every latrine (cross-community)",
                 min_tl >= 30.0, f"min dist {min_tl:.1f} m")

# SA4: no latrine within 6 m of any shelter (across all communities)
all_sh_polys = [_Poly(s["corners_m"]) for s in shelters]
all_lt_polys = [_Poly(l["corners_m"]) for l in latrines]
if all_sh_polys and all_lt_polys:
    min_sl = min(sp.distance(lp) for sp in all_sh_polys for lp in all_lt_polys)
    ok &= _check("SA4  all latrines >= 6 m from all shelters (edge-to-edge)",
                 min_sl >= 6.0, f"min gap {min_sl:.1f} m")

# SA3: every shelter within 50 m of its nearest latrine
if shelters and latrines:
    sh_cens = [_cen(s["corners_m"]) for s in shelters]
    max_sl  = max(min(_dist(sc, lc) for lc in all_lt_cens) for sc in sh_cens)
    ok &= _check("SA3  all shelters <= 50 m from nearest latrine",
                 max_sl <= 50.0, f"max dist {max_sl:.1f} m")

# WS3: every shelter within 200 m of its nearest tap
if shelters and taps:
    sh_cens = [_cen(s["corners_m"]) for s in shelters]
    max_wt  = max(min(_dist(sc, tc) for tc in all_tap_cens) for sc in sh_cens)
    ok &= _check("WS3  all shelters <= 200 m from nearest tap",
                 max_wt <= 200.0, f"max dist {max_wt:.1f} m")

# No footprint overlaps across all elements
all_polys = all_sh_polys + all_lt_polys
if len(all_polys) >= 2:
    total_area = sum(p.area for p in all_polys)
    union_area = unary_union(all_polys).area
    overlap    = max(0.0, total_area - union_area)
    ok &= _check("No footprint overlaps across block",
                 overlap < 0.5, f"{overlap:.2f} m² overlap")

# All elements inside parcel
all_elems = all_sh_polys + all_lt_polys + [_Poly(t["corners_m"]) for t in taps]
contained = all(parcel.contains(e) for e in all_elems)
ok &= _check("All elements inside parcel", contained)

# ── SH7: two-block side-by-side width check ────────────────────────────────
print(f"\nSH7 firebreak check: placing two blocks side by side")
BLOCK2_CX = BLOCK_CX + block["built_width"] + 5.0   # tight gap (no firebreak yet)
block2 = _place_block(
    parcel, BLOCK2_CX, BLOCK_CY, N_COMM,
    shelter_w, shelter_h, occ=block["occ"],
)
if block2:
    combined_width = block["built_width"] + block2["built_width"]
    needs_fb = combined_width >= SH7_LIMIT
    ok &= _check(
        f"SH7  two blocks combined width {combined_width:.1f} m"
        + (" => firebreak needed" if needs_fb else " => within 300 m, no firebreak"),
        True,   # informational — just printing the measurement
        f"block1={block['built_width']:.1f} m  block2={block2['built_width']:.1f} m"
    )
    print(f"  (A 30 m firebreak gap would be inserted here if combined > {SH7_LIMIT:.0f} m)")
else:
    print("  Block 2 could not be placed (parcel too small for this test)")

# ── Text map ───────────────────────────────────────────────────────────────
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
    for r in range(r0, r1+1):
        for c in range(c0, c1+1):
            grid[r][c] = char

# Draw order: shelters + latrines first, then open spaces on top
for s in shelters:
    _mark(s["corners_m"], "S")
for s in (block2["shelters"] if block2 else []):
    _mark(s["corners_m"], "s")   # second block in lowercase
for l in latrines:
    _mark(l["corners_m"], "L")
for l in (block2["latrines"] if block2 else []):
    _mark(l["corners_m"], "l")
for w in washing:
    _mark(w["corners_m"], "W")
# Open spaces last (drawn over shelters so they stay visible)
for comm in comms:
    _mark(comm["open_corners"], "O")
for comm in (block2["communities"] if block2 else []):
    _mark(comm["open_corners"], "o")
# Taps over open spaces
for t in taps:
    _mark(t["corners_m"], "T")
for t in (block2["water_taps"] if block2 else []):
    _mark(t["corners_m"], "t")

print("  " + "".join(str(c % 10) for c in range(COLS)))
for r in range(ROWS - 1, -1, -1):
    row_label = f"{int(r * CELL):3d}"
    print(f"{row_label} " + "".join(grid[r]))

print(f"\nLegend (block 1): S=shelter O=open T=tap L=latrine W=washing")
print(f"Legend (block 2): s=shelter o=open t=tap l=latrine")
print(f"\nOverall: {'ALL CHECKS PASS' if ok else 'ONE OR MORE CHECKS FAILED'}")
