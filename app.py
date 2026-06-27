import copy
import re
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from math import cos, radians
from PIL import Image, ImageDraw
from src.conversation import render_input_stage
from src.layout_engine import (
    place_shelters, place_all_facilities, place_roads,
    optimise_facilities, move_facility, FACILITY_STYLE,
    MOVE_DEFAULT_DISTANCE_M,
    reposition_facilities_after_shelter_placement,
)
from src.scoring import score_layout, compliance_gate
from src.location import render_location_stage
from src.requirements_engine import compute_requirements
from src.site_search import metres_to_latlon
from src.summary import render_summary_stage
from src.feedback import classify_feedback, MOVABLE_FACILITY_KEYS
from src.brand import apply_brand, render_brand_header

def _hamlet_favicon() -> Image.Image:
    """Generate the Hamlet app-icon mark as a 64×64 RGBA PNG for use as page_icon."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    sc = size / 132.0          # viewBox is 132×132 (−66 to +66)
    off = 66.0 * sc            # shift origin to image centre
    indigo     = (31, 71, 136, 255)   # #1F4788
    cream      = (244, 241, 234, 255) # #F4F1EA
    terracotta = (194, 96, 63, 255)   # #C2603F
    # App-icon background: indigo rounded square
    pad = 2 * sc
    bg_r = round(24 * sc)
    d.rounded_rectangle([(pad, pad), (size - pad, size - pad)], radius=bg_r, fill=indigo)
    # 8 rounded rectangles in cream
    rects = [
        (-7, -53), (25.5, -39.5), (39, -7), (25.5, 25.5),
        (-7,  39), (-39.5, 25.5), (-53, -7), (-39.5, -39.5),
    ]
    rr = max(1, round(3 * sc))   # rx=3 in SVG coords → pixels
    for rx, ry in rects:
        x0 = rx * sc + off
        y0 = ry * sc + off
        x1 = x0 + 14 * sc
        y1 = y0 + 14 * sc
        d.rounded_rectangle([(x0, y0), (x1, y1)], radius=rr, fill=cream)
    # Terracotta circle (r=21 in SVG coords)
    r = 21 * sc
    d.ellipse([(off - r, off - r), (off + r, off + r)], fill=terracotta)
    return img


st.set_page_config(page_title="Hamlet", page_icon=_hamlet_favicon(), layout="wide")

STAGES = ["input", "location", "summary", "layout"]

# Fields required to advance from Stage 1 and labels shown in "still needed" text.
_STAGE1_REQUIRED = [
    "city", "population", "men", "women", "children",
    "climate", "duration", "cultural_notes", "special_needs",
]
_FIELD_LABEL = {
    "city": "Location", "population": "Population",
    "men": "Men", "women": "Women", "children": "Children",
    "climate": "Climate", "duration": "Duration",
    "cultural_notes": "Cultural notes", "special_needs": "Special needs",
}

# Facility types whose moves are actually executed. Schools / worship_facility
# stay declined (multi-instance — needs facility numbering, not built yet),
# and target_facility/"toward" relative moves stay declined too (v1 is
# direction-only).
EXECUTABLE_MOVE_KEYS = {
    "health_post", "food_distribution", "community_space", "administrative_area",
}


def _render_fixed_continue(
    label: str,
    enabled: bool,
    missing: list[str],
    btn_key: str,
    target_stage: str,
    bottom: int = 0,
) -> None:
    """Full-width fixed bottom bar with a primary continue button. Content padding is injected
    by each stage wrapper so nothing scrolls underneath."""
    btn_color = "#1F4788" if enabled else "#B0A898"
    btn_cursor = "pointer" if enabled else "not-allowed"
    arrow = " →" if enabled else ""

    count = len(missing)
    hint_html = (
        f'<span style="font-size:0.78rem;color:#8A8579;font-family:Inter,sans-serif;">'
        f'{count} detail{"s" if count != 1 else ""} still needed</span>'
        if missing else ""
    )

    st.markdown(
        f'<div class="hamlet-bot-bar" style="bottom:{bottom}px;">'
        f'{hint_html}'
        f'<button id="hfc-{btn_key}"'
        f' style="background:{btn_color};color:#F4F1EA;border:none;border-radius:8px;'
        f'font-family:Inter,sans-serif;font-weight:500;font-size:0.9rem;'
        f'padding:0.55rem 1.4rem;cursor:{btn_cursor};margin-left:auto;">'
        f'{label}{arrow}</button>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Hidden Streamlit trigger (unique ⏩ prefix; JS hides it then clicks it)
    if st.button(f"⏩{btn_key}", key=f"hfc_{btn_key}", type="primary"):
        if enabled:
            st.session_state["stage"] = target_stage
            st.rerun()

    components.html(
        f"""<script>
