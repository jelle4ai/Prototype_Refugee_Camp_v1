import json
import re
import streamlit as st
from PIL import Image, ImageDraw
from src.ai_client import get_ai_response
from src.requirements_engine import compute_required_area


def _brand_avatar(hex_color: str) -> Image.Image:
    """64×64 circular avatar filled with the given Hamlet brand colour."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    draw.ellipse([0, 0, size - 1, size - 1], fill=(r, g, b, 255))
    return img


_AI_AVATAR   = _brand_avatar("#1F4788")   # Hamlet Indigo for the AI assistant
_USER_AVATAR = _brand_avatar("#8A8579")   # Muted warm-grey for the human

# These nine fields gate the handoff to the map stage.
REQUIRED_FIELDS = [
    "city", "population", "men", "women", "children",
    "climate", "duration", "cultural_notes", "special_needs",
]

# Collected through chat; do not gate the handoff.
SERVICE_FIELDS = ["cause", "water_source", "power_source", "sanitation"]

# The extraction call may ONLY write these fields.
# population / men / women / children are intentionally excluded:
# they can only be set via the quick-input Set button.
EXTRACTABLE_FIELDS = [
    "city", "climate", "duration", "cultural_notes", "special_needs",
    "cause", "water_source", "power_source", "sanitation",
]

_CONVERSATION_SYSTEM_PROMPT_BASE = """\
You are a friendly, calm humanitarian planning assistant. Your role in this conversation \
is STRICTLY LIMITED to gathering the information needed to plan a refugee camp site, \
then handing off to the map stage.

WHAT YOU MUST NOT DO:
- Invent, describe, or suggest a camp layout, facility counts, housing-unit numbers, \
  cluster designs, zone arrangements, or any spatial plan. The tool generates those \
  in a later stage from a scored algorithm — do not pre-empt it.
- Choose a specific plot of land autonomously. You can explain what makes a site \
  suitable, but the user confirms any selection.
- If the user asks for a layout or facility list, say: "The tool will generate a \
  scored layout on the map once the site is confirmed — let's finish gathering \
  the basics first." Then ask for the next missing input.
- NEVER state, announce, guess, or repeat any headcount figure (population total, \
  or the men / women / children split). If headcount comes up, say: \
  "Please enter the numbers using the Men / Women / Children boxes in the panel \
  above the chat, then click Set — I'll pick them up automatically." \
  Do not invent or confirm a figure yourself.

WHAT YOU MAY DO:
- Gather the required inputs (city, climate, duration, cultural notes, special needs), \
  one question at a time.
- Once population is shown in the sidebar as set, state the approximate land needed \
  exactly once: e.g. "For 2,000 people you will need roughly 9 hectares, about \
  335 × 335 metres." (Formula: total area = population × 45 m²; suggested side = \
  √(total area × 1.25).) Do this exactly once — do not repeat it every turn.
- After the core fields are covered, ask about each of the following service details \
  once each, one at a time. These are optional — if the user says they do not know, \
  accept that and move on: \
  (1) cause or reason for the displacement, \
  (2) available water source (e.g. municipal, borehole, trucking, river), \
  (3) available power source (e.g. grid, generators, solar), \
  (4) planned sanitation approach (e.g. portable toilets, pit latrines, sewer). \
  Do not re-ask a service field that is already listed under ALREADY KNOWN.
- If asked about site selection, share these general criteria: flat land, near existing \
  roads, away from natural hazards (floods, earthquakes, landslides), accessible to \
  services, away from industrial or conflict hazards.
- Acknowledge values the user has already entered; do not re-ask for anything listed \
  under ALREADY KNOWN below.

STYLE:
- Ask EXACTLY ONE question at a time.
- Never invent or assume values the user has not stated.
- If several facts arrive at once, acknowledge all and ask only for the next missing item.
- Use plain, accessible language. Be warm and patient.
- When all nine required inputs are collected, tell the user they can proceed to the \
  map stage to find a site.\
