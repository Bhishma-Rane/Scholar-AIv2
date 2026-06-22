"""
ui/tab_viewer.py
==================
The "Viewer" tab: browse all generated files for the active chapter
(guides, quizzes, flashcards, mock exams), download them raw, or
export .txt files to PDF.
"""
import os

import streamlit as st

from core.paths import get_chapter_paths
from core.pdf_export import create_pdf_from_text


def render_viewer_tab(username: str, active_subject: str, active_chapter: str):
    st.header("📄 Offline Document Viewer")

    if active_chapter == "Select Chapter":
        st.warning("Choose an active chapter.")
        return

    paths = get_chapter_paths(username, active_subject, active_chapter)
    all_files = []
    for d in paths.values():
        if os.path.exists(d):
            all_files.extend([os.path.join(d, f) for f in os.listdir(d) if f.endswith((".txt", ".json"))])

    if not all_files:
        st.info("No generated files yet for this chapter.")
        return

    file_dict = {os.path.basename(f): f for f in all_files}
    selected_file = st.selectbox("Select file:", list(file_dict.keys()))
    file_path = file_dict[selected_file]

    # Fix Issue #9: Viewer crash on binary.
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    c1, c2 = st.columns(2)
    c1.download_button("📥 Raw File", data=content, file_name=selected_file)

    if selected_file.endswith(".txt"):
        if c2.button("📄 Export to PDF"):
            pdf_result = create_pdf_from_text(content, file_path)
            if "Error" not in pdf_result and "Failure" not in pdf_result:
                with open(pdf_result, "rb") as pdf_file:
                    st.download_button(
                        "📥 Download PDF",
                        data=pdf_file,
                        file_name=selected_file.replace(".txt", ".pdf"),
                        mime="application/pdf",
                    )
            else:
                st.error(pdf_result)

    with st.container(border=True):
        if selected_file.endswith(".json"):
            try:
                st.json(content)
            except Exception:
                st.text(content)
        else:
            st.markdown(content)