(function(){{
  var KEY='{btn_key}',ENABLED={'true' if enabled else 'false'};
  function setup(){{
    var p=window.parent.document;
    /* hide trigger button */
    p.querySelectorAll('button').forEach(function(b){{
      var t=(b.innerText||b.textContent||'').trim();
      if(t==='⏩'+KEY){{
        var w=b.closest('[data-testid="stButton"]')||b.parentElement;
        if(w)w.style.cssText='height:0;overflow:hidden;margin:0;padding:0;';
      }}
    }});
    /* wire fixed HTML button → trigger */
    var btn=p.getElementById('hfc-'+KEY);
    if(btn&&!btn._hfc){{
      btn._hfc=true;
      btn.addEventListener('click',function(){{
        if(!ENABLED)return;
        p.querySelectorAll('button').forEach(function(b){{
          var t=(b.innerText||b.textContent||'').trim();
          if(t==='⏩'+KEY)b.click();
        }});
      }});
    }}
  }}
  setTimeout(setup,120);
}})();
</script>""",
        height=0,
    )


def init_session_state():
    if "stage" not in st.session_state:
        st.session_state["stage"] = "input"
    if "site_inputs" not in st.session_state:
        st.session_state["site_inputs"] = {}


def advance_stage():
    current = st.session_state["stage"]
    idx = STAGES.index(current)
    st.session_state["stage"] = STAGES[(idx + 1) % len(STAGES)]
    st.rerun()


def _navigate_to(target_stage: str) -> None:
    """Navigate backward to target_stage, clearing derived state from later stages."""
    target_idx = STAGES.index(target_stage)
    if target_idx < STAGES.index("layout"):
        st.session_state.pop("layout_result", None)
        _clear_feedback_state()
    st.session_state["stage"] = target_stage
    st.rerun()


def render_stepper(current_stage: str) -> None:
    """Sticky progress bar. Completed steps show green ✓ and are clickable (back-nav)."""
    labels = ["Information gathering", "Site selection", "Review and confirm", "Layout result"]
    current_idx = STAGES.index(current_stage)

    # ── Visual HTML sticky bar ─────────────────────────────────────────────────
    parts = []
    for i, label in enumerate(labels):
        num = i + 1
        sk = STAGES[i]
        if i < current_idx:
            parts.append(
                f'<button class="hs hs-done" data-hs-nav="{sk}" '
                f'aria-label="Return to {label}">'
                f'<span class="hs-tick">✓</span> {num}. {label}</button>'
            )
        elif i == current_idx:
            parts.append(
                f'<div class="hs hs-cur" aria-current="step">{num}. {label}</div>'
            )
        else:
            parts.append(f'<div class="hs hs-fut">{num}. {label}</div>')
        if i < 3:
            parts.append('<span class="hs-arr" aria-hidden="true">›</span>')

    st.markdown(
        '<nav class="hstep" aria-label="Progress">' + "".join(parts) + "</nav>",
        unsafe_allow_html=True,
    )

    # ── Hidden Streamlit back-nav triggers (⬅ prefix makes them uniquely findable by JS) ─
    for i in range(current_idx):
        if st.button(f"⬅{STAGES[i]}", key=f"hn_{STAGES[i]}", type="secondary"):
            _navigate_to(STAGES[i])

    # ── JS: hide trigger buttons + wire HTML bar clicks → trigger buttons ──────
    if current_idx > 0:
        components.html(
            """<script>
