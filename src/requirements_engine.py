from math import sqrt


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
