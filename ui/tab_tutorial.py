"""
ui/tab_tutorial.py
=====================
The guided tour content, now rendered as a spotlight overlay (dims the
real app, highlights the relevant tab/element) instead of a plain text
card -- since most people were skipping the old text-only version.

This is no longer a top-level tab (see ui/tab_settings.py, which has the
"Replay Tutorial" button that triggers this). render_tutorial_tab() is
still used for the automatic first-run gate in app.py (a new account
sees the pathway picker full-screen before anything else), but the
step-by-step walkthrough itself now uses the spotlight overlay.
"""
import streamlit as st

from config import APP_NAME, APP_TAGLINE
from core.onboarding_store import mark_tutorial_complete
from ui.tutorial_content import PATHWAYS, FEATURE_REFERENCE
from ui.tutorial_overlay import render_tutorial_overlay, cleanup_tutorial_overlay, TUTORIAL_STEPS


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


def _render_spotlight_walkthrough():
    """
    Replaces the old plain step-card with the dimmed spotlight overlay
    (ui/tutorial_overlay.py) -- the visual highlight is rendered by that
    component, while the Next/Back/Skip controls below are plain
    Streamlit buttons (an iframe sandbox can't host clickable Streamlit
    widgets itself, so the real interaction lives here, outside it).
    """
    steps = TUTORIAL_STEPS
    idx = st.session_state.tutorial_step_index
    idx = max(0, min(idx, len(steps) - 1))

    render_tutorial_overlay(steps, idx)

    st.progress((idx + 1) / len(steps), text=f"Step {idx + 1} of {len(steps)}")

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button("⬅️ Back", disabled=(idx == 0), use_container_width=True, key="overlay_back"):
            st.session_state.tutorial_step_index -= 1
            st.rerun()
    with c2:
        if st.button("⏭️ Skip Tour", use_container_width=True, key="overlay_skip"):
            cleanup_tutorial_overlay()
            return "finish"
    with c3:
        is_last_step = idx == len(steps) - 1
        label = "✅ Finish" if is_last_step else "Next ➡️"
        if st.button(label, type="primary", use_container_width=True, key="overlay_next"):
            if is_last_step:
                cleanup_tutorial_overlay()
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
    Renders the first-run tutorial gate (full-screen, before the main
    app). Returns True once the user has finished/skipped (caller
    should st.rerun() to drop into the main app), False otherwise.
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
        result = _render_spotlight_walkthrough()
        if result == "finish":
            mark_tutorial_complete(username)
            return True
        return False


def render_tutorial_overlay_if_active(username: str):
    """
    Called from app.py on EVERY render (after the main tab UI exists),
    so the "Replay Tutorial" button in Settings can trigger the
    spotlight walkthrough over the real app -- as opposed to
    render_tutorial_tab(), which is only for the very first, full-screen,
    pre-sidebar gate.
    """
    if not st.session_state.get("show_tutorial_overlay"):
        return

    _init_tutorial_state()
    result = _render_spotlight_walkthrough()
    if result == "finish":
        st.session_state["show_tutorial_overlay"] = False
        st.session_state.tutorial_selected_pathway = None
        st.session_state.tutorial_step_index = 0
        st.rerun()
