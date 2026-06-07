from math import ceil, sqrt


_RELIGIOUS_KEYWORDS = {
    "church", "mosque", "worship", "religion", "religious", "temple",
    "prayer", "pray", "faith", "synagogue", "chapel", "shrine",
}


def compute_requirements(site_inputs: dict) -> dict:
    """
    Compute facility counts for a camp from site_inputs.
    Returns a dict keyed by facility type; each value has count, constraint,
    unit, explanation, and optional extra fields (area, climate, etc.).
    Returns {} if population is missing or zero.
    """
    population = site_inputs.get("population")
    if not population:
        return {}

    children = site_inputs.get("children") or 0
    climate = (site_inputs.get("climate") or "warm").lower()
    cultural_notes = (site_inputs.get("cultural_notes") or "").lower()

    if climate == "cold":
        area_per_unit_m2 = 22.5
        shelter_constraint = "SH2"
    else:
        area_per_unit_m2 = 17.5
        shelter_constraint = "SH1"

    if population < 5000:
        food_pts = 1
    elif population <= 10000:
        food_pts = 2
    else:
        food_pts = 3

    schools_count = max(1, ceil(children / 200)) if children > 0 else 0
    learning_area_m2 = round(children * 1.24, 1) if children > 0 else 0.0

    has_religious_need = any(kw in cultural_notes for kw in _RELIGIOUS_KEYWORDS)

    return {
        "shelter_units": {
            "count": ceil(population / 5),
            "area_per_unit_m2": area_per_unit_m2,
            "climate": climate,
            "constraint": shelter_constraint,
            "unit": "units",
            "explanation": (
                f"1 unit per 5-person household; "
                f"{area_per_unit_m2} m² per unit in {climate} climate."
            ),
        },
        "water_points": {
            "count": ceil(population / 250),
            "constraint": "WS2",
            "unit": "points",
            "explanation": "1 water point per 250 people.",
        },
        "toilets": {
            "count": ceil(population / 20),
            "constraint": "SA1",
            "unit": "units",
            "explanation": "1 toilet per 20 people.",
        },
        "washing_facilities": {
            "count": ceil(population / 100),
            "constraint": "SA2",
            "unit": "units",
            "explanation": "1 washing facility per 100 people.",
        },
        "health_posts": {
            "count": max(1, ceil(population / 10000)),
            "constraint": "HE1",
            "unit": "posts",
            "explanation": "Minimum 1; 1 per 10,000 people.",
        },
        "food_distribution_points": {
            "count": food_pts,
            "constraint": "FD3",
            "unit": "points",
            "explanation": "1 for < 5,000 people; 2 for ≤ 10,000; 3 for larger camps.",
        },
        "schools": {
            "count": schools_count,
            "area_m2": learning_area_m2,
            "constraint": "ED1",
            "unit": "schools",
            "explanation": (
                f"1 per 200 children; learning area 1.24 m² per child "
                f"({learning_area_m2} m² total). Distance rule ED3 applies."
            ),
        },
        "community_space": {
            "count": 1,
            "constraint": "CS1",
            "unit": "space",
            "explanation": "1 community space per camp.",
        },
        "administrative_area": {
            "count": 1,
            "constraint": "CS2",
            "unit": "area",
            "explanation": "1 administrative area per camp.",
        },
        "worship_facility": {
            "count": 1 if has_religious_need else 0,
            "constraint": "RB1",
            "unit": "facilities",
            "explanation": (
                "Contextual (Appendix C) — included when cultural notes "
                "indicate a religious need."
            ),
        },
    }


def compute_required_area(population: int) -> dict:
    """
    Compute minimum land requirements for a camp.

    Constraint SH3: 45 m² all-in per person.
    A 25% search buffer keeps the suggested rectangle from being cramped
    and leaves room for expansion negotiations.
    """
    total_area_m2 = population * 45
    suggested_side_m = round(sqrt(total_area_m2 * 1.25))
    return {
        "total_area_m2": total_area_m2,
        "suggested_side_m": suggested_side_m,
    }
