"""
app.py
=======
ScholarAI by AuraStudios — "Learn. Understand. Master."
Streamlit entry point.

This file only orchestrates: page setup, session state, login, the
first-run tutorial gate, sidebar, and tab dispatch. All actual logic
lives in core/, features/, and ui/. Run with:

    streamlit run app.py

RESTRUCTURED: down from 11 tabs to 6, based on user feedback that the
old layout was overwhelming.
  - Study Tools + Flashcards + Viewer -> combined into "Study"
  - Assessment + Batch Gen (quiz half) + Question Paper -> combined into
    "Practice & Exams" (Batch Gen's old mock-exam-paper generation mode
    is REMOVED -- Question Paper replaces it entirely)
  - Tutorial -> no longer a top-level tab; replay it from Settings
    instead (most people were skipping the always-visible tab)
  - Feedback -> no longer a tab; now a small popover button in the
    header instead, so it doesn't eat a tab slot
  - NEW: Settings tab (account info, accent color picker, tutorial replay)
"""
import streamlit as st

from config import configure_page, init_session_state, inject_css, render_brand_header, DEFAULT_THEME_COLOR
from core.paths import get_user_paths
from core.onboarding_store import has_completed_tutorial
from core.bridge_client import get_theme_color, BridgeUnavailableError
from ui.auth import require_login
from ui.sidebar import render_sidebar
from ui.tab_tutorial import render_tutorial_tab, render_tutorial_overlay_if_active
from ui.tab_dashboard import render_dashboard_tab
from ui.tab_chat import render_chat_tab
from ui.tab_study import render_study_tab
from ui.tab_practice import render_practice_tab
from ui.tab_progress import render_progress_tab
from ui.tab_settings import render_settings_tab
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

render_brand_header()

if not has_completed_tutorial(username):
    finished = render_tutorial_tab(username)
    if finished:
        st.rerun()
    st.stop()

_, feedback_col = st.columns([6, 1])
with feedback_col:
    render_feedback_widget(username)

selections = render_sidebar(username, user_paths)
active_subject = selections["active_subject"]
active_chapter = selections["active_chapter"]
data_source = selections["data_source"]
target_language = selections["target_language"]

tab_dashboard, tab_chat, tab_study, tab_practice, tab_progress, tab_settings = st.tabs(
    [
        "🤖 Dashboard",
        "💬 Tutor",
        "📚 Study",
        "📝 Practice & Exams",
        "📊 Progress",
        "⚙️ Settings",
    ]
)

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

render_tutorial_overlay_if_active(username)