"""

EXTRACTION_SYSTEM_PROMPT = """\
You are a structured data extraction assistant. Read the conversation provided and \
extract planning information about a refugee camp.

Return ONLY a valid JSON object with exactly these nine fields — no prose, no \
explanation, no markdown, no code fences, just the raw JSON:

{
  "city": <string or null>,
  "climate": <"warm" or "cold" or null>,
  "duration": <"emergency" or "protracted" or null>,
  "cultural_notes": <string, "None specified", or null>,
  "special_needs": <string, "None specified", or null>,
  "cause": <string or null>,
  "water_source": <string or null>,
  "power_source": <string or null>,
  "sanitation": <string or null>
}

Extraction rules:
- Reason over the ENTIRE conversation from start to finish, not just the most \
  recent message. Include values the user mentioned in any earlier message.
- Extract ONLY values the user has explicitly stated. Never infer or guess.
- Use null for any field not yet mentioned by the user in any message.
- cultural_notes and special_needs: if the user explicitly says there are none \
  (e.g. "none", "no", "n/a", "nothing", "not applicable", "none that I know of", \
  "no special needs", "no cultural notes"), return the string "None specified" — \
  this is a real answer. null means the topic was never mentioned at all.
- If the user corrects a field they gave earlier, use the newer value.
- climate: "warm" for hot/arid/tropical; "cold" for cold/temperate/mountain/arctic.
- duration: "emergency" for acute/short-term; "protracted" for long-term/ongoing.
- Do NOT extract headcount figures. population, men, women, and children are \
  entered only via the UI panel and must never appear in this JSON.\
