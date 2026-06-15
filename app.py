import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from math import cos, radians
from src.conversation import render_input_stage
from src.layout_engine import (
    place_shelters, place_all_facilities, place_roads,
    optimise_facilities, FACILITY_STYLE,
)
from src.scoring import score_layout, compliance_gate
from src.location import render_location_stage
from src.requirements_engine import compute_requirements
from src.site_search import metres_to_latlon
from src.summary import render_summary_stage

st.set_page_config(page_title="Refugee Camp Layout Generator", layout="wide")

STAGES = ["input", "location", "summary", "layout", "feedback"]


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


def stage_input():
    render_input_stage()


def stage_location():
    render_location_stage()


def stage_summary():
    render_summary_stage()


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


def _run_placement(site: dict, reqs: dict) -> tuple[dict, dict, dict]:
    """
    Place facilities (CS5 order) then shelters, returning
    (shelter_result, facilities, roads).  Caches result in session state.
    """
    facilities     = place_all_facilities(site, reqs)
    occupied_geo   = facilities.pop("_occupied_geo", None)
    shelter_result = place_shelters(site, reqs, occupied_geo=occupied_geo)
    roads          = place_roads(site, shelter_result, facilities)
    return shelter_result, facilities, roads


def stage_layout():
    st.header("Stage: Layout")

    inputs = st.session_state.get("site_inputs", {})
    site   = st.session_state.get("site")
    reqs   = compute_requirements(inputs)

    if not (site and reqs):
        st.info("No site or population data — cannot compute layout.")
        if st.button("Next →", key="btn_layout"):
            advance_stage()
        return

    # ── Placement (cached in session state so the optimiser can update it) ────
    if "layout_result" not in st.session_state:
        with st.spinner("Placing facilities and shelters…"):
            sr, fac, rd = _run_placement(site, reqs)
        st.session_state["layout_result"] = {"shelter_result": sr,
                                              "facilities": fac,
                                              "roads": rd,
                                              "opt_log": []}
        st.rerun()

    lr          = st.session_state["layout_result"]
    shelter_result = lr["shelter_result"]
    facilities     = lr["facilities"]
    roads          = lr["roads"]
    opt_log        = lr.get("opt_log", [])
    fac_status     = facilities.get("status", {})

    # ── Optimiser button (Step 2) ─────────────────────────────────────────────
    col_opt, col_reset = st.columns([2, 1])
    if col_opt.button("Optimise layout", key="btn_optimise",
                      help="Run greedy improvement loop (10 iterations max)"):
        with st.spinner("Optimising facility positions…"):
            fac_new, new_log = optimise_facilities(
                site, reqs, facilities, shelter_result, roads, max_iter=10
            )
        lr["facilities"] = fac_new
        lr["opt_log"]    = new_log
        # Regenerate roads with improved positions
        lr["roads"] = place_roads(site, shelter_result, fac_new)
        st.rerun()

    if col_reset.button("Reset layout", key="btn_reset_layout"):
        del st.session_state["layout_result"]
        st.rerun()

    if opt_log:
        with st.expander(f"Optimiser log ({len(opt_log)} entries)"):
            for entry in opt_log:
                st.text(entry)

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
    st.plotly_chart(
        _layout_map(site, shelter_result["shelters"], facilities, roads),
        use_container_width=True,
    )

    # ── Placement status ──────────────────────────────────────────────────────
    st.subheader("Placement status")
    sh_p, sh_r = shelter_result["placed"], shelter_result["required"]
    status_rows = [{"Facility": "Shelter units",
                    "Placed": sh_p, "Required": sh_r,
                    "OK": "yes" if sh_p == sh_r else f"partial ({sh_p}/{sh_r})"}]

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
        elif p == r:
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

    if st.button("Next →", key="btn_layout"):
        advance_stage()


def stage_feedback():
    st.header("Stage: Feedback")
    st.write("Placeholder — scoring and AI feedback here.")
    if st.button("Start over", key="btn_feedback"):
        advance_stage()


STAGE_HANDLERS = {
    "input": stage_input,
    "location": stage_location,
    "summary": stage_summary,
    "layout": stage_layout,
    "feedback": stage_feedback,
}


def _scroll_to_top() -> None:
    components.html(
        "<script>window.parent.scrollTo({top: 0, behavior: 'instant'});</script>",
        height=0,
    )


def main():
    init_session_state()

    current_stage = st.session_state["stage"]
    if st.session_state.get("_prev_stage") != current_stage:
        st.session_state["_prev_stage"] = current_stage
        _scroll_to_top()

    st.title("Refugee Camp Layout Generator")
    STAGE_HANDLERS[current_stage]()


if __name__ == "__main__":
    main()
