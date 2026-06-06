from math import sqrt
import streamlit as st
import plotly.graph_objects as go

from src.geocoding import geocode_city, metres_to_latlon, latlon_to_metres, fetch_roads


# ── Session-state helpers ─────────────────────────────────────────────────────

def _init_location_state() -> None:
    """Seed all loc_* keys on first entry; geocodes the city from Stage 1."""
    if "loc_geocoded" in st.session_state:
        return  # already initialised — leave everything as-is

    inputs = st.session_state["site_inputs"]
    city = inputs.get("city", "")
    suggested = max(100, int(inputs.get("suggested_side_m", 300)))

    st.session_state["loc_geocoded"] = False
    st.session_state["loc_geocode_error"] = ""
    st.session_state["loc_centre_lat"] = 0.0
    st.session_state["loc_centre_lon"] = 0.0
    st.session_state["loc_width_m"] = suggested
    st.session_state["loc_length_m"] = suggested
    st.session_state["loc_roads_m"] = []
    st.session_state["loc_roads_detected"] = False

    if city:
        result = geocode_city(city)
        if result:
            lat, lon = result
            st.session_state["loc_centre_lat"] = lat
            st.session_state["loc_centre_lon"] = lon
            st.session_state["loc_geocoded"] = True
        else:
            st.session_state["loc_geocode_error"] = city


def _origin_from_centre(
    centre_lat: float, centre_lon: float, width_m: float, length_m: float
) -> tuple[float, float]:
    """Return bottom-left (SW) corner lat/lon from site centre and dimensions."""
    return metres_to_latlon(-width_m / 2, -length_m / 2, centre_lat, centre_lon)


# ── Map builder ───────────────────────────────────────────────────────────────

