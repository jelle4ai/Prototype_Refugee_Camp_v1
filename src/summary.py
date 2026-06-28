"""
Stage 3 – editable summary and final review.

Displays all collected inputs in one screen, grouped into sections.
Every field is editable in-place.  Nothing auto-advances; the user
must click "Generate the layout" to proceed.
"""
from __future__ import annotations

from math import cos, radians

import plotly.graph_objects as go
import streamlit as st

from src.requirements_engine import compute_required_area
from src.site_search import metres_to_latlon

_DISCLAIMER = (
    "Screening based on map data only. Ground conditions, flood risk, "
    "legal availability and zoning must be confirmed with local authorities."
)

_REQUIRED_FIELDS = [
    "city", "population", "men", "women", "children",
    "climate", "duration", "cultural_notes", "special_needs",
]


# ── Site mini-map ─────────────────────────────────────────────────────────────

def _site_map(site: dict) -> go.Figure:
    origin_lat = site["origin_lat"]
    origin_lon = site["origin_lon"]

    latlons = [
        metres_to_latlon(x, y, origin_lat, origin_lon)
        for x, y in site["parcel_polygon_m"]
    ]
    lats = [p[0] for p in latlons] + [latlons[0][0]]
    lons = [p[1] for p in latlons] + [latlons[0][1]]
    mid_lat = (min(lats) + max(lats)) / 2
    mid_lon = (min(lons) + max(lons)) / 2

    traces: list = [go.Scattermapbox(
        lat=lats, lon=lons,
        mode="lines",
        fill="toself", fillcolor="rgba(230,57,70,0.15)",
        line=dict(color="#e63946", width=3),
        name="Selected site",
    )]
    for road in site.get("roads_m", []):
        if len(road) < 2:
            continue
        rlats, rlons = [], []
        for x, y in road:
            la, lo = metres_to_latlon(x, y, origin_lat, origin_lon)
            rlats.append(la)
            rlons.append(lo)
        traces.append(go.Scattermapbox(
            lat=rlats, lon=rlons,
            mode="lines",
            line=dict(color="#457b9d", width=2),
            showlegend=False,
        ))
    if site.get("roads_m"):
        traces.append(go.Scattermapbox(
            lat=[None], lon=[None], mode="lines",
            line=dict(color="#457b9d", width=2),
            name=f"Roads ({len(site['roads_m'])} segments)",
        ))

    lat_span_km = (max(lats) - min(lats)) * 111.32
    lon_span_km = (max(lons) - min(lons)) * 111.32 * cos(radians(mid_lat))
    span_km = max(lat_span_km, lon_span_km, 0.1)
    zoom = 15 if span_km < 0.5 else 14 if span_km < 1 else 13 if span_km < 2 else 12

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(style="open-street-map", center=dict(lat=mid_lat, lon=mid_lon), zoom=zoom),
        margin=dict(l=0, r=0, t=0, b=0),
        height=280,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.85)",
                    font=dict(color="black")),
    )
    return fig


# ── Parcel area (Shoelace in local metres) ────────────────────────────────────

def _polygon_area_ha(polygon_m: list[tuple[float, float]]) -> float:
    n = len(polygon_m)
    if n < 3:
        return 0.0
    a = sum(
        polygon_m[i][0] * polygon_m[(i + 1) % n][1]
        - polygon_m[(i + 1) % n][0] * polygon_m[i][1]
        for i in range(n)
    )
    return abs(a) / 2 / 10_000


# ── Validation ────────────────────────────────────────────────────────────────

def _missing(inputs: dict, site: dict | None) -> list[str]:
    out = []
    labels = {
        "city": "City", "population": "Population",
        "men": "Men", "women": "Women", "children": "Children",
        "climate": "Climate", "duration": "Duration",
        "cultural_notes": "Cultural notes", "special_needs": "Special needs",
    }
    for f in _REQUIRED_FIELDS:
        if inputs.get(f) is None:
            out.append(labels.get(f, f))
    if site is None:
        out.append("Selected site")
    return out


