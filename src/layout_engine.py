"""
Stage 4 layout engine — shelter unit placement.

place_shelters(site, requirements) -> dict
  Lays shelter rectangles in rows inside the real parcel polygon.
  Uses shapely to discard any unit whose rectangle does not lie fully
  within the parcel boundary (handles irregular shapes correctly).
"""
from shapely.geometry import Polygon as ShapelyPolygon


_UNIT_WIDTH_M = 5.0   # fixed shelter width; height derived from area


def _footprint(area_m2: float) -> tuple[float, float]:
    h = round(area_m2 / _UNIT_WIDTH_M, 2)
    return _UNIT_WIDTH_M, h


def place_shelters(site: dict, requirements: dict) -> dict:
    """
    Parameters
    ----------
    site          : the st.session_state["site"] dict
    requirements  : output of compute_requirements(site_inputs)

    Returns
    -------
    dict with keys:
      shelters  – list of {"corners_m": [(x,y)×4]} in local metres (SW origin)
      placed    – int
      required  – int
    """
    shelter_req = requirements.get("shelter_units", {})
    required    = shelter_req.get("count", 0)
    area_m2     = shelter_req.get("area_per_unit_m2", 17.5)

    if required == 0 or not site.get("parcel_polygon_m"):
        return {"shelters": [], "placed": 0, "required": required}

    unit_w, unit_h = _footprint(area_m2)
    gap_unit = 2.0   # SH6 — side-to-side gap between units
    gap_row  = 4.0   # row-to-row gap reserved for footpaths
    margin   = 3.0   # inset from parcel edge

    parcel = ShapelyPolygon(site["parcel_polygon_m"])
    minx, miny, maxx, maxy = parcel.bounds

    shelters: list[dict] = []
    y = miny + margin

    while y + unit_h <= maxy - margin and len(shelters) < required:
        x = minx + margin
        while x + unit_w <= maxx - margin and len(shelters) < required:
            corners = [
                (x,          y),
                (x + unit_w, y),
                (x + unit_w, y + unit_h),
                (x,          y + unit_h),
            ]
            if parcel.contains(ShapelyPolygon(corners)):
                shelters.append({"corners_m": corners})
            x += unit_w + gap_unit
        y += unit_h + gap_row

    return {
        "shelters": shelters,
        "placed":   len(shelters),
        "required": required,
    }
