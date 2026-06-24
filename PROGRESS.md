# Progress Log

## Date: 24 June 2026 (autonomous session)

## HANDOFF

Worked unsupervised through Stages A, B, C of the session plan in full;
stopped before Stage D (optional) rather than force an ambiguous,
multi-layer feature. Every commit below is green (full regression suite
passes after each one). Verification throughout was scripted Python tests
(no browser) per session rules.

**Stage A — entrance fix (DONE, was the open thread from 22 June):**
- `4e749c0` — `_entry_point()` now selects the access road by alongside-
  length within an 8 m buffer (not single nearest-point distance), and
  projects the entrance onto the boundary at the midpoint of that road's
  longest alongside-stretch, not its single closest point. Fixes the
  exact bug diagnosed on 22 June: a corner-proximate stub road beating a
  genuine hundreds-of-metres frontage road. `test_entrance.py` added —
  synthetic fixture (corner stub vs. long frontage road) since real OSM
  trace geometry could not be captured without a browser run.
- `c8a1386` — stripped the temporary debug `print()` trace from
  `_entry_point` (was marked "MUST BE REMOVED" in its docstring).

**Stage B — main road shape (DONE):**
- `d1659be` — added `test_main_road.py` confirming the main road starts
  at the (corrected) entrance and spans most of the parcel's diagonal.
  No engine change was needed here: the entrance fix plus the existing
  entrance→centroid→far-vertex waypoint logic (landed before this
  session) already produce this; the test guards against regression.

**Stage C — road hierarchy / PA10-16 realism gap (DONE, landed in 3 commits):**
This was the biggest item. Before this work, roads were straight lines
regardless of what they crossed — confirmed on the irregular-parcel
scenario from `test_stage4.py` (1500 pp, 300 shelters): 2/2 main road
segments, 15/23 secondary roads, and 3/6 footpaths cut through a shelter
or facility.
- `af004aa` — added `_route_around()` (a small local visibility graph
  using the existing NetworkX dependency) and `_displace_from_obstacles()`
  (pushes a waypoint that lands inside an obstacle — e.g. the parcel
  centroid landing inside the health post, since both target the same
  point — out to just clear of it, since a road can't route "around" a
  point that IS the obstacle). Wired into the main road only. Also fixed:
  `place_roads`'s obstacle set was missing toilets/washing_facilities
  entirely.
- `4d63fb1` — wired the same obstacle avoidance into secondary roads
  (each facility → nearest main-road point), excluding each facility's
  own footprint. Also fixed a real performance problem found while
  testing this: a wide corridor through a dense shelter field blew up the
  O(nodes²) visibility-graph edge check (46s for one scenario). Capped
  the graph to the nearest 8 obstacles within the corridor and switched
  to shapely prepared geometries for the repeated intersection tests
  (~6s after).
- `2bd8198` — replaced footpaths (arbitrary "shelter band" groupings)
  with one tertiary path per COMMUNITY, sourced from each community's own
  shared open space centroid (guaranteed clear of its own shelters by
  construction), routed with the same obstacle avoidance, excluding that
  community's own shelters/latrines/washing/tap. Also fixed a latent
  no-op in the secondary-road exclusion (was comparing against polygons
  built from a different list, so never actually excluded anything — see
  note below). Junctions fall out of the existing NetworkX graph
  construction for free (no separate mechanism needed): a path attaching
  to the nearest point on the main/secondary network becomes a graph
  node.

  Worth knowing for next time: most communities turn out to need no
  explicit tertiary segment at all, because the obstacle-avoidance router
  threads straight through communities' open spaces (deliberately not
  obstacles) on its way across the site, rather than detouring around
  them — so many communities already sit "on" the routed network by the
  time tertiary paths are considered. Confirmed via direct distance
  checks this is real routing behaviour, not a bug. The required
  guarantee (every community reachable from the entrance through the
  road graph, zero stranded nodes) holds regardless and is what's tested.

  Tests: `test_road_overlap.py` (no main/secondary/footpath segment cuts
  through a shelter or other facility — extended in place across the
  three Stage C commits rather than duplicated) and
  `test_road_connectivity.py` (every community reachable, zero stranded
  nodes, on both a single-community parcel and the 19-community scenario
  B from `test_stage4.py`).

