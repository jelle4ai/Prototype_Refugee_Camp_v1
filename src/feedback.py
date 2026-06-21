"""
Stage 5 feedback classifier.

Translates a planner's plain-language feedback into one of a small, fixed set
of engine-actionable operations, modelled on conversation.py's _extract_inputs():
a narrow LLM call constrained to strict JSON, consumed only by deterministic
code. The LLM never touches geometry — it only picks from a whitelist.
"""
import json
import re
from src.ai_client import get_ai_response
from src.layout_engine import FACILITY_STYLE

# The only standalone facilities a move can target. Shelters, communities,
# blocks, roads, and water_points (community-embedded, not freestanding) are
# explicitly out of scope for v1.
MOVABLE_FACILITY_KEYS = [
    "health_post", "food_distribution", "community_space",
    "administrative_area", "schools", "worship_facility",
]

_FACILITY_LINES = "\n".join(
    f'  "{key}" — {FACILITY_STYLE[key][0]}' for key in MOVABLE_FACILITY_KEYS
)

_FEEDBACK_SYSTEM_PROMPT_BASE = f"""\
You are a structured request classifier for a refugee camp layout tool. Read \
a planner's plain-language feedback about a generated layout and classify it \
into EXACTLY ONE of four supported actions. Return ONLY a valid JSON object — \
no prose, no markdown, no code fences.

SUPPORTED ACTIONS:

1. "move_facility" — the planner wants ONE of these specific facility TYPES
   moved (the engine only ever acts on a facility type as a whole — it cannot
   move a single instance out of several of the same type):
{_FACILITY_LINES}
   Required fields: "facility" (one of the keys above), and EITHER
     "direction": one of "north", "south", "east", "west", OR
     "target_facility": one of the keys above (to move toward/away from it),
     plus "toward": true or false.
   Use "direction" when the planner names a compass direction or a side of the
   parcel (e.g. "closer to the north blocks" → direction "north"). Use
   "target_facility"/"toward" when the planner names another facility to move
   relative to (e.g. "move the school away from the clinic").

2. "optimise" — the planner wants the layout generally improved, tightened, or
   made more efficient without naming a specific facility or direction
   (e.g. "tighten this up", "use the space better", "this feels inefficient").

3. "revert" — the planner wants to undo changes and go back to the originally
   generated layout (e.g. "undo my changes", "start this layout over").

4. "unsupported" — anything that does not cleanly fit one of the three actions
   above. This INCLUDES:
   - Moving or resizing shelters, communities, or blocks (no such control exists).
   - Changing road layout or hierarchy (not implemented).
   - Adding, removing, or resizing any facility (not implemented).
   - Changing population, climate, duration, or other stage-1 inputs (belongs
     to an earlier stage, not this one).
   - Vague feedback that does not name a supported facility or a clear,
     general "make it better" intent.
   - SINGLING OUT ONE SPECIFIC INSTANCE of a facility type that currently has
     MORE THAN ONE instance in the layout (see FACILITY COUNTS below), using a
     positional, ordinal, or relational qualifier — e.g. "the left school",
     "the northern food point", "the second school", "the one near the river".
     The engine cannot target one instance among several of the same type; it
     can only move the whole type, or optimise the whole layout. Do NOT silently
     drop the qualifier and classify this as an ordinary move_facility on the
     type — classify it as "unsupported" instead.
     If the named facility type currently has only ONE instance, a positional
     or descriptive qualifier is not ambiguous (there is nothing else it could
     refer to) — treat it as an ordinary move_facility request in that case.
   - Anything you are not confident maps to actions 1-3.
   Always include a "reason" field in plain language explaining what is
   missing or unsupported, specific enough for the planner to understand why.
   When declining for the "singling out one instance" case, ALSO tell the
   planner what they can do instead in the same sentence: move the whole
   facility type, or run the optimiser. For example: "there are 3 schools and
   the engine can only move the schools as a type, not single out the left
   one yet; you can move all schools together, or use Optimise layout to
   reposition them."

RULES:
- Never guess a facility, direction, or target the planner did not state or
  clearly imply. If in doubt, classify as "unsupported" and say why.
- Only "facility", "target_facility" values from the list above are valid —
  if the planner names something else (e.g. "the latrines", "a shelter"),
  classify as "unsupported".
- Always include a short "reason" field (1-2 sentences) for every action,
  including the supported ones, describing what you understood.

JSON schema (always include every field; use null where not applicable):
{{
  "action": "move_facility" | "optimise" | "revert" | "unsupported",
  "facility": <one of the facility keys above, or null>,
  "direction": <"north" | "south" | "east" | "west", or null>,
  "target_facility": <one of the facility keys above, or null>,
  "toward": <true, false, or null>,
  "reason": <string>
}}
"""


def _build_system_prompt(facility_counts: dict[str, int] | None) -> str:
    if not facility_counts:
        return _FEEDBACK_SYSTEM_PROMPT_BASE
    lines = "\n".join(
        f'  "{key}": {facility_counts.get(key, 0)}' for key in MOVABLE_FACILITY_KEYS
    )
    return (
        _FEEDBACK_SYSTEM_PROMPT_BASE
        + f"\n\nFACILITY COUNTS IN THE CURRENT LAYOUT (use these to decide whether "
          f"a positional/ordinal/relational qualifier is ambiguous):\n{lines}\n"
    )


def _strip_code_fence(raw: str) -> str:
    cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    return re.sub(r"\n?```$", "", cleaned.strip(), flags=re.MULTILINE).strip()


def classify_feedback(message: str, facility_counts: dict[str, int] | None = None) -> dict:
    """
    Classify planner feedback into a structured action. Never raises — on any
    parse failure or malformed/missing fields, falls back to "unsupported"
    so a request is never silently treated as something it wasn't.

    facility_counts, if given, maps MOVABLE_FACILITY_KEYS to how many
    instances of each currently exist in the layout — used so the classifier
    can tell a genuinely ambiguous "the left school" (3 schools) from a
    harmless one (1 school) instead of silently dropping the qualifier.
    """
    fallback = {
        "action": "unsupported",
        "facility": None,
        "direction": None,
        "target_facility": None,
        "toward": None,
        "reason": "Could not interpret this request as one of the supported actions.",
    }

    messages = [{"role": "user", "content": message}]
    system_prompt = _build_system_prompt(facility_counts)
    raw = get_ai_response(messages, system_prompt=system_prompt, max_tokens=256)
    cleaned = _strip_code_fence(raw)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    action = parsed.get("action")
    if action not in ("move_facility", "optimise", "revert", "unsupported"):
        return fallback

    if action == "move_facility":
        facility = parsed.get("facility")
        direction = parsed.get("direction")
        target_facility = parsed.get("target_facility")
        if facility not in MOVABLE_FACILITY_KEYS:
            return fallback
        if direction not in ("north", "south", "east", "west") and (
            target_facility not in MOVABLE_FACILITY_KEYS or target_facility == facility
        ):
            return fallback

    result = dict(fallback)
    result.update({
        "action": action,
        "facility": parsed.get("facility"),
        "direction": parsed.get("direction"),
        "target_facility": parsed.get("target_facility"),
        "toward": parsed.get("toward"),
        "reason": parsed.get("reason") or fallback["reason"],
    })
    return result
