"""
app.py
=======
ScholarAI by AuraStudios — "Learn. Understand. Master."
Streamlit entry point.

This file only orchestrates: page setup, session state, login, the
first-run tutorial gate, sidebar, and tab dispatch. All actual logic
lives in core/, features/, and ui/. Run with:

    streamlit run app.py
"""
import streamlit as st

from config import configure_page, init_session_state, inject_css, render_brand_header
from core.paths import get_user_paths
from core.onboarding_store import has_completed_tutorial
from ui.auth import require_login
from ui.sidebar import render_sidebar
from ui.tab_tutorial import render_tutorial_tab
from ui.tab_dashboard import render_dashboard_tab
from ui.tab_chat import render_chat_tab
from ui.tab_tools import render_tools_tab
from ui.tab_flashcards import render_flashcards_tab
from ui.tab_batch_gen import render_batch_gen_tab
from ui.tab_assessment import render_assessment_tab
from ui.tab_progress import render_progress_tab
from ui.tab_viewer import render_viewer_tab

# ---------------------------------------------------------------------
# 1. PAGE SETUP
# ---------------------------------------------------------------------
configure_page()
inject_css()
init_session_state()

# ---------------------------------------------------------------------
# 2. AUTH GATE
# ---------------------------------------------------------------------
username = require_login()
user_paths = get_user_paths(username)

# ---------------------------------------------------------------------
# 3. FIRST-RUN TUTORIAL GATE
# A brand-new account sees the tutorial full-screen, with no sidebar or
# tab clutter, until they finish or explicitly skip it. Returning users
# who want to see it again can do so via the "Tutorial" tab below — this
# gate only fires automatically once, ever, per account.
# ---------------------------------------------------------------------
render_brand_header()

if not has_completed_tutorial(username):
    finished = render_tutorial_tab(username)
    if finished:
        st.rerun()
    st.stop()

# ---------------------------------------------------------------------
# 4. SIDEBAR (subject/chapter workspace, timer, data source, language)
# ---------------------------------------------------------------------
selections = render_sidebar(username, user_paths)
active_subject = selections["active_subject"]
active_chapter = selections["active_chapter"]
data_source = selections["data_source"]
target_language = selections["target_language"]

# ---------------------------------------------------------------------
# 5. TABBED APPLICATION MATRIX
# ---------------------------------------------------------------------
tab_dashboard, tab_chat, tab_tools, tab_flash, tab_gen, tab_quiz, tab_progress, tab_viewer, tab_tutorial = st.tabs(
    [
        "🤖 Dashboard",
        "💬 Tutor",
        "🛠️ Study Tools",
        "🗂️ Flashcards",
        "⚡ Batch Gen",
        "📝 Assessment",
        "📊 Progress",
        "📄 Viewer",
        "❓ Tutorial",
    ]
)

with tab_dashboard:
    render_dashboard_tab(username, active_subject, target_language)

with tab_chat:
    render_chat_tab(username, active_subject, active_chapter, data_source, target_language)

with tab_tools:
    render_tools_tab(username, active_subject, active_chapter, target_language)

with tab_flash:
    render_flashcards_tab(username, active_subject, active_chapter, target_language)

with tab_gen:
    render_batch_gen_tab(username, active_subject, active_chapter, target_language)

with tab_quiz:
    render_assessment_tab(username, active_subject, active_chapter, target_language)

with tab_progress:
    render_progress_tab(username, active_subject)

with tab_viewer:
    render_viewer_tab(username, active_subject, active_chapter)

with tab_tutorial:
    render_tutorial_tab(username)