# ── Section: population ───────────────────────────────────────────────────────

def _section_population(inputs: dict) -> None:
    st.subheader("Population and demographics")

    pop    = inputs.get("population") or 0
    req_ha = (inputs.get("required_area_m2") or 0) / 10_000
    m_cur  = inputs.get("men")      or 0
    w_cur  = inputs.get("women")    or 0
    c_cur  = inputs.get("children") or 0

    st.markdown(
        f"**Total population:** {pop:,} &nbsp;|&nbsp; "
        f"**Required site area:** ~{req_ha:.1f} ha &nbsp; *(population × 45 m²)*"
    )
    st.caption("Edit the breakdown below and click **Save all changes** to apply.")

    c1, c2, c3 = st.columns(3)
    c1.number_input("Men",      min_value=0, step=100, value=m_cur, key="sum_men")
    c2.number_input("Women",    min_value=0, step=100, value=w_cur, key="sum_women")
    c3.number_input("Children", min_value=0, step=100, value=c_cur, key="sum_children")


# ── Section: context ──────────────────────────────────────────────────────────

def _section_context(inputs: dict) -> None:
    st.subheader("Context")

    # Climate
    climate = inputs.get("climate")
    row = st.columns([1, 1, 4])
    if row[0].button(
        "Warm", key="sum_cl_warm",
        type="primary" if climate == "warm" else "secondary",
        use_container_width=True,
    ):
        inputs["climate"] = "warm"
        st.rerun()
    if row[1].button(
        "Cold", key="sum_cl_cold",
        type="primary" if climate == "cold" else "secondary",
        use_container_width=True,
    ):
        inputs["climate"] = "cold"
        st.rerun()
    if climate:
        row[2].markdown(f"Climate: **{climate.capitalize()}**")
    else:
        row[2].markdown(":red[Climate not set — click a button to the left]")

    # Duration
    duration = inputs.get("duration")
    row2 = st.columns([1.3, 1.3, 3.4])
    if row2[0].button(
        "Emergency", key="sum_du_emerg",
        type="primary" if duration == "emergency" else "secondary",
        use_container_width=True,
    ):
        inputs["duration"] = "emergency"
        st.rerun()
    if row2[1].button(
        "Protracted", key="sum_du_prot",
        type="primary" if duration == "protracted" else "secondary",
        use_container_width=True,
    ):
        inputs["duration"] = "protracted"
        st.rerun()
    if duration:
        row2[2].markdown(f"Duration: **{duration.capitalize()}**")
    else:
        row2[2].markdown(":red[Duration not set — click a button to the left]")

    # Free-text fields
    for field, label in [
        ("cultural_notes", "Cultural notes"),
        ("special_needs",  "Special needs"),
    ]:
        cur = inputs.get(field) or ""
        st.text_input(label, value=cur, key=f"sum_{field}", placeholder='e.g. "None specified"')


# ── Section: services ─────────────────────────────────────────────────────────

def _section_services(inputs: dict) -> None:
    st.subheader("Site services")
    st.caption("These are optional — leave blank if unknown.")

    for field, label, ph in [
        ("cause",        "Displacement cause", "e.g. conflict, flood, drought"),
        ("water_source", "Water source",       "e.g. municipal, borehole, trucking"),
        ("power_source", "Power source",       "e.g. grid, generators, solar"),
        ("sanitation",   "Sanitation",         "e.g. pit latrines, portable toilets, sewer"),
    ]:
        cur = inputs.get(field) or ""
        st.text_input(label, value=cur, key=f"sum_{field}", placeholder=ph)


# ── Section: selected site ────────────────────────────────────────────────────

