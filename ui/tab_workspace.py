"""
ui/tab_workspace.py
====================
Renders the "Workspace" tab: subject/chapter management (create/upload/
delete). This used to live in the sidebar (ui/sidebar.py) but was moved
here so it's the first thing a user sees and interacts with, as a clear,
guided setup step instead of a cramped sidebar section.

Returns a small dict of selections (active_subject, active_chapter) that
the rest of the UI needs — same contract the sidebar used to fulfill.
"""

import streamlit as st

from core.paths import (
    sanitize_filename,
    list_subjects,
    create_subject,
    list_subject_files,
    upload_subject_file,
    delete_subject_file,
    delete_subject_remote,
    delete_chapter_local_content,
    delete_subject_local_content,
)
from core.bridge_client import BridgeUnavailableError, is_bridge_reachable
from core.vectorstore import get_vector_store


def render_workspace_tab(username: str, user_paths: dict) -> dict:
    st.header("📁 Workspace")
    st.markdown(
        "Set up what ScholarAI studies from here. This is where you create "
        "**subjects**, upload the **chapter files** ScholarAI reads from, and "
        "pick which chapter is currently **active** across the other tabs."
    )

    if not is_bridge_reachable():
        st.warning(
            "⚠️ Storage bridge unreachable — subjects/files may not load or save. "
            "Check that the bridge server and its ngrok tunnel are running.",
            icon="⚠️",
        )

    st.markdown("---")

    # ------------------------------------------------------------
    # Step 1 — Subject
    # ------------------------------------------------------------
    st.subheader("Step 1 · Create or select a subject")
    st.caption(
        "A subject is a top-level folder — e.g. \"Physics\" or \"World History\". "
        "Create a new one, or pick an existing subject from the dropdown below."
    )

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

    if active_subject == "Select Subject":
        st.info("👆 Create or select a subject above to continue.")
        return {
            "active_subject": active_subject,
            "active_chapter": active_chapter,
        }

    st.markdown("---")

    # ------------------------------------------------------------
    # Step 2 — Upload chapter files
    # ------------------------------------------------------------
    st.subheader("Step 2 · Upload chapter files")
    st.caption(
        "**What to upload:** one **PDF or TXT** file per chapter — e.g. the "
        "actual textbook chapter, lecture notes, or study material text. "
        "The filename (minus extension) becomes the chapter name, so name "
        "the file the way you'd want the chapter listed, e.g. "
        "`Chapter 3 - Thermodynamics.pdf`."
    )

    try:
        subject_files = list_subject_files(username, active_subject)
    except BridgeUnavailableError:
        subject_files = []
        st.error("Could not load files for this subject — storage bridge is unreachable.")

    # Fix Issue #14: File uploader state control.
    with st.form("upload_form", clear_on_submit=True):
        uploaded_file = st.file_uploader("Upload PDF or TXT", type=["pdf", "txt"])
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

    st.markdown("---")

    # ------------------------------------------------------------
    # Step 3 — Active chapter
    # ------------------------------------------------------------
    st.subheader("Step 3 · Pick your active chapter")
    st.caption(
        "Whichever chapter is selected here is what the Tutor, Study, and "
        "Practice & Exams tabs will use."
    )

    files = [f.rsplit(".", 1)[0] for f in subject_files if f.endswith((".txt", ".pdf"))]
    if not files:
        st.info("No chapter files uploaded yet for this subject — upload one in Step 2.")
    active_chapter = st.selectbox("Active Chapter", ["Select Chapter"] + sorted(files))

    st.markdown("---")

    # ------------------------------------------------------------
    # Step 4 — Next Step
    # ------------------------------------------------------------
    st.subheader("Step 4 · Next steps from here")
    st.caption(
        "Whichever chapter is selected here is what the Tutor, Study, and "
        "Go on! Try to generate some study materials in the Study/Generate tab!"
    )

    st.markdown("---")

    # ------------------------------------------------------------
    # Danger zone — delete chapter / subject
    # ------------------------------------------------------------
    st.subheader("⚠️ Danger zone")

    if active_chapter != "Select Chapter":
        with st.popover("🗑️ Delete Selected Chapter", use_container_width=True):
            st.warning(
                f"Delete **{active_chapter}** and all its quizzes, flashcards, "
                f"and study guides? This can't be undone."
            )
            if st.button("Confirm delete chapter", type="primary", key="confirm_delete_chapter"):
                try:
                    for ext in [".txt", ".pdf"]:
                        candidate = active_chapter + ext
                        if candidate in subject_files:
                            delete_subject_file(username, active_subject, candidate)
                    delete_chapter_local_content(username, active_subject, active_chapter)
                    # Fix Issue #37: Rebuild vector db when files are deleted.
                    get_vector_store(username, active_subject, force_rebuild=True)
                    st.rerun()
                except BridgeUnavailableError:
                    st.error("Delete failed — storage bridge is unreachable. Please try again shortly.")

    # Delete Entire Subject — higher blast radius than a chapter delete
    # (every chapter, file, and generated study item under it), so this
    # asks the person to type the subject name back rather than just
    # clicking a button, to guard against a misclick nuking an entire
    # subject by accident.
    with st.popover("🗑️ Delete Entire Subject", use_container_width=True):
        st.error(
            f"This permanently deletes **{active_subject}** — every chapter, uploaded file, "
            f"quiz, flashcard deck, and study guide under it. This cannot be undone."
        )
        typed_subject = st.text_input(
            f"Type \"{active_subject}\" to confirm:", key="confirm_delete_subject_text"
        )
        if st.button("Confirm delete subject", type="primary", key="confirm_delete_subject_btn"):
            if typed_subject.strip() != active_subject:
                st.error("That doesn't match the subject name. Nothing was deleted.")
            else:
                try:
                    delete_subject_remote(username, active_subject)
                    delete_subject_local_content(username, active_subject)
                    st.success(f"Deleted subject \"{active_subject}\".")
                    st.rerun()
                except BridgeUnavailableError:
                    st.error("Delete failed — storage bridge is unreachable. Please try again shortly.")

    return {
        "active_subject": active_subject,
        "active_chapter": active_chapter,
    }
