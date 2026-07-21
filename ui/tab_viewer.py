"""
ui/tab_viewer.py
==================
The "Viewer" tab: browse all generated files for the active chapter
(guides, quizzes, flashcards, mock exams), download them raw, or
export .txt files to PDF.

CHANGED: this used to walk core.paths.get_chapter_paths()'s local
folders directly. Those folders live on Streamlit Cloud's ephemeral
container disk, so this tab silently lost track of every generated
file on restart/redeploy -- even ones that (before this fix) were
never actually visible here because they'd already vanished. Now reads
the same bridge-backed core.content_store every generator writes to,
so this tab is always in sync with what's actually saved.
"""
import os
import tempfile

import streamlit as st

from core.content_store import list_chapter_content, load_text
from core.pdf_export import create_pdf_from_text

# Friendlier labels for the internal category names used by content_store.
CATEGORY_LABELS = {
    "guides": "Study Guide",
    "flashcards": "Flashcards",
    "mock": "Mock Exam",
    "mcq": "Quiz",
}


def render_viewer_tab(username: str, active_subject: str, active_chapter: str):
    st.header("📄 Offline Document Viewer")

    if active_chapter == "Select Chapter":
        st.warning("Choose an active chapter.")
        return

    items = list_chapter_content(username, active_subject, active_chapter)
    # Only show files the Viewer can actually render/export -- .txt and .json.
    items = [i for i in items if i["filename"].endswith((".txt", ".json"))]

    if not items:
        st.info("No generated files yet for this chapter.")
        return

    def _label(item):
        cat_label = CATEGORY_LABELS.get(item["category"], item["category"])
        return f"[{cat_label}] {item['filename']}"

    item_dict = {_label(i): i for i in items}
    selected_label = st.selectbox("Select file:", list(item_dict.keys()))
    selected = item_dict[selected_label]
    selected_file = selected["filename"]

    content = load_text(username, active_subject, active_chapter, selected["category"], selected_file)
    if content is None:
        st.error("This file couldn't be loaded (it may have just been deleted). Try refreshing.")
        return

    c1, c2 = st.columns(2)
    c1.download_button("📥 Raw File", data=content, file_name=selected_file)

    if selected_file.endswith(".txt"):
        if c2.button("📄 Export to PDF"):
            # create_pdf_from_text() needs a filesystem path to write the
            # PDF to -- this is a genuinely scratch/temporary file (not
            # persisted user data, see core/paths.py's docstring), so a
            # temp directory is the right place for it, not the bridge.
            scratch_path = os.path.join(tempfile.gettempdir(), selected_file)
            pdf_result = create_pdf_from_text(content, scratch_path)
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