**Stage D — facility numbering (NOT STARTED, deliberate stop):**
Per the session brief, this is optional and explicitly not to be forced
if ambiguous. Looked at what it would take before stopping:
- No facility numbering/labelling exists anywhere yet — `app.py` renders
  multi-instance types (schools, worship_facility) as a single grouped
  layer with a count label (e.g. "Schools (2)"), not individual numbered
  markers. "Move school 2 east" is meaningless to a planner today because
  there is no "2" visible on the map for them to have meant.
- Making it real needs three layers to land together: (1) a UI change in
  `app.py` to render and label individual instances on the map, (2) a
  classifier change in `src/feedback.py` to extract an ordinal/index from
  phrases like "school 2" or "the second school" (the prompt currently
  explicitly instructs the LLM to decline these — see the "SINGLING OUT
  ONE SPECIFIC INSTANCE" rule), and (3) an engine change in
  `src/layout_engine.py`'s `move_facility()`, which currently hard-rejects
  any key with `len(items) != 1` (line ~1172) and would need an index
  parameter threaded through from the classifier, through `app.py`'s
  call sites, to `facilities[key][index]`.
- The UI labelling half of this cannot be verified by a scripted Python
  test — this session's verification rule is scripts only, no browser —
  so doing Stage D properly means either accepting unverified UI work or
  pulling in a verify/browser pass, both of which are outside what this
  autonomous session is set up to safely do.
- Recommended next step: pick up Stage D in a session that can verify
  the UI half by running the app, OR scope a smaller engine-only slice
  first (e.g. accept a 1-based index in `move_facility()` and surface it
  through the API without yet exposing it in the planner-facing
  classifier/UI), and land map labelling as a separate, browser-verified
  step.

No assumptions made that aren't stated above; nothing was guessed past
what each commit's own test asserts. Full regression suite (test_block.py,
test_community.py, test_move_facility.py, test_shelter_placement.py,
test_stage4.py, plus all Stage A/B/C tests) was green at every commit
in this session. Note for next session: `test_move_facility.py` and
`test_shelter_placement.py` take noticeably longer to run (~85s and ~15-20s
respectively) than the rest of the suite — confirmed via profiling this
is pre-existing (`_union_add`'s shapely `union()` calls inside
`move_facility`, nothing touched this session), not a regression from
this session's road work; budget for it if running the full suite with a
short timeout.

## Date: 22 June 2026

