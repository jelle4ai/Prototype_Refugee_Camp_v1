# Progress Log

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
