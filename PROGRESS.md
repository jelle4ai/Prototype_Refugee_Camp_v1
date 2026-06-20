# Progress Log

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
- Build the feedback and revision stage (stage 5, currently an empty placeholder)
- Roads connect but do not yet follow the three-level hierarchy
- Gradient, orientation, drainage and site suitability remain future work as they need data a flat 2D engine does not have
