"""
ui/sidebar.py
=============
Renders the sidebar: profile/logout, focus timer, subject/chapter
workspace management (create/upload/delete), data source, and language.

Returns a small dict of selections (active_subject, active_chapter,
data_source, target_language) that the rest of the UI needs.
"""
import os
import base64
import shutil

import streamlit as st

from config import DATA_SOURCES, LANGUAGES
from core.paths import get_user_paths, sanitize_filename
from core.vectorstore import get_vector_store
from core.onboarding_store import reset_tutorial


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
        col_logout, col_tutorial = st.columns(2)
        with col_logout:
            if st.button("🚪 Logout", use_container_width=True):
                # Fix Issue #2: Safe dictionary deletion.
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
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

        existing_subjects = [
            d for d in os.listdir(user_paths["sources"]) if os.path.isdir(os.path.join(user_paths["sources"], d))
        ]

        # Fix Issue #13: Infinite rerun loop solved using a Form.
        with st.form("new_subject_form", clear_on_submit=True):
            new_subject = st.text_input("➕ Create New Subject")
            if st.form_submit_button("Create"):
                if new_subject.strip():
                    clean_sub = sanitize_filename(new_subject)
                    os.makedirs(os.path.join(user_paths["sources"], clean_sub), exist_ok=True)
                    st.rerun()

        active_subject = st.selectbox("Select Subject", ["Select Subject"] + existing_subjects)
        active_chapter = "Select Chapter"

        if active_subject != "Select Subject":
            subj_path = os.path.join(user_paths["sources"], active_subject)
            files = [f.rsplit(".", 1)[0] for f in os.listdir(subj_path) if f.endswith((".txt", ".pdf"))]
            active_chapter = st.selectbox("Active Chapter", ["Select Chapter"] + sorted(files))

            # Fix Issue #14: File uploader state control.
            with st.form("upload_form", clear_on_submit=True):
                uploaded_file = st.file_uploader("Upload PDF or TXT", type=["pdf", "txt"], label_visibility="collapsed")
                if st.form_submit_button("Upload"):
                    if uploaded_file is not None:
                        # Fix Issue #29: Safe filename.
                        safe_name = (
                            sanitize_filename(uploaded_file.name.rsplit(".", 1)[0])
                            + "."
                            + uploaded_file.name.rsplit(".", 1)[1]
                        )
                        with open(os.path.join(subj_path, safe_name), "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        get_vector_store(username, active_subject, force_rebuild=True)
                        st.success(f"Imported {safe_name}!")
                        st.rerun()

            if active_chapter != "Select Chapter":
                if st.button("🗑️ Delete Selected Chapter", type="secondary"):
                    for ext in [".txt", ".pdf"]:
                        tgt = os.path.join(subj_path, active_chapter + ext)
                        if os.path.exists(tgt):
                            os.remove(tgt)
                    study_folder = os.path.join(user_paths["study"], active_subject, active_chapter)
                    if os.path.exists(study_folder):
                        shutil.rmtree(study_folder)

                    # Fix Issue #37: Rebuild vector db when files are deleted.
                    get_vector_store(username, active_subject, force_rebuild=True)
                    st.rerun()

        st.markdown("---")
        data_source = st.radio("Data Source:", DATA_SOURCES)
        target_language = st.selectbox("Target Language:", LANGUAGES)

    return {
        "active_subject": active_subject,
        "active_chapter": active_chapter,
        "data_source": data_source,
        "target_language": target_language,
    }
