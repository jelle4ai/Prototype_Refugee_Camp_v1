import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from math import cos, radians
from src.conversation import render_input_stage
from src.layout_engine import place_shelters
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


def _layout_map(site: dict, shelters: list[dict]) -> go.Figure:
    """Plotly map showing the parcel outline and placed shelter rectangles."""
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
        fill="toself", fillcolor="rgba(230,57,70,0.08)",
        line=dict(color="#e63946", width=2),
        name="Parcel boundary",
    )]

    # ── Shelter rectangles — packed into one multi-polygon trace ──────────────
    if shelters:
        s_lats: list = []
        s_lons: list = []
        for sh in shelters:
            closed = sh["corners_m"] + [sh["corners_m"][0]]
            for x, y in closed:
                la, lo = metres_to_latlon(x, y, origin_lat, origin_lon)
                s_lats.append(la)
                s_lons.append(lo)
            s_lats.append(None)
            s_lons.append(None)

        traces.append(go.Scattermapbox(
            lat=s_lats, lon=s_lons,
            mode="lines",
            fill="toself",
            fillcolor="rgba(245,222,179,0.75)",
            line=dict(color="#C4A882", width=1),
            name=f"Shelter units ({len(shelters)})",
        ))

    # ── Map zoom from parcel span ─────────────────────────────────────────────
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
        height=520,
        legend=dict(
            yanchor="top", y=0.99, xanchor="left", x=0.01,
            bgcolor="rgba(255,255,255,0.85)",
        ),
    )
    return fig


def stage_layout():
    st.header("Stage: Layout")

    inputs = st.session_state.get("site_inputs", {})
    site   = st.session_state.get("site")
    reqs   = compute_requirements(inputs)

    # ── Shelter placement ─────────────────────────────────────────────────────
    if site and reqs:
        result = place_shelters(site, reqs)
        placed   = result["placed"]
        required = result["required"]
        shelters = result["shelters"]

        if placed < required:
            st.warning(
                f"Placed **{placed}** of **{required}** shelter units — "
                "parcel is too small to fit all units within the boundary."
            )
        else:
            st.success(f"Placed **{placed}** of **{required}** shelter units.")

        st.plotly_chart(_layout_map(site, shelters), use_container_width=True)
    else:
        st.info("No site or population data — cannot compute layout.")

    # ── Facility requirements table ───────────────────────────────────────────
    st.subheader("Facility requirements")
    if reqs:
        rows = []
        for key, data in reqs.items():
            name  = key.replace("_", " ").title()
            count = data["count"]
            if key == "shelter_units":
                count_display = f"{count}  (× {data['area_per_unit_m2']} m²/unit)"
            elif key == "schools" and data.get("area_m2", 0) > 0:
                count_display = f"{count}  ({data['area_m2']} m² learning area)"
            else:
                count_display = str(count)
            rows.append({
                "Facility":    name,
                "Count / Area": count_display,
                "Unit":        data.get("unit", ""),
                "Constraint":  data.get("constraint", ""),
                "Explanation": data.get("explanation", ""),
            })
        st.table(rows)
    else:
        st.info("No population data found — requirements cannot be computed.")

    # ── Site debug JSON ───────────────────────────────────────────────────────
    if site:
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