def _build_map(
    centre_lat: float,
    centre_lon: float,
    width_m: float,
    length_m: float,
    origin_lat: float,
    origin_lon: float,
    roads_m: list,
) -> go.Figure:
    # Site rectangle: 5 corner points (closed loop), in local metres
    corners_m = [(0, 0), (0, length_m), (width_m, length_m), (width_m, 0), (0, 0)]
    rect_lats, rect_lons = [], []
    for x, y in corners_m:
        lat, lon = metres_to_latlon(x, y, origin_lat, origin_lon)
        rect_lats.append(lat)
        rect_lons.append(lon)

    traces: list = [
        go.Scattermapbox(
            lat=rect_lats,
            lon=rect_lons,
            mode="lines",
            line=dict(color="#e63946", width=3),
            name="Site boundary",
        ),
        go.Scattermapbox(
            lat=[centre_lat],
            lon=[centre_lon],
            mode="markers",
            marker=dict(size=9, color="#e63946"),
            name="Centre",
        ),
    ]

    # Road polylines
    for road in roads_m:
        if not road:
            continue
        road_lats, road_lons = [], []
        for x, y in road:
            lat, lon = metres_to_latlon(x, y, origin_lat, origin_lon)
            road_lats.append(lat)
            road_lons.append(lon)
        traces.append(go.Scattermapbox(
            lat=road_lats,
            lon=road_lons,
            mode="lines",
            line=dict(color="#457b9d", width=2),
            showlegend=False,
        ))

    # Single legend entry for all roads
    if roads_m:
        traces.append(go.Scattermapbox(
            lat=[None], lon=[None],
            mode="lines",
            line=dict(color="#457b9d", width=2),
            name=f"Existing roads ({len(roads_m)})",
        ))

    # Zoom: keep site comfortably in view
    diagonal = sqrt(width_m ** 2 + length_m ** 2)
    zoom = 15 if diagonal < 300 else 14 if diagonal < 700 else 13 if diagonal < 1500 else 12

    fig = go.Figure(traces)
    fig.update_layout(
        mapbox=dict(
            style="open-street-map",
            center=dict(lat=centre_lat, lon=centre_lon),
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


# ── Main render entry point ───────────────────────────────────────────────────

def render_location_stage() -> None:
    st.header("Site Selection")

    inputs = st.session_state["site_inputs"]

    # ── Guard: demographic split must equal population ─────────────────────────
    pop = inputs.get("population") or 0
    men = inputs.get("men") or 0
    women = inputs.get("women") or 0
    children = inputs.get("children") or 0
    split_sum = men + women + children

    if split_sum != pop:
        st.warning(
            f"**Demographic mismatch:** {men:,} + {women:,} + {children:,} = {split_sum:,}, "
            f"but population is recorded as {pop:,}. "
            "The demographic split may have been assumed rather than confirmed. "
            "You can go back and correct it, or proceed with the current figures."
        )
        if st.button("← Back to questions", key="btn_back_guard"):
            st.session_state["stage"] = "input"
            st.rerun()
        st.divider()

    # ── Initialise state (geocodes city on first entry) ────────────────────────
    _init_location_state()

    error_city = st.session_state.get("loc_geocode_error", "")
    if error_city:
        st.error(
            f"Could not geocode **{error_city}**. "
            "Use the search box below to try a more specific place name."
        )

    # ── Location search and position fine-tune ─────────────────────────────────
    with st.expander(
        "Search location / fine-tune centre",
        expanded=not st.session_state["loc_geocoded"],
    ):
        srch_col, btn_col = st.columns([4, 1])
        new_search = srch_col.text_input(
            "Place name",
            value=inputs.get("city", ""),
            key="loc_search_input",
            label_visibility="collapsed",
            placeholder="e.g. Dadaab, Kenya",
        )
        if btn_col.button("Search", key="btn_geocode", use_container_width=True):
            result = geocode_city(new_search)
            if result:
                lat, lon = result
                st.session_state["loc_centre_lat"] = lat
                st.session_state["loc_centre_lon"] = lon
                st.session_state["loc_geocoded"] = True
                st.session_state["loc_geocode_error"] = ""
                st.session_state["loc_roads_m"] = []
                st.session_state["loc_roads_detected"] = False
                st.rerun()
            else:
                st.error(f'Could not geocode "{new_search}". Try a different name or add the country.')

        if st.session_state["loc_geocoded"]:
            st.caption(
                "Fine-tune the site centre by adjusting the coordinates below. "
                "Move the decimal to place the rectangle on open land."
            )
            lat_col, lon_col = st.columns(2)
            new_lat = lat_col.number_input(
                "Centre latitude",
                value=float(st.session_state["loc_centre_lat"]),
                min_value=-90.0, max_value=90.0,
                step=0.0005, format="%.6f",
                key="fine_lat",
            )
            new_lon = lon_col.number_input(
                "Centre longitude",
                value=float(st.session_state["loc_centre_lon"]),
                min_value=-180.0, max_value=180.0,
                step=0.0005, format="%.6f",
                key="fine_lon",
            )
            if (
                new_lat != st.session_state["loc_centre_lat"]
                or new_lon != st.session_state["loc_centre_lon"]
            ):
                st.session_state["loc_centre_lat"] = new_lat
                st.session_state["loc_centre_lon"] = new_lon
                st.session_state["loc_roads_m"] = []
                st.session_state["loc_roads_detected"] = False

    # ── Site dimensions ────────────────────────────────────────────────────────
    suggested = int(st.session_state.get("loc_width_m", 300))
    area_ha = round(inputs.get("required_area_m2", 0) / 10_000, 1)
    st.caption(
        f"Required area: ~{area_ha} ha. "
        f"Suggested square: **{suggested} × {suggested} m**. "
        "Adjust width and length to fit the available land."
    )
    dim1, dim2, _ = st.columns([2, 2, 3])
    width_m = dim1.number_input(
        "Width (m, E–W)", min_value=50, max_value=10_000, step=100,
        key="loc_width_m",
    )
    length_m = dim2.number_input(
        "Length (m, N–S)", min_value=50, max_value=10_000, step=100,
        key="loc_length_m",
    )

    # ── Map ────────────────────────────────────────────────────────────────────
    if not st.session_state["loc_geocoded"]:
        st.info("Search for a location above to display the map.")
        return

    centre_lat = st.session_state["loc_centre_lat"]
    centre_lon = st.session_state["loc_centre_lon"]
    origin_lat, origin_lon = _origin_from_centre(centre_lat, centre_lon, width_m, length_m)
    roads_m = st.session_state["loc_roads_m"]

    fig = _build_map(centre_lat, centre_lon, width_m, length_m, origin_lat, origin_lon, roads_m)
    st.plotly_chart(fig, use_container_width=True)

    if st.session_state["loc_roads_detected"]:
        if roads_m:
            st.success(f"✓ {len(roads_m)} road segment(s) detected within the site boundary.")
        else:
            st.info("No existing roads were detected within the site boundary (open land or unmapped area).")

    # ── Confirm + proceed ──────────────────────────────────────────────────────
    st.divider()
    confirm_col, proceed_col = st.columns([2, 1])

    with confirm_col:
        if st.button(
            "Confirm site and detect roads",
            type="primary",
            use_container_width=True,
            key="btn_confirm_site",
        ):
            max_lat, max_lon = metres_to_latlon(width_m, length_m, origin_lat, origin_lon)
            with st.spinner("Querying OpenStreetMap for existing roads…"):
                roads = fetch_roads(
                    origin_lat, origin_lon,
                    max_lat, max_lon,
                    origin_lat, origin_lon,
                )
            st.session_state["loc_roads_m"] = roads
            st.session_state["loc_roads_detected"] = True
            st.rerun()

    with proceed_col:
        if st.session_state["loc_roads_detected"]:
            if st.button(
                "Proceed to layout →",
                use_container_width=True,
                key="btn_proceed_layout",
            ):
                st.session_state["site"] = {
                    "origin_lat": origin_lat,
                    "origin_lon": origin_lon,
                    "width_m": float(width_m),
                    "length_m": float(length_m),
                    "centre_lat": centre_lat,
                    "centre_lon": centre_lon,
                    "roads_m": st.session_state["loc_roads_m"],
                }
                st.session_state["stage"] = "layout"
                st.rerun()
