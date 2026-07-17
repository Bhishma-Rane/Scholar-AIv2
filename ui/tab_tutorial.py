"""
ui/tab_tutorial.py
=====================
The guided tour content, rendered as a spotlight overlay (dims the real
app, highlights the relevant widget/tab) instead of a plain text card.

ARCHITECTURE NOTE 1 (first-run gate): the welcome + pathway picker is a
real Streamlit modal (st.dialog), shown ON TOP OF the already-rendered
main app -- NOT a full-screen placeholder that replaces the app. The
real app (sidebar + tabs) is always rendered first in app.py; the
dialog floats over it (Streamlit dims the background for a dialog
automatically), and once a pathway is picked we hand off to the
spotlight overlay, which draws over the real, live, already-rendered
elements underneath.

ARCHITECTURE NOTE 2 (per-step targeting): the spotlight walkthrough
pulls its step list from PATHWAYS[selected_pathway]["steps"] (see
ui/tutorial_content.py) -- i.e. whichever pathway the user actually
picked -- NOT a fixed generic list. Each of those steps carries a
"target" telling the overlay exactly which real widget to highlight
(e.g. the subject-name input, by its label text), so "Create a
Subject" highlights the subject input specifically, "Upload Your
Material" highlights the uploader specifically, etc., instead of one
big box around the whole sidebar for every step.

render_first_run_gate() and render_tutorial_overlay_if_active() are
both called from app.py AFTER the real app (sidebar + tabs) has
rendered. The latter also handles the "Replay Tutorial" button in
Settings/Sidebar -- same code path either way.
"""

import streamlit as st

from config import APP_NAME, APP_TAGLINE
from core.onboarding_store import has_completed_tutorial, mark_tutorial_complete
from ui.tutorial_content import PATHWAYS, FEATURE_REFERENCE
from ui.tutorial_overlay import render_tutorial_overlay, cleanup_tutorial_overlay


def _init_tutorial_state():
    if "tutorial_selected_pathway" not in st.session_state:
        st.session_state.tutorial_selected_pathway = None
    if "tutorial_step_index" not in st.session_state:
        st.session_state.tutorial_step_index = 0


def _current_steps():
    """
    The step list for whichever pathway is currently selected, or None
    if no pathway has been picked yet (shouldn't happen while the
    spotlight walkthrough is showing, but guarded defensively).
    """
    pathway_key = st.session_state.get("tutorial_selected_pathway")
    if pathway_key is None or pathway_key not in PATHWAYS:
        return None
    return PATHWAYS[pathway_key]["steps"]


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
                # Hand off to the spotlight walkthrough -- needs the
                # dialog closed and the real app visible underneath it.
                st.session_state["show_tutorial_overlay"] = True
                st.rerun()


def _render_feature_reference():
    with st.expander("📋 Or just show me everything (full feature list)"):
        for item in FEATURE_REFERENCE:
            st.markdown(f"**{item['tab']}** — {item['summary']}")


@st.dialog("Welcome", width="large")
def _first_run_dialog(username: str):
    """
    The welcome + pathway-picker step, as a real modal floating over
    the already-rendered app. Streamlit dims the real background behind
    a dialog automatically -- the sidebar and tabs are genuinely there,
    rendered and live, right behind this dialog.
    """
    _render_welcome()
    st.markdown("---")
    _render_pathway_picker()
    st.markdown("---")
    _render_feature_reference()
    st.markdown("---")
    if st.button("⏭️ Skip Tutorial", use_container_width=True):
        mark_tutorial_complete(username)
        st.session_state["show_tutorial_overlay"] = False
        st.rerun()


def render_first_run_gate(username: str):
    """
    Call from app.py AFTER the real app (sidebar + tabs) has rendered.
    Shows the welcome/pathway-picker dialog for users who haven't
    completed the tutorial yet and haven't already picked a pathway.
    """
    if has_completed_tutorial(username):
        return
    if st.session_state.get("show_tutorial_overlay"):
        return  # pathway already picked; the spotlight walkthrough is running
    _init_tutorial_state()
    _first_run_dialog(username)


def _render_spotlight_walkthrough():
    """
    Renders the dimmed spotlight overlay (ui/tutorial_overlay.py) over
    the real, already-rendered app, targeting whichever step belongs to
    the CURRENTLY SELECTED pathway -- plus the Next/Back/Skip controls
    (plain Streamlit buttons; an iframe can't host clickable Streamlit
    widgets itself, so the real interaction lives here, outside it).
    """
    steps = _current_steps()
    if not steps:
        # No pathway selected (shouldn't normally happen here) -- bail
        # out quietly rather than showing a broken overlay.
        return "finish"

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


def render_tutorial_overlay_if_active(username: str):
    """
    Called from app.py on EVERY render, after the real main app UI
    exists. Handles BOTH the first-run walkthrough (once a pathway has
    been picked in the dialog) and the "Replay Tutorial" walkthrough
    triggered from Settings/Sidebar -- same code path either way, since
    both just need the spotlight drawn over the real, live app, driven
    by whichever pathway is currently selected in session state.
    """
    if not st.session_state.get("show_tutorial_overlay"):
        return

    _init_tutorial_state()

    if _current_steps() is None:
        # Overlay was requested (e.g. "Replay Tutorial") but no pathway
        # has been (re-)selected yet -- show the picker dialog instead
        # of a broken/empty spotlight.
        st.session_state["show_tutorial_overlay"] = False
        _first_run_dialog(username)
        return

    result = _render_spotlight_walkthrough()
    if result == "finish":
        mark_tutorial_complete(username)
        st.session_state["show_tutorial_overlay"] = False
        st.session_state.tutorial_selected_pathway = None
        st.session_state.tutorial_step_index = 0
        st.rerun()