def _section_site(inputs: dict, site: dict) -> None:
    st.subheader("Selected site")

    area_ha  = _polygon_area_ha(site["parcel_polygon_m"])
    roads_n  = len(site.get("roads_m", []))
    width_m  = round(site["width_m"])
    length_m = round(site["length_m"])
    city     = inputs.get("city") or "—"

    col_l, col_r = st.columns([2, 3])
    with col_l:
        st.markdown(f"**City:** {city}")
        st.markdown(f"**Parcel area:** ~{area_ha:.1f} ha")
        st.markdown(f"**Bounding box:** {width_m} m × {length_m} m")
        st.markdown(f"**Detected road segments:** {roads_n}")

        # Area adequacy check
        req_area_m2 = inputs.get("required_area_m2") or 0
        if req_area_m2 > 0:
            req_ha = req_area_m2 / 10_000
            if area_ha < req_ha * 0.95:
                st.warning(
                    f"Parcel (~{area_ha:.1f} ha) may be too small for the "
                    f"current population ({req_ha:.1f} ha needed)."
                )

        st.caption(f"Note: {_DISCLAIMER}")

        if st.button("Choose a different site", key="btn_change_site"):
            st.session_state.pop("site", None)
            for k in ("ss2_selected", "ss2_roads_done", "ss2_roads_m", "ss2_roads_error"):
                st.session_state.pop(k, None)
            st.session_state["stage"] = "location"
            st.rerun()

    with col_r:
        st.plotly_chart(_site_map(site), use_container_width=True)


# ── Main render entry point ───────────────────────────────────────────────────

def render_summary_stage() -> None:
    st.header("Review and confirm")
    st.caption("Every field is editable — no need to go back to earlier stages.")

    inputs = st.session_state["site_inputs"]
    site   = st.session_state.get("site")

    # Keep required-area in sync with population (may have been edited)
    pop = inputs.get("population") or 0
    if pop > 0:
        result = compute_required_area(pop)
        inputs["required_area_m2"] = result["total_area_m2"]
        inputs["suggested_side_m"] = result["suggested_side_m"]

    # ── Status banner ─────────────────────────────────────────────────────────
    gaps = _missing(inputs, site)
    if gaps:
        st.error("**Still needed:** " + ", ".join(gaps))
    else:
        st.success("All required information is present. Ready to generate.")

    st.divider()

    # ── Sections ──────────────────────────────────────────────────────────────
    with st.container(border=True):
        _section_population(inputs)

    with st.container(border=True):
        _section_context(inputs)

    with st.container(border=True):
        _section_services(inputs)

    with st.container(border=True):
        if site is not None:
            _section_site(inputs, site)
        else:
            st.subheader("Selected site")
            st.warning("No site has been selected yet.")
            if st.button("Go to site selection →", key="btn_goto_site"):
                st.session_state["stage"] = "location"
                st.rerun()

    # ── Single save for all editable text and number fields ───────────────────
    if st.button("Save all changes", key="btn_save_all", use_container_width=True):
        m = int(st.session_state.get("sum_men") or inputs.get("men") or 0)
        w = int(st.session_state.get("sum_women") or inputs.get("women") or 0)
        c = int(st.session_state.get("sum_children") or inputs.get("children") or 0)
        inputs["men"]        = m
        inputs["women"]      = w
        inputs["children"]   = c
        inputs["population"] = m + w + c
        for field in ["cultural_notes", "special_needs",
                      "cause", "water_source", "power_source", "sanitation"]:
            raw = (st.session_state.get(f"sum_{field}") or "").strip()
            inputs[field] = raw if raw else None
        st.rerun()

    # ── Generate button at bottom too (for users who scroll through the form) ─
    st.divider()
    gaps2 = _missing(inputs, site)
    if gaps2:
        st.button("Generate the layout", type="primary", use_container_width=True,
                  disabled=True, key="btn_gen_bottom_disabled")
    else:
        if st.button("Generate the layout →", type="primary",
                     use_container_width=True, key="btn_gen_bottom"):
            st.session_state["stage"] = "layout"
            st.rerun()
