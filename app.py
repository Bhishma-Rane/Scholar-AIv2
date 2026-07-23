"""
app.py
======
ScholarAI by AuraStudios — "Learn. Understand. Master."
Streamlit entry point.

This file only orchestrates: page setup, session state, login, the
first-run tutorial gate, sidebar, and tab dispatch. All actual logic
lives in core/, features/, and ui/. Run with:

    streamlit run app.py

UPDATED: subject/chapter/upload management moved out of the sidebar and
into a new "📁 Workspace" tab — now the FIRST tab, so setup is the first
thing a user sees and does, with clear step-by-step instructions,
instead of being buried in a cramped sidebar section.
"""

import streamlit as st

from config import configure_page, init_session_state, inject_css, DEFAULT_THEME_COLOR, ADMIN_USERNAME
from core.paths import get_user_paths
from core.bridge_client import get_theme_color, BridgeUnavailableError
from ui.auth import require_login
from ui.sidebar import render_sidebar
from ui.tab_workspace import render_workspace_tab
from ui.tab_tutorial import render_first_run_gate, render_tutorial_overlay_if_active
from ui.tab_dashboard import render_dashboard_tab
from ui.tab_chat import render_chat_tab
from ui.tab_study import render_study_tab
from ui.tab_practice import render_practice_tab
from ui.tab_progress import render_progress_tab
from ui.tab_settings import render_settings_tab
from ui.tab_admin import render_admin_tab
from ui.feedback_widget import render_feedback_widget

configure_page()
init_session_state()

username = require_login()
user_paths = get_user_paths(username)

if "theme_color" not in st.session_state or st.session_state.get("_theme_color_loaded_for") != username:
    try:
        st.session_state["theme_color"] = get_theme_color(username)
    except BridgeUnavailableError:
        st.session_state["theme_color"] = DEFAULT_THEME_COLOR
    st.session_state["_theme_color_loaded_for"] = username

inject_css(st.session_state["theme_color"])

_, feedback_col = st.columns([6, 1])
with feedback_col:
    render_feedback_widget(username)

sidebar_selections = render_sidebar(username)
data_source = sidebar_selections["data_source"]
target_language = sidebar_selections["target_language"]

is_admin = username == ADMIN_USERNAME

tab_labels = [
    "📁 Workspace",
    "🤖 Dashboard",
    "💬 Tutor",
    "📚 Study",
    "📝 Practice & Exams",
    "📊 Progress",
    "⚙️ Settings",
]
if is_admin:
    tab_labels.append("🔐 Admin")

tabs = st.tabs(tab_labels)
(
    tab_workspace, tab_dashboard, tab_chat, tab_study,
    tab_practice, tab_progress, tab_settings,
) = tabs[:7]
tab_admin = tabs[7] if is_admin else None

with tab_workspace:
    workspace_selections = render_workspace_tab(username, user_paths)

active_subject = workspace_selections["active_subject"]
active_chapter = workspace_selections["active_chapter"]

with tab_dashboard:
    render_dashboard_tab(username, active_subject, target_language)

with tab_chat:
    render_chat_tab(username, active_subject, active_chapter, data_source, target_language)

with tab_study:
    render_study_tab(username, active_subject, active_chapter, target_language)

with tab_practice:
    render_practice_tab(username, active_subject, active_chapter, target_language)

with tab_progress:
    render_progress_tab(username, active_subject)

with tab_settings:
    render_settings_tab(username)

if tab_admin is not None:
    with tab_admin:
        render_admin_tab()

# Both of these run AFTER the real app above is fully rendered, so
# whichever one is active (first-run dialog, or either flavor of the
# spotlight walkthrough) has real, live elements to show/dim/target.
render_first_run_gate(username)
render_tutorial_overlay_if_active(username)
