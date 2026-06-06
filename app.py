import streamlit as st
from src.conversation import render_input_stage

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
    st.header("Stage: Location")
    st.write("Placeholder — map-based location selection here.")
    if st.button("Next →", key="btn_location"):
        advance_stage()


def stage_summary():
    st.header("Stage: Summary")
    st.write("Placeholder — review collected inputs here.")
    if st.button("Next →", key="btn_summary"):
        advance_stage()


def stage_layout():
    st.header("Stage: Layout")
    st.write("Placeholder — generated camp layout displayed here.")
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


def main():
    init_session_state()
    st.title("Refugee Camp Layout Generator")
    STAGE_HANDLERS[st.session_state["stage"]]()


if __name__ == "__main__":
    main()
