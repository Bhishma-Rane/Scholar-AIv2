"""
ui/sidebar.py
=============
Renders the sidebar: profile/logout, focus timer, subject/chapter
workspace management (create/upload/delete), data source, and language.

Returns a small dict of selections (active_subject, active_chapter,
data_source, target_language) that the rest of the UI needs.

CHANGED: subject creation, file upload, file listing, and delete all
go through the storage bridge now (core/paths.py's bridge-backed
helpers) instead of local disk — so subjects and uploaded PDFs survive
a Streamlit Cloud container restart. Logout now calls ui.auth.logout()
so the bridge-issued auto-login token gets revoked, instead of just
clearing session_state (which would leave a stale token in the URL
that silently logs the user back in).
"""
import base64

import streamlit as st

from config import DATA_SOURCES, LANGUAGES
from core.paths import (
    get_user_paths,
    sanitize_filename,
    list_subjects,
    create_subject,
    list_subject_files,
    upload_subject_file,
    delete_subject_file,
)
from core.bridge_client import BridgeUnavailableError, is_bridge_reachable
from core.vectorstore import get_vector_store
from core.onboarding_store import reset_tutorial
from ui.auth import logout as auth_logout


def _render_focus_timer(timer_mins: int):
    html_content = f"""
    <!DOCTYPE html><html><head><style>body {{ margin: 0; font-family: sans-serif; }}</style></head><body>
        <div id="tD" style="font-size:30px; font-family:monospace; text-align:center; font-weight:bold; color:#333; background:#f0f2f6; border-radius:10px; padding:10px; margin-bottom:10px;">{timer_mins}:00</div>
        <div style="text-align:center;">
            <button onclick="sT()" style="padding:5px 15px; border:none; background:#4CAF50; color:white; border-radius:5px; cursor:pointer;">Start</button>
            <button onclick="rT()" style="padding:5px 15px; border:none; background:#f44336; color:white; border-radius:5px; cursor:pointer;">Reset</button>
        </div>
        <script>
        let iv; let tl = {timer_mins * 60};
        function uD() {{ let m = Math.floor(tl / 60); let s = tl % 60; document.getElementById('tD').innerText = m + ":" + (s < 10 ? "0" : "") + s; }}
        function sT() {{ clearInterval(iv); iv = setInterval(() => {{ if(tl > 0) {{ tl--; uD(); }} else {{ clearInterval(iv); alert('Complete!'); }} }}, 1000); }}
        function rT() {{ clearInterval(iv); tl = {timer_mins * 60}; uD(); }}
        </script>
    </body></html>
    """
    b64_html = base64.b64encode(html_content.encode("utf-8")).decode("utf-8")
    st.markdown(
        f'<iframe src="data:text/html;base64,{b64_html}" width="100%" height="120" style="border:none; overflow:hidden;"></iframe>',
        unsafe_allow_html=True,
    )


def render_sidebar(username: str, user_paths: dict) -> dict:
    with st.sidebar:
        st.title("🎓 ScholarAI")
        st.caption("*Learn. Understand. Master.*")
        st.caption(f"👤 Profile: **{username.capitalize()}**")

        if not is_bridge_reachable():
            st.warning(
                "⚠️ Storage bridge unreachable — subjects/files may not load or save. "
                "Check that the bridge server and its ngrok tunnel are running.",
                icon="⚠️",
            )

        col_logout, col_tutorial = st.columns(2)
        with col_logout:
            if st.button("🚪 Logout", use_container_width=True):
                auth_logout()
                st.rerun()
        with col_tutorial:
            if st.button("❓ Replay Tutorial", use_container_width=True):
                reset_tutorial(username)
                st.session_state.tutorial_selected_pathway = None
                st.session_state.tutorial_step_index = 0
                st.rerun()

        st.markdown("---")
        st.subheader("⏱️ Focus Timer")
        timer_mins = st.number_input("Set Timer (Minutes)", min_value=1, max_value=120, value=25)
        _render_focus_timer(timer_mins)

        st.markdown("---")
        st.subheader("📁 Subject Workspace")

        try:
            existing_subjects = list_subjects(username)
        except BridgeUnavailableError:
            existing_subjects = []
            st.error("Could not load subjects — storage bridge is unreachable.")

        # Fix Issue #13: Infinite rerun loop solved using a Form.
        with st.form("new_subject_form", clear_on_submit=True):
            new_subject = st.text_input("➕ Create New Subject")
            if st.form_submit_button("Create"):
                if new_subject.strip():
                    clean_sub = sanitize_filename(new_subject)
                    try:
                        create_subject(username, clean_sub)
                        st.rerun()
                    except BridgeUnavailableError:
                        st.error("Could not create subject — storage bridge is unreachable.")

        active_subject = st.selectbox("Select Subject", ["Select Subject"] + existing_subjects)
        active_chapter = "Select Chapter"

        if active_subject != "Select Subject":
            try:
                subject_files = list_subject_files(username, active_subject)
            except BridgeUnavailableError:
                subject_files = []
                st.error("Could not load files for this subject — storage bridge is unreachable.")

            files = [f.rsplit(".", 1)[0] for f in subject_files if f.endswith((".txt", ".pdf"))]
            active_chapter = st.selectbox("Active Chapter", ["Select Chapter"] + sorted(files))

            # Fix Issue #14: File uploader state control.
            with st.form("upload_form", clear_on_submit=True):
                uploaded_file = st.file_uploader("Upload PDF or TXT", type=["pdf", "txt"], label_visibility="collapsed")
                if st.form_submit_button("Upload"):
                    if uploaded_file is not None:
                        try:
                            stored_name = upload_subject_file(
                                username, active_subject, uploaded_file.name, uploaded_file.getbuffer().tobytes()
                            )
                            get_vector_store(username, active_subject, force_rebuild=True)
                            st.success(f"Imported {stored_name}!")
                            st.rerun()
                        except BridgeUnavailableError:
                            st.error("Upload failed — storage bridge is unreachable. Please try again shortly.")

            if active_chapter != "Select Chapter":
                if st.button("🗑️ Delete Selected Chapter", type="secondary"):
                    try:
                        for ext in [".txt", ".pdf"]:
                            candidate = active_chapter + ext
                            if candidate in subject_files:
                                delete_subject_file(username, active_subject, candidate)

                        # Locally-generated study content (quizzes, flashcards,
                        # etc.) is still on local disk for now — see
                        # core/paths.py module docstring for the follow-up.
                        import os
                        import shutil
                        study_folder = os.path.join(user_paths["study"], active_subject, active_chapter)
                        if os.path.exists(study_folder):
                            shutil.rmtree(study_folder)

                        # Fix Issue #37: Rebuild vector db when files are deleted.
                        get_vector_store(username, active_subject, force_rebuild=True)
                        st.rerun()
                    except BridgeUnavailableError:
                        st.error("Delete failed — storage bridge is unreachable. Please try again shortly.")

        st.markdown("---")
        data_source = st.radio("Data Source:", DATA_SOURCES)
        target_language = st.selectbox("Target Language:", LANGUAGES)

    return {
        "active_subject": active_subject,
        "active_chapter": active_chapter,
        "data_source": data_source,
        "target_language": target_language,
    }