"""

_OPENING_MESSAGE = (
    "Hello! I'm here to help you plan a refugee camp site. "
    "I'll ask a few questions to understand the situation — "
    "feel free to share as much or as little as you know right now.\n\n"
    "To start: where is the camp being planned, and what can you tell me about the situation?"
)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_conversation_prompt() -> str:
    inputs = st.session_state["site_inputs"]
    all_tracked = REQUIRED_FIELDS + SERVICE_FIELDS
    known = {k: v for k, v in inputs.items() if k in all_tracked and v is not None}
    prompt = _CONVERSATION_SYSTEM_PROMPT_BASE
    if known:
        lines = "\n".join(f"  {k}: {v}" for k, v in known.items())
        prompt += f"\n\nALREADY KNOWN (do not ask again; acknowledge if relevant):\n{lines}"
    return prompt


def _update_area(inputs: dict, population: int) -> None:
    result = compute_required_area(population)
    inputs["required_area_m2"] = result["total_area_m2"]
    inputs["suggested_side_m"] = result["suggested_side_m"]


def _extract_inputs(chat_history: list) -> dict | None:
    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in chat_history
    )
    messages = [
        {
            "role": "user",
            "content": "Extract the required planning fields from this conversation:\n\n"
                       + conversation_text,
        }
    ]
    raw = get_ai_response(messages, system_prompt=EXTRACTION_SYSTEM_PROMPT, max_tokens=512)
    cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


def _merge_inputs(extracted: dict | None) -> None:
    """Write extracted values into session state, but ONLY for EXTRACTABLE_FIELDS.
    population / men / women / children are never written here."""
    if not extracted or not isinstance(extracted, dict):
        return
    inputs = st.session_state["site_inputs"]
    for field in EXTRACTABLE_FIELDS:
        value = extracted.get(field)
        if value is not None:
            inputs[field] = value


def _all_collected() -> bool:
    inputs = st.session_state["site_inputs"]
    return all(inputs.get(f) is not None for f in REQUIRED_FIELDS)


# ── Sidebar: collected inputs ─────────────────────────────────────────────────

def _render_sidebar(inputs: dict) -> None:
    with st.sidebar:
        st.markdown("### Planning inputs")
        st.divider()

        def _row(label: str, value, fmt=None):
            if value is not None:
                display = fmt(value) if fmt else str(value)
                st.markdown(f"**{label}:** {display}")
            else:
                st.markdown(f"**{label}:** :gray[not set yet]")

        _row("Location",   inputs.get("city"))
        _row("Population", inputs.get("population"), lambda v: f"{v:,}")

        m, w, c = inputs.get("men"), inputs.get("women"), inputs.get("children")
        if any(x is not None for x in (m, w, c)):
            def _n(x): return f"{x:,}" if x is not None else "—"
            st.markdown(f"**Men / Women / Children:** {_n(m)} / {_n(w)} / {_n(c)}")
        else:
            st.markdown("**Men / Women / Children:** :gray[not set yet]")

        _row("Climate",       inputs.get("climate"),  str.capitalize)
        _row("Duration",      inputs.get("duration"), str.capitalize)
        _row("Cultural notes", inputs.get("cultural_notes"))
        _row("Special needs",  inputs.get("special_needs"))

        if inputs.get("required_area_m2"):
            ha   = inputs["required_area_m2"] / 10_000
            side = inputs["suggested_side_m"]
            st.markdown(f"**Required area:** ~{ha:.1f} ha (about {side} × {side} m)")
        else:
            st.markdown("**Required area:** :gray[not set yet]")

        # Site services — subheading appears only once at least one is filled
        svc = {f: inputs.get(f) for f in SERVICE_FIELDS}
        if any(v is not None for v in svc.values()):
            st.divider()
            st.markdown("#### Site services")
            _row("Cause",        svc["cause"])
            _row("Water source", svc["water_source"])
            _row("Power source", svc["power_source"])
            _row("Sanitation",   svc["sanitation"])


# ── Quick inputs: inline row above chat input ─────────────────────────────────

def _render_quick_inputs(inputs: dict) -> None:
    """Compact controls for unset fields only. Disappears when all filled."""
    needs = {
        "climate":  inputs.get("climate")  is None,
        "duration": inputs.get("duration") is None,
        "men":      inputs.get("men")      is None,
        "women":    inputs.get("women")    is None,
        "children": inputs.get("children") is None,
    }

    if not any(needs.values()):
        return

    st.caption("Quick inputs — fill here or describe in the chat:")

    # ── Row 1: categorical buttons ───────────────────────────────
    btn_specs = []
    if needs["climate"]:
        btn_specs += [("Warm",  "climate",  "warm",  1),
                      ("Cold",  "climate",  "cold",  1)]
    if needs["duration"]:
        btn_specs += [("Emergency",  "duration", "emergency",  1.5),
                      ("Protracted", "duration", "protracted", 1.5)]

    if btn_specs:
        widths = [s[3] for s in btn_specs] + [4]
        cols = st.columns(widths)
        for i, (label, field, value, _) in enumerate(btn_specs):
            cur = inputs.get(field)
            if cols[i].button(
                label,
                use_container_width=True,
                type="primary" if cur == value else "secondary",
                key=f"qbtn_{value}",
            ):
                inputs[field] = value
                st.rerun()

    # ── Breakdown boxes with a single Set button ──────────────────
    breakdown_fields = [f for f in ("men", "women", "children") if needs[f]]
    if breakdown_fields:
        # Wider columns for inputs, narrower for Set button so grouping is clear
        cols = st.columns([2] * len(breakdown_fields) + [1])
        drafts: dict[str, int] = {}
        for i, field in enumerate(breakdown_fields):
            drafts[field] = int(cols[i].number_input(
                field.capitalize(), min_value=0, step=100, key=f"draft_{field}",
            ))

        if cols[len(breakdown_fields)].button(
            "Set", key="set_breakdown", use_container_width=True
        ):
            if all(drafts[f] > 0 for f in breakdown_fields):
                for f in breakdown_fields:
                    inputs[f] = drafts[f]
                m = inputs.get("men")
                w = inputs.get("women")
                c = inputs.get("children")
                if m is not None and w is not None and c is not None:
                    inputs["population"] = m + w + c
                    _update_area(inputs, m + w + c)
                st.rerun()

        # Warn when draft values don't match committed total
        draft_m = drafts.get("men", inputs.get("men") or 0)
        draft_w = drafts.get("women", inputs.get("women") or 0)
        draft_c = drafts.get("children", inputs.get("children") or 0)
        if draft_m > 0 and draft_w > 0 and draft_c > 0:
            draft_sum = draft_m + draft_w + draft_c
            committed_pop = inputs.get("population")
            if committed_pop and draft_sum != committed_pop:
                st.caption(
                    f"Note: {draft_m:,} + {draft_w:,} + {draft_c:,} = {draft_sum:,}, "
                    f"not {committed_pop:,}. Clicking Set will update the total."
                )


# ── Completion summary ────────────────────────────────────────────────────────

def _render_completion_summary(inputs: dict) -> None:
    ha   = inputs.get("required_area_m2", 0) / 10_000
    side = inputs.get("suggested_side_m", 0)

    st.success("All required information has been collected.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**City:** {inputs.get('city', '—')}")
        pop = inputs.get("population", 0)
        st.markdown(f"**Population:** {pop:,}")
        st.markdown(
            f"- Men: {inputs.get('men', 0):,} / "
            f"Women: {inputs.get('women', 0):,} / "
            f"Children: {inputs.get('children', 0):,}"
        )
        st.markdown(f"**Required area:** ~{ha:.1f} ha (plot ~{side} × {side} m)")
    with col2:
        climate  = (inputs.get("climate")  or "").capitalize()
        duration = (inputs.get("duration") or "").capitalize()
        st.markdown(f"**Climate:** {climate}")
        st.markdown(f"**Duration:** {duration}")
        st.markdown(f"**Cultural notes:** {inputs.get('cultural_notes', '—')}")
        st.markdown(f"**Special needs:** {inputs.get('special_needs', '—')}")

    st.divider()
    if st.button("Find a site on the map", type="primary", use_container_width=True):
        st.session_state["stage"] = "location"
        st.rerun()


# ── Main render entry point ───────────────────────────────────────────────────

def render_input_stage() -> None:
    st.header("Camp Planning — Tell us about the situation")

    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = [
            {"role": "assistant", "content": _OPENING_MESSAGE}
        ]

    inputs = st.session_state["site_inputs"]

    # Recompute population from committed components each render (Set button is
    # the only source; this just keeps the derived total in sync).
    m, w, c = inputs.get("men"), inputs.get("women"), inputs.get("children")
    if m is not None and w is not None and c is not None:
        inputs["population"] = m + w + c

    pop = inputs.get("population")
    if pop and pop > 0:
        _update_area(inputs, pop)

    # Sidebar always renders (collapses natively via Streamlit arrow)
    _render_sidebar(inputs)

    # Full-width chat history
    _avatar_map = {"assistant": _AI_AVATAR, "user": _USER_AVATAR}
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"], avatar=_avatar_map.get(msg["role"])):
            st.write(msg["content"])

    # Generate AI reply when the last message is from the user
    history = st.session_state["chat_history"]
    if history and history[-1]["role"] == "user":
        with st.chat_message("assistant", avatar=_AI_AVATAR):
            with st.spinner(""):
                reply = get_ai_response(
                    history,
                    system_prompt=_build_conversation_prompt(),
                )
            st.write(reply)
        history.append({"role": "assistant", "content": reply})
        _merge_inputs(_extract_inputs(history))
        st.rerun()

    # Completion summary + handoff (replaces quick inputs when done)
    if _all_collected():
        _render_completion_summary(inputs)
    else:
        # Inline quick-input row above the chat box
        _render_quick_inputs(inputs)

    # Sticky chat input (page footer)
    if user_input := st.chat_input("Type your message here…"):
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        st.rerun()
