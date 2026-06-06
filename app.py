import streamlit as st
import streamlit.components.v1 as components
from src.conversation import render_input_stage
from src.location import render_location_stage
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


def stage_layout():
    st.header("Stage: Layout")
    st.write("Placeholder — generated camp layout displayed here.")
    site = st.session_state.get("site")
    if site:
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
