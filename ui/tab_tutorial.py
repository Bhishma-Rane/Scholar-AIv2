"""
ui/tab_tutorial.py
=====================
The first-run tutorial: a welcome screen, three goal-based guided
pathways (pick one, walk through numbered steps pointing at real tabs),
and a full feature reference for anyone who wants the complete picture
instead of a guided walkthrough. Shown automatically once per account
(core.onboarding_store tracks completion) and re-enterable anytime via
the sidebar.
"""
import streamlit as st

from config import APP_NAME, APP_TAGLINE
from core.onboarding_store import mark_tutorial_complete
from ui.tutorial_content import PATHWAYS, FEATURE_REFERENCE


def _init_tutorial_state():
    if "tutorial_selected_pathway" not in st.session_state:
        st.session_state.tutorial_selected_pathway = None
    if "tutorial_step_index" not in st.session_state:
        st.session_state.tutorial_step_index = 0


def _render_welcome():
    st.markdown(
        f"<div style='text-align:center; font-size:42px; font-weight:800;'>🎓 Welcome to {APP_NAME}</div>"
        f"<div style='text-align:center; font-style:italic; color:#666; font-size:18px; margin-bottom:24px;'>"
        f"{APP_TAGLINE}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        """
The core loop, in four steps:

1. **Upload** a PDF or TXT chapter into a subject.
2. **Generate** study aids, flashcards, or quizzes from it.
3. **Practice** — chat with the tutor, take quizzes, drill flashcards.
4. **Track** your progress on the Dashboard and Progress tabs.

Pick what you'd like to do first, and we'll walk you through it step by step —
or skip straight to a full feature list if you'd rather explore on your own.
"""
    )


def _render_pathway_picker():
    st.markdown("#### What would you like to do first?")
    cols = st.columns(len(PATHWAYS))
    for col, (key, pathway) in zip(cols, PATHWAYS.items()):
        with col:
            if st.button(pathway["label"], use_container_width=True, key=f"pathway_btn_{key}"):
                st.session_state.tutorial_selected_pathway = key
                st.session_state.tutorial_step_index = 0
                st.rerun()


def _render_pathway_walkthrough(pathway_key: str):
    pathway = PATHWAYS[pathway_key]
    steps = pathway["steps"]
    idx = st.session_state.tutorial_step_index
    idx = max(0, min(idx, len(steps) - 1))
    step = steps[idx]

    st.markdown(f"#### {pathway['label']}")
    st.progress((idx + 1) / len(steps), text=f"Step {idx + 1} of {len(steps)}")

    with st.container(border=True):
        st.markdown(f"##### {step['title']}")
        st.caption(f"📍 Found in: {step['tab']}")
        st.markdown(step["body"])

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button("⬅️ Back", disabled=(idx == 0), use_container_width=True):
            st.session_state.tutorial_step_index -= 1
            st.rerun()
    with c2:
        if st.button("🔁 Choose a Different Path", use_container_width=True):
            st.session_state.tutorial_selected_pathway = None
            st.session_state.tutorial_step_index = 0
            st.rerun()
    with c3:
        is_last_step = idx == len(steps) - 1
        label = "✅ Finish Tutorial" if is_last_step else "Next ➡️"
        if st.button(label, type="primary", use_container_width=True):
            if is_last_step:
                return "finish"
            st.session_state.tutorial_step_index += 1
            st.rerun()
    return None


def _render_feature_reference():
    with st.expander("📋 Or just show me everything (full feature list)"):
        for item in FEATURE_REFERENCE:
            st.markdown(f"**{item['tab']}** — {item['summary']}")


def render_tutorial_tab(username: str) -> bool:
    """
    Renders the tutorial screen. Returns True if the user has just
    finished/skipped it this run (caller should st.rerun() to drop into
    the main app), False otherwise.
    """
    _init_tutorial_state()

    _render_welcome()
    st.markdown("---")

    if st.session_state.tutorial_selected_pathway is None:
        _render_pathway_picker()
        st.markdown("---")
        _render_feature_reference()
        st.markdown("---")
        if st.button("⏭️ Skip Tutorial", use_container_width=True):
            mark_tutorial_complete(username)
            return True
        return False
    else:
        result = _render_pathway_walkthrough(st.session_state.tutorial_selected_pathway)
        st.markdown("---")
        _render_feature_reference()
        if result == "finish":
            mark_tutorial_complete(username)
            return True
        return False