(function () {
  function setup () {
    var p = window.parent.document;
    /* hide Streamlit back-nav triggers identified by unique ⬅ prefix */
    p.querySelectorAll('button').forEach(function (b) {
      var t = (b.innerText || b.textContent || '').trim();
      if (t.length > 1 && t.charCodeAt(0) === 0x2B05) {
        var w = b.closest('[data-testid="stButton"]') || b.parentElement;
        if (w) w.style.cssText = 'height:0;overflow:hidden;margin:0;padding:0;';
      }
    });
    /* wire HTML bar completed-step clicks → hidden Streamlit trigger buttons */
    p.querySelectorAll('[data-hs-nav]').forEach(function (el) {
      if (el._hs) return;
      el._hs = true;
      el.addEventListener('click', function () {
        var nav = this.getAttribute('data-hs-nav');
        p.querySelectorAll('button').forEach(function (b) {
          var t = (b.innerText || b.textContent || '').trim();
          if (t === '⬅' + nav) b.click();
        });
      });
    });
  }
  setTimeout(setup, 120);
})();
</script>""",
            height=0,
        )


def stage_input():
    # Reserve space: bar (56px) stacks above chat input (~76px) → 155px clears both.
    st.markdown(
        '<style>.block-container{padding-bottom:155px!important;}</style>',
        unsafe_allow_html=True,
    )
    render_input_stage()
    inputs = st.session_state.get("site_inputs", {})
    missing = [_FIELD_LABEL.get(f, f) for f in _STAGE1_REQUIRED if inputs.get(f) is None]
    # bottom=76 positions bar above Streamlit's chat_input (~76px tall)
    _render_fixed_continue("Find a site on the map", not missing, missing, "stage1", "location", bottom=76)


def stage_location():
    st.markdown(
        '<style>.block-container{padding-bottom:72px!important;}</style>',
        unsafe_allow_html=True,
    )
    # Show "Confirm site" at top once a site has been selected, so the
    # primary action is visible without scrolling to the bottom of the list.
    if st.session_state.get("ss2_search_done"):
        if st.session_state.get("ss2_selected"):
            if st.button(
                "Confirm site →", key="btn_confirm_top",
                type="primary", use_container_width=True,
            ):
                st.session_state["stage"] = "summary"
                st.rerun()
        else:
            st.button(
                "Confirm site", key="btn_confirm_top_disabled",
                type="primary", use_container_width=True, disabled=True,
                help="Select a site from the list to continue",
            )
    render_location_stage()
    # Fixed bottom-right continue button
    if not st.session_state.get("ss2_search_done"):
        s2_missing = ["run a site search first"]
        s2_enabled = False
    elif not st.session_state.get("ss2_selected"):
        s2_missing = ["select a site from the results"]
        s2_enabled = False
    else:
        s2_missing = []
        s2_enabled = True
    _render_fixed_continue("Confirm site", s2_enabled, s2_missing, "stage2", "summary")


def stage_summary():
    st.markdown(
        '<style>.block-container{padding-bottom:72px!important;}</style>',
        unsafe_allow_html=True,
    )
    render_summary_stage()
    # Fixed bottom-right continue button (reuses same missing-fields logic as summary.py)
    inputs = st.session_state.get("site_inputs", {})
    site = st.session_state.get("site")
    s3_missing = [_FIELD_LABEL.get(f, f) for f in _STAGE1_REQUIRED if inputs.get(f) is None]
    if site is None:
        s3_missing.append("Selected site")
    _render_fixed_continue("Generate the layout", not s3_missing, s3_missing, "stage3", "layout")


def _packed_trace(items: list[dict],
                  origin_lat: float, origin_lon: float,
                  label: str, fill: str, line: str,
                  opacity: float = 0.80) -> go.Scattermapbox:
    """Convert a list of {corners_m} items into one multi-polygon Scattermapbox trace."""
    lats: list = []
    lons: list = []
    for item in items:
        pts = item["corners_m"]
        closed = pts + [pts[0]]
        for x, y in closed:
            la, lo = metres_to_latlon(x, y, origin_lat, origin_lon)
            lats.append(la)
            lons.append(lo)
        lats.append(None)
        lons.append(None)
    r, g, b = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
    return go.Scattermapbox(
        lat=lats, lon=lons,
        mode="lines",
        fill="toself",
        fillcolor=f"rgba({r},{g},{b},{opacity})",
        line=dict(color=line, width=1),
        name=label,
    )


def _road_trace(segments: list[dict],
                origin_lat: float, origin_lon: float,
                label: str, color: str, width: float) -> go.Scattermapbox:
    """Pack road polyline segments into one Scattermapbox line trace."""
    lats: list = []
    lons: list = []
    for seg in segments:
        for x, y in seg["pts_m"]:
            la, lo = metres_to_latlon(x, y, origin_lat, origin_lon)
            lats.append(la)
            lons.append(lo)
        lats.append(None)
        lons.append(None)
    return go.Scattermapbox(
        lat=lats, lon=lons,
        mode="lines",
        line=dict(color=color, width=width),
        name=label,
    )


def _layout_map(site: dict,
                shelters: list[dict],
                facilities: dict,
                roads: dict | None = None) -> go.Figure:
    """Plotly map with parcel outline, shelters, and all placed facilities."""
    origin_lat = site["origin_lat"]
    origin_lon = site["origin_lon"]

    # ── Parcel outline ────────────────────────────────────────────────────────
    parcel_pts = [
        metres_to_latlon(x, y, origin_lat, origin_lon)
        for x, y in site["parcel_polygon_m"]
    ]
    p_lats = [p[0] for p in parcel_pts] + [parcel_pts[0][0]]
    p_lons = [p[1] for p in parcel_pts] + [parcel_pts[0][1]]
    mid_lat = (min(p_lats) + max(p_lats)) / 2
    mid_lon = (min(p_lons) + max(p_lons)) / 2

    traces: list = [go.Scattermapbox(
        lat=p_lats, lon=p_lons,
        mode="lines",
        fill="toself", fillcolor="rgba(230,57,70,0.06)",
        line=dict(color="#e63946", width=2),
        name="Parcel boundary",
    )]

    # ── Roads (drawn first so facilities/shelters appear on top) ─────────────
    if roads:
        if roads.get("existing_roads"):
            traces.append(_road_trace(
                roads["existing_roads"], origin_lat, origin_lon,
                "Existing roads", "#707070", 2.5,
            ))
        if roads.get("main_road"):
            traces.append(_road_trace(
                roads["main_road"], origin_lat, origin_lon,
                "Main road (PA1)", "#A0A0A0", 4,
            ))
        sec = roads.get("secondary_roads", []) + roads.get("footpaths", [])
        if sec:
            traces.append(_road_trace(
                sec, origin_lat, origin_lon,
                "Secondary roads / footpaths (PA2)", "#C8C8C8", 2,
            ))
        # Entrance marker
        ex_m = roads.get("entrance_m")
        if ex_m:
            e_la, e_lo = metres_to_latlon(ex_m[0], ex_m[1], origin_lat, origin_lon)
            traces.append(go.Scattermapbox(
                lat=[e_la], lon=[e_lo],
                mode="markers",
                marker=dict(size=12, color="#FF4500"),
                name="Entrance",
            ))

    # ── Shelters ──────────────────────────────────────────────────────────────
    if shelters:
        label, fill, line = FACILITY_STYLE["shelter_units"]
        traces.append(_packed_trace(
            shelters, origin_lat, origin_lon,
            f"{label} ({len(shelters)})", fill, line, opacity=0.75,
        ))

    # ── Other facilities in draw order ────────────────────────────────────────
    draw_order = [
        "toilets", "washing_facilities", "schools",
        "water_points", "community_space", "food_distribution",
        "administrative_area", "worship_facility", "health_post",
    ]
    for key in draw_order:
        items = facilities.get(key, [])
        if not items:
            continue
        label, fill, line = FACILITY_STYLE[key]
        traces.append(_packed_trace(
            items, origin_lat, origin_lon,
            f"{label} ({len(items)})", fill, line,
        ))

    # ── Zoom ──────────────────────────────────────────────────────────────────
    lat_span_km = (max(p_lats) - min(p_lats)) * 111.32
    lon_span_km = (max(p_lons) - min(p_lons)) * 111.32 * cos(radians(mid_lat))
    span_km = max(lat_span_km, lon_span_km, 0.05)
    zoom = 17 if span_km < 0.2 else 16 if span_km < 0.5 else 15 if span_km < 1 else 14

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=mid_lat, lon=mid_lon),
            zoom=zoom,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        height=560,
        legend=dict(
            yanchor="top", y=0.99, xanchor="left", x=0.01,
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor="#cccccc",
            borderwidth=1,
            font=dict(size=11, color="#000000"),
        ),
    )
    return fig


def _site_capacity_warning(shelter_result: dict, inputs: dict) -> str | None:
    """Return plain-language explanation when a site cannot house the entered population."""
    pop = inputs.get("population", 0)

    if shelter_result.get("r4_fail"):
        detail = shelter_result.get("r4_detail", "")
        try:
            capacity = int(detail.split("supports")[1].split("pp")[0].strip())
        except (IndexError, ValueError):
            capacity = 0
        if capacity:
            return (
                f"**This site is too small.** The parcel can hold roughly "
                f"**{capacity:,} people** at the required density (45 m²/person), "
                f"but **{pop:,}** were entered. "
                f"Choose a larger site, or reduce the population to {capacity:,} or fewer."
            )
        return (
            f"**This site is too small for {pop:,} people.** "
            f"Choose a larger site or reduce the population."
        )

    sc = shelter_result.get("shortfall_communities", 0)
    if sc > 0:
        placed   = shelter_result.get("placed", 0)
        required = shelter_result.get("required", 0)
        capacity = placed * 5
        return (
            f"**This site cannot house everyone.** "
            f"Only **{placed} of {required} shelters** could be placed — "
            f"enough for about **{capacity:,} people**, not the {pop:,} entered. "
            f"The site's boundary limits the number of community zones that fit "
            f"after safety margins are applied. "
            f"Choose a site with a more regular shape, or reduce the population to "
            f"{capacity:,} or fewer."
        )

    return None


def _run_placement(site: dict, reqs: dict) -> tuple[dict, dict, dict]:
    """
    Place facilities (CS5 order) then shelters (block/community hierarchy),
    merge community water/sanitation back into facilities, then build roads.
    Returns (shelter_result, facilities, roads).
    """
    facilities   = place_all_facilities(site, reqs)
    occupied_geo = facilities.pop("_occupied_geo", None)
    shelter_result = place_shelters(site, reqs, occupied_geo=occupied_geo)

    # Two-pass fix: reposition HP to actual shelter centroid (FIX 1) and
    # re-place schools inside the populated region (FIX 2).
    # Must happen BEFORE community merge so community_latrines is still accessible.
    facilities = reposition_facilities_after_shelter_placement(site, facilities, shelter_result)

    # Merge community-placed facilities into the main facilities dict so that
    # the compliance gate and road builder see them. Must happen before roads.
    facilities["water_points"].extend(shelter_result.pop("community_water", []))
    facilities["toilets"].extend(shelter_result.pop("community_latrines", []))
    facilities["washing_facilities"].extend(shelter_result.pop("community_washing", []))
    for fac_key in ("water_points", "toilets", "washing_facilities"):
        facilities["status"][fac_key] = {
            "placed":   len(facilities[fac_key]),
            "required": facilities["status"].get(fac_key, {}).get("required", 0),
        }

    roads = place_roads(site, shelter_result, facilities)
    return shelter_result, facilities, roads


def _feedback_input_key() -> str:
    version = st.session_state.get("_feedback_input_version", 0)
    return f"feedback_input_v{version}"


def _clear_feedback_state() -> None:
    """Drop any stale feedback message and draft text — call whenever the
    layout underneath them changes (regenerate, optimise, reset).

    The draft text box is cleared by incrementing its key's version rather
    than deleting the old key's session-state entry: a live browser can hold
    its own buffered/debounced value for a text_area independent of the
    server's copy, so deleting the old key doesn't reliably clear what's
    displayed. Changing the key forces a brand-new widget with no possible
    residual client-side state.
    """
    st.session_state.pop("_last_feedback_result", None)
    st.session_state["_feedback_input_version"] = (
        st.session_state.get("_feedback_input_version", 0) + 1
    )


def stage_layout():
    st.header("Layout result")

    inputs = st.session_state.get("site_inputs", {})
    site   = st.session_state.get("site")
    reqs   = compute_requirements(inputs)

    if not (site and reqs):
        st.info("No site or population data — cannot compute layout.")
        if st.button("Next →", key="btn_layout"):
            advance_stage()
        return

    # ── Road-data status (PA14) — shown before the map, every time, so the
    # planner can tell at a glance whether this run is a valid test of
    # entrance/road placement, without opening a debug expander ────────────
    roads_fetch_error = site.get("roads_fetch_error", "")
    roads_m_count      = len(site.get("roads_m") or [])
    if roads_fetch_error:
        st.error(
            "**Running without external road data.** Overpass road detection "
            f"failed ({roads_fetch_error}). The entrance and road network are "
            "using parcel-only fallback logic, not real road geometry — "
            "**this run is not a valid test of entrance or road placement.**"
        )
    elif roads_m_count == 0:
        st.info(
            "No OSM roads were detected near this site (not a fetch failure — "
            "genuinely none nearby). Entrance and road connections use "
            "parcel-only positioning."
        )
    else:
        st.caption(
            f"✓ Real road data loaded: {roads_m_count} road segment(s) detected "
            "for this site — entrance and road connections use this data."
        )

    # ── Placement (cached in session state so the optimiser can update it) ────
    if "layout_result" not in st.session_state:
        with st.spinner("Placing facilities and shelters…"):
            sr, fac, rd = _run_placement(site, reqs)
        st.session_state["layout_result"] = {"shelter_result": sr,
                                              "facilities": fac,
                                              "roads": rd,
                                              "opt_log": []}
        _clear_feedback_state()
        st.rerun()

    lr          = st.session_state["layout_result"]
    shelter_result = lr["shelter_result"]
    facilities     = lr["facilities"]
    roads          = lr["roads"]
    opt_log        = lr.get("opt_log", [])
    fac_status     = facilities.get("status", {})

    # ── Optimiser button (Step 2) ─────────────────────────────────────────────
    if st.button("Improve layout", key="btn_optimise", type="primary",
                 use_container_width=True,
                 help="Run greedy improvement loop (10 iterations max)"):
        before_layout = {"shelter_result": shelter_result, "facilities": facilities, "roads": roads}
        score_before = score_layout(before_layout, site, reqs)["quality"]["total"]

        with st.spinner("Optimising facility positions…"):
            fac_new, new_log = optimise_facilities(
                site, reqs, facilities, shelter_result, roads, max_iter=10
            )
        lr["facilities"] = fac_new
        lr["opt_log"]    = new_log
        # Regenerate roads with improved positions
        roads_new = place_roads(site, shelter_result, fac_new)
        lr["roads"] = roads_new

        after_layout = {"shelter_result": shelter_result, "facilities": fac_new, "roads": roads_new}
        score_after = score_layout(after_layout, site, reqs)["quality"]["total"]
        # Only count lines that record an actual move — optimise_facilities()
        # also appends a final "Converged after N iteration(s)" summary line,
        # which is not a move and must not be reported as one.
        moved_count = sum(1 for entry in new_log if entry.startswith("iter "))
        lr["last_optimise_summary"] = {
            "moved": moved_count, "before": score_before, "after": score_after,
        }
        lr.pop("last_move_summary", None)
        _clear_feedback_state()
        st.rerun()

    opt_summary = lr.get("last_optimise_summary")
    if opt_summary:
        if opt_summary["moved"] == 0:
            st.info(
                "No changes needed — this layout is already near-optimal "
                f"(quality score: {opt_summary['after']})."
            )
        else:
            delta = opt_summary["after"] - opt_summary["before"]
            arrow = "▲" if delta > 0 else "▼" if delta < 0 else "→"
            st.info(
                f"**Optimiser result:** moved {opt_summary['moved']} facility "
                f"position(s) — quality score {opt_summary['before']} → "
                f"{opt_summary['after']} ({arrow} {delta:+d})"
            )

    if opt_log:
        with st.expander(f"Optimiser log ({len(opt_log)} entries)"):
            for entry in opt_log:
                st.text(entry)

    move_summary = lr.get("last_move_summary")
    if move_summary:
        delta = move_summary["after"] - move_summary["before"]
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "→"
        label = FACILITY_STYLE[move_summary["facility"]][0]
        moved_m     = move_summary.get("moved_m") or 0.0
        requested_m = move_summary.get("requested_m") or moved_m
        blocked_by  = move_summary.get("blocked_by")
        partial_note = ""
        if blocked_by and moved_m < requested_m - 0.5:
            partial_note = (
                f" (requested {requested_m:.0f} m, blocked beyond that by "
                f"{', '.join(blocked_by)})"
            )
        st.info(
            f"**Move applied:** moved {label} {moved_m:.0f} m {move_summary['direction']}"
            f"{partial_note} — quality score {move_summary['before']} → "
            f"{move_summary['after']} ({arrow} {delta:+d})"
        )

    # ── Site capacity warning — shown before the gate so the user sees WHY ──────
    _cap_msg = _site_capacity_warning(shelter_result, inputs)
    if _cap_msg:
        st.warning(_cap_msg)

    # ── Compliance gate (Step 3) ──────────────────────────────────────────────
    _layout = {"shelter_result": shelter_result, "facilities": facilities, "roads": roads}
    gate    = compliance_gate(_layout, site, reqs)

    if gate["pass"]:
        st.success("**Compliance gate: PASS** — all hard constraints satisfied")
    else:
        st.error("**Compliance gate: FAIL** — one or more hard constraints violated")

    failed = [c for c in gate["checks"] if not c["pass"]]
    passed = [c for c in gate["checks"] if c["pass"]]

    with st.expander(
        f"Compliance checks — {len(passed)} passed, {len(failed)} failed",
        expanded=not gate["pass"],
    ):
        for c in gate["checks"]:
            icon = "✓" if c["pass"] else "✗"
            colour = "#2e7d32" if c["pass"] else "#c62828"
            st.markdown(
                f"<span style='color:{colour};font-weight:600'>{icon} {c['name']}</span>"
                f" — {c['detail']}",
                unsafe_allow_html=True,
            )

    # ── Quality score (Step 3) — only meaningful if gate passes ──────────────
    score  = score_layout(_layout, site, reqs)
    total  = score["quality"]["total"]
    _color = "#2e7d32" if total >= 80 else "#e65100" if total >= 50 else "#c62828"
    gate_note = "" if gate["pass"] else " *(non-compliant layout)*"
    st.markdown(
        f"<div style='font-size:2.2rem;font-weight:700;color:{_color};"
        f"margin-top:0.6rem;margin-bottom:0.2rem'>"
        f"Quality score: {total} / 100{gate_note}</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Quality score breakdown"):
        score_rows = [
            {
                "Component":   c["name"].replace("_", " ").title(),
                "Score":       f"{c['points']}/10",
                "Weight":      f"×{c['weight']}",
                "Explanation": c["explanation"],
            }
            for c in score["quality"]["components"]
        ]
        st.table(score_rows)

    # ── Map ───────────────────────────────────────────────────────────────────
    st.subheader("Camp layout")
    st.plotly_chart(
        _layout_map(site, shelter_result["shelters"], facilities, roads),
        use_container_width=True,
    )

    # ── Feedback (Stage 5) ────────────────────────────────────────────────────
    st.subheader("Feedback")
    st.write(
        "Describe what you'd like changed, in plain language. A compass-direction "
        "move of a single health post, food distribution, community space, or "
        "administrative area is executed live. Everything else is classified "
        "and explained, but not yet applied."
    )

    feedback_text = st.text_area(
        "Your feedback", key=_feedback_input_key(),
        placeholder="e.g. move the food distribution north",
    )

    if st.button("Submit feedback", key="btn_submit_feedback"):
        if feedback_text.strip():
            facility_counts = {
                key: len(facilities.get(key, [])) for key in MOVABLE_FACILITY_KEYS
            }
            with st.spinner("Interpreting feedback…"):
                result = classify_feedback(feedback_text.strip(), facility_counts)

            move_outcome = None
            if (result["action"] == "move_facility"
                    and result.get("facility") in EXECUTABLE_MOVE_KEYS
                    and result.get("direction") in ("north", "south", "east", "west")
                    and not result.get("target_facility")):
                before_layout = {"shelter_result": shelter_result,
                                  "facilities": facilities, "roads": roads}
                score_before  = score_layout(before_layout, site, reqs)["quality"]["total"]
                gate_before   = compliance_gate(before_layout, site, reqs)
                passed_before = {c["name"] for c in gate_before["checks"] if c["pass"]}

                requested_m = result.get("distance_m") or MOVE_DEFAULT_DISTANCE_M
                fac_trial = copy.deepcopy(facilities)
                fac_trial, reject_reason, moved_m, blocked_by = move_facility(
                    site, fac_trial, shelter_result, roads,
                    result["facility"], result["direction"],
                    distance_m=result.get("distance_m"),
                )
                if reject_reason:
                    move_outcome = {"ok": False, "reason": reject_reason}
                else:
                    roads_trial  = place_roads(site, shelter_result, fac_trial)
                    after_layout = {"shelter_result": shelter_result,
                                     "facilities": fac_trial, "roads": roads_trial}
                    gate_after   = compliance_gate(after_layout, site, reqs)
                    newly_failed = [c for c in gate_after["checks"]
                                    if not c["pass"] and c["name"] in passed_before]
                    if newly_failed:
                        names = ", ".join(
                            f"{c['name']} ({c['detail']})" for c in newly_failed
                        )
                        move_outcome = {
                            "ok": False,
                            "reason": f"Move rejected — it would break compliance: {names}.",
                        }
                    else:
                        score_after = score_layout(after_layout, site, reqs)["quality"]["total"]
                        lr["facilities"] = fac_trial
                        lr["roads"]      = roads_trial
                        lr.pop("last_optimise_summary", None)
                        lr["last_move_summary"] = {
                            "facility": result["facility"],
                            "direction": result["direction"],
                            "moved_m": moved_m,
                            "requested_m": requested_m,
                            "blocked_by": blocked_by,
                            "before": score_before, "after": score_after,
                        }
                        move_outcome = {"ok": True}

            if move_outcome and move_outcome["ok"]:
                _clear_feedback_state()
                st.rerun()

            st.session_state["_last_feedback_result"] = {
                "text": feedback_text.strip(),
                "result": result,
                "move_outcome": move_outcome,
            }

    last = st.session_state.get("_last_feedback_result")
    if last:
        st.markdown(f"**You said:** {last['text']}")
        r = last["result"]
        move_outcome = last.get("move_outcome")
        if r["action"] == "unsupported":
            st.error(f"**Declined** — {r['reason']}")
        elif move_outcome is not None and not move_outcome["ok"]:
            label = FACILITY_STYLE[r["facility"]][0]
            st.error(f"**{label}:** {move_outcome['reason']}")
        else:
            st.success(
                f"**Understood as:** `{r['action']}`"
                + (f" — facility: `{r['facility']}`" if r.get("facility") else "")
                + (f", direction: `{r['direction']}`" if r.get("direction") else "")
                + (f", relative to: `{r['target_facility']}` (toward={r['toward']})"
                   if r.get("target_facility") else "")
            )
            st.caption(r["reason"])
            if r["action"] == "move_facility":
                st.caption(
                    "Single-instance compass-direction moves are executed live. "
                    "Moves relative to another facility, and facility types with "
                    "more than one instance (like schools), are classified but "
                    "not yet applied — that needs facility numbering, which is "
                    "the next step."
                )

    # ── Placement status ──────────────────────────────────────────────────────
    st.subheader("Placement status")
    sh_p, sh_r = shelter_result["placed"], shelter_result["required"]
    status_rows = [{"Facility": "Shelter units",
                    "Placed": sh_p, "Required": sh_r,
                    "OK": "yes" if sh_p >= sh_r else f"partial ({sh_p}/{sh_r})"}]

    fac_display = [
        ("health_post",         "Health post"),
        ("water_points",        "Water points"),
        ("food_distribution",   "Food distribution"),
        ("community_space",     "Community space"),
        ("administrative_area", "Administrative area"),
        ("schools",             "Schools"),
        ("worship_facility",    "Worship facility"),
        ("toilets",             "Latrine blocks"),
        ("washing_facilities",  "Washing facilities"),
    ]
    for key, label in fac_display:
        s = fac_status.get(key, {})
        p, r = s.get("placed", 0), s.get("required", 0)
        if r == 0:
            ok = "n/a"
        elif p >= r:
            ok = "yes"
        else:
            ok = f"partial ({p}/{r})"
        status_rows.append({"Facility": label, "Placed": p, "Required": r, "OK": ok})

    # ── Road network rows ─────────────────────────────────────────────────────
    conn     = roads.get("connected", True)
    stranded = roads.get("stranded", [])
    ex_m     = roads.get("entrance_m", (0.0, 0.0))
    if conn:
        conn_ok = "yes"
    else:
        conn_ok = f"no — stranded: {', '.join(stranded[:4])}" + (
            f" (+{len(stranded) - 4} more)" if len(stranded) > 4 else ""
        )
    status_rows.append({
        "Facility": "Road network (PA1+PA2+PA4)",
        "Placed": "connected" if conn else f"partial ({len(stranded)} stranded)",
        "Required": "fully connected",
        "OK": conn_ok,
    })
    status_rows.append({
        "Facility": "Entrance",
        "Placed": f"({ex_m[0]:.0f}, {ex_m[1]:.0f}) m",
        "Required": "on boundary",
        "OK": "yes",
    })

    st.table(status_rows)

    # ── Facility requirements table ───────────────────────────────────────────
    st.subheader("Facility requirements")
    req_rows = []
    for key, data in reqs.items():
        name  = key.replace("_", " ").title()
        count = data["count"]
        if key == "shelter_units":
            count_display = f"{count}  (× {data['area_per_unit_m2']} m²/unit)"
        elif key == "schools" and data.get("area_m2", 0) > 0:
            count_display = f"{count}  ({data['area_m2']} m² learning area)"
        else:
            count_display = str(count)
        req_rows.append({
            "Facility":     name,
            "Count / Area": count_display,
            "Unit":         data.get("unit", ""),
            "Constraint":   data.get("constraint", ""),
            "Explanation":  data.get("explanation", ""),
        })
    st.table(req_rows)

    # ── Site debug JSON ───────────────────────────────────────────────────────
    with st.expander("Site data (debug)"):
        summary = {k: v for k, v in site.items() if k != "roads_m"}
        summary["roads_count"] = len(site.get("roads_m", []))
        st.json(summary)

    st.divider()
    if st.button("Reset layout", key="btn_reset_layout"):
        del st.session_state["layout_result"]
        _clear_feedback_state()
        st.rerun()

    if st.button("Start over", key="btn_layout"):
        advance_stage()


STAGE_HANDLERS = {
    "input": stage_input,
    "location": stage_location,
    "summary": stage_summary,
    "layout": stage_layout,
}


def _scroll_to_top() -> None:
    components.html(
        "<script>window.parent.scrollTo({top: 0, behavior: 'instant'});</script>",
        height=0,
    )


def main():
    apply_brand()
    init_session_state()

    current_stage = st.session_state["stage"]
    if st.session_state.get("_prev_stage") != current_stage:
        st.session_state["_prev_stage"] = current_stage
        _scroll_to_top()

    render_brand_header()
    render_stepper(current_stage)

    STAGE_HANDLERS[current_stage]()


if __name__ == "__main__":
    main()