## What works
- Stage 5 facility-move execution is done. For the four single-instance facility types (health post, food distribution, community space, administrative area), a classified `move_facility` request now actually repositions that facility: `move_facility()` (src/layout_engine.py) reuses the optimiser's occupied-geometry construction (roads, shelters, every other facility) and its `_nudge()` search, walked along the requested compass direction instead of searched in 8 directions. Moves travel a default ~26 m (MOVE_DEFAULT_DISTANCE_M) so they're visible on the map, or an explicit distance if the planner states one (e.g. "move the food distribution 100 metres north" — `distance_m` added to the feedback classifier's whitelist in src/feedback.py). If the full requested distance is blocked partway, the facility lands at the furthest valid position short of it and the result names what blocked further progress; if it cannot move at all, it is rejected and names every piece of geometry the nearest attempted position actually collided with (parcel boundary / road network / shelters / a specific other facility — not just the first match in a fixed priority order). After a successful move, app.py recomputes the compliance gate and quality score on a trial layout before committing; if the move would newly break a hard compliance check, it is rejected and the check is named, even though the move was geometrically possible — consistent with the geometry-rejection logic. A move that passes is applied and shown even if the quality score drops — the planner's instruction is carried out and the cost is shown honestly, never silently applied and never refused for a score drop alone. Schools and worship_facility (multi-instance types) and target_facility/"toward" relative moves stay classified but declined, with an honest caption — facility numbering (next step) is needed before single instances of a multi-instance type can be targeted. Tests in test_move_facility.py (8 cases): default distance, explicit distance, partial move with named blocker, and one rejection case per blocker type (parcel boundary, road, shelters, another facility, multiple blockers at once).

- Shelter under-placement bug is fixed. Root cause (diagnosed earlier the same day): the community candidate grid in `place_shelters()` used a 62×82 m pitch too coarse to generate enough slots to reach the required community count — confirmed by every skip-reason counter (firebreak-pushed-outside-inset, CS5-overlap, `_place_community` returning None) being zero, meaning the scan never rejected a candidate, it simply ran out of candidates, while R4 passed with room to spare. Fix: tightened the pitch to **54.0×48.0 m**, derived as the collision-proof minimum from the worst-case community extent — `_place_community` checks every element it places against the occupied geometry except the shared open space, which is added unconditionally, so the binding constraint is that a neighbour's 20×16 m open space must never be able to reach into this community's worst-case structural extent (ring-4 shelter search at the largest shelter footprint in the codebase, 22.5 m² cold-climate units: 42.04 m × 38.04 m from centre). The new pitch sits ~2 m above that ceiling — true regardless of climate, ring depth, or obstruction pattern, not a probabilistic margin — while still giving ~1.96× as many raw grid points as the old pitch. Also fixed: the candidate grid now walks the inset polygon's own bounds (`inset.bounds`) instead of recomputing `parcel.bounds ± margin`, which also fixes a latent zero-candidate trap on very narrow parcels (margin-from-parcel-bounds could invert to zero rows when parcel height < 2×margin). Verified with a new test_shelter_placement.py across shapes, not just the one parcel the previous attempt was verified on: full fill with zero footprint overlap on a large rectangular parcel (512/500 shelters, 128/125 toilets, 32/25 washing), a large irregular notched parcel (480/480, 120/120, 30/24), and a narrow/awkward trapezoid (240/240, 60/60, 15/12); a deliberately too-small parcel correctly fails R4 with an honest capacity message instead of under-placing. Full existing regression suite (test_block.py, test_community.py, test_stage4.py, test_move_facility.py) still passes. One genuine geometric limit remains and is reported honestly rather than treated as a bug: an extreme single-row strip (e.g. 900×90 m) cannot fit a second row at any pitch since the parcel's own height is the constraint, not sampling density — `shortfall_communities`/`shortfall_shelters` and the compliance gate's placed/required counts already surface this correctly.

## What still needs to be done
- The engine cannot yet target one of several same-type facilities individually (facility numbering is a planned feature, needed for single-instance moves on schools/worship_facility and for relative target_facility/"toward" moves).

### Road network / entrance — IN PROGRESS, NOT FINISHED (mid-debugging save)
Stage 1 of the road work (main road spanning the parcel's long axis instead of a 3-point hub) landed cleanly. Entrance placement is the current open thread, picked up right after, and is **not done** — resuming here.

**Done so far:**
- Stage 1 main-road fix: `place_roads()`'s main road now runs entrance → centroid → farthest parcel-boundary vertex (was entrance → centroid → health post, which clustered all three waypoints near the centre and caused secondary roads to radiate from one point). Verified: secondary roads now attach at 4 distinct points along a 300–560 m backbone across three test shapes, fully connected, zero stranded nodes.
- Robust Overpass handling in `src/site_search.py`: `_overpass()` retries up to 2x with 2s/4s backoff on timeouts and 5xx only (not 4xx — confirmed correct with a mocked test covering all four cases). A per-parcel `ss2_roads_cache` means a failed retry no longer destroys a previously-successful fetch for the same parcel. `site["roads_fetch_error"]` is threaded through so the failure reason survives past the site-search screen.
- Layout-stage banner in `app.py`: prominent error banner when a run has no real road data (states plainly that the run is not a valid test of entrance/road placement), a milder note for the genuine "no roads nearby" case, and a one-line success caption when real road data loaded — visible before the map, no debug expander needed.
- `_entry_point()` (src/layout_engine.py) rewritten: was snapping to the nearest *parcel vertex* across a flat pool of every point from every fetched road (so a road's stray node near a corner could win over a road actually running alongside the parcel, and even then could only ever return an existing corner). Rewrite picks the nearest *road* by full-geometry distance, then projects onto the parcel's exterior via `nearest_points` — no longer vertex-locked.

**Open problem — entrance still lands at a corner, not on the real access road:**
Confirmed on two separate real sites via diagnostic trace logging (kept in `_entry_point`, see below) that selecting the road by single-point distance is the wrong criterion: a road can win by merely touching or terminating near a corner (distance ≈ 0–1.3 m) while a genuinely-frontage road that runs alongside the parcel for hundreds of metres loses. Tried an "alongside-length within an 8 m buffer" measure as a diagnostic (length of a road's geometry that stays within 8 m of the parcel, rather than its single closest point) — on real data this measure correctly identifies the real frontage road in both test cases (one site: 532 m alongside vs. the loser's terminal-point proximity; another site: 245.1 m alongside vs. a tiny 58.8 m stub winning today on distance=0.0 m alone). **Not yet wired into the actual selection** — still diagnostic only.
Even once the right road is selected, the projection step (`nearest_points`) still anchors to that road's single closest point along its full length, which can be a corner-proximate spot rather than the middle of the stretch where it actually runs alongside the parcel. Likely fix, not yet implemented: place the entrance at the midpoint of the alongside-stretch (the buffer-intersection piece), not the absolute nearest point.

**Note:** temporary trace logging (`print("[_entry_point DEBUG] ...")`) is deliberately still present in `_entry_point`, marked in its docstring as "MUST BE REMOVED before this function is considered done." Left in across sessions on purpose so the next session can keep reading the trace without re-adding it — strip it once the alongside-length selection + midpoint projection fix lands and is confirmed live.

- Roads connect but do not follow the full three-level hierarchy beyond Stage 1; gradient, orientation, drainage and site suitability remain future work.

## Date: 21 June 2026

## What works
- As of 21 June 2026, the stage 5 layout feedback step is implemented and lives on the layout page below the map, so the planner sees the camp while giving feedback. A narrow JSON-only classifier (src/feedback.py, modelled on conversation.py's _extract_inputs) maps plain-language feedback to a small whitelist of actions and declines everything else in plain language naming what is missing, including ambiguous references to one of several same-type facilities (e.g. "the left school"), which are declined with a usable alternative. Submitting feedback, optimising, or resetting clears the previous message and typed text. The Optimise button now shows a visible result summary: moved N facilities with score before and after, or an explicit "no improvement, layout already near-optimal" line for a genuine no-op. A move-count over-reporting bug (counting the convergence log line) was fixed.

## Date: 20 June 2026

## What works
- Stages 1 to 4 are functional end to end
- LLM conversation collects all required inputs
- OSM site search finds and presents candidate parcels
- Summary screen allows the planner to review and correct inputs
- Layout engine places all facilities in priority order with a connected road network
- End-to-end test passes and returns a score of 69/100
- Block-based shelter placement is implemented. Shelters are placed in the Appendix F module hierarchy: 16 families form a community cluster with its own water tap, latrines and washing around a visible shared open space; communities group into blocks. Water, latrines and washing were moved out of place_all_facilities() into the community module and merged back in app.py's _run_placement() so the compliance gate counts them. New functions _place_community() and _place_block() in src/layout_engine.py, with tests test_community.py, test_block.py and test_stage4.py.
- Irregular-parcel placement is working. The old bounding-box block grid left shelters unplaced when blocks fell outside the polygon or past the SH7 firebreak shift. Replaced with a community-scan approach that walks the actual parcel interior, inserts SH7 firebreaks per y-band, filters candidate positions where the community open space would clash with CS5 facilities, and uses a WS5-derived margin (35 m) instead of a flat 50 m. Verified on a 420 x 350 m cut-corner parcel with 1500 people: 304/300 shelters placed, 76/75 toilets, 19/15 washing, full compliance gate pass (17/17 checks), zero footprint overlap.

## What still needs to be done
- Not yet built: feedback currently only classifies and declines; executing a supported facility move is the next step. The engine cannot yet target one of several same-type facilities individually (facility numbering is a planned feature).
- Also still open from before: roads connect but do not follow the three-level hierarchy; gradient, orientation, drainage and site suitability remain future work.
