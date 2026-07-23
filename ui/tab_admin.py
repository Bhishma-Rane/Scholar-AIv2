"""
ui/tab_admin.py
================
The "🔐 Admin" tab — visible only to config.ADMIN_USERNAME. Lets Bhishma
browse and clear any student's chat history, per subject/chapter. This is
the in-app counterpart to admin_gui.py (the separate desktop tool); that
one manages accounts/subscriptions/feedback/papers directly against the
DB file, this one goes through the same bridge_client/content_store calls
every student's own session uses, just pointed at a chosen username
instead of st.session_state.logged_in_user.

ACCESS CONTROL: nothing here is gated on the bridge side (the bridge only
checks the shared secret, same as every other route in storage_bridge.py)
-- the actual boundary is that app.py only ever mounts this tab when
username == config.ADMIN_USERNAME, so a regular student never sees it or
gets a UI path that calls these functions with someone else's username.
Keep it that way: don't call anything in this file from a code path a
non-admin user can reach.
"""
import streamlit as st

from config import ADMIN_USERNAME
from core.paths import list_subjects, list_subject_files
from core.bridge_client import list_all_users, BridgeUnavailableError
from core import content_store


def _chapters_for(username: str, subject: str) -> list:
    try:
        files = list_subject_files(username, subject)
    except BridgeUnavailableError:
        return []
    return sorted({f.rsplit(".", 1)[0] for f in files if f.endswith((".txt", ".pdf"))})


def _render_readonly_message(msg: dict):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("image_results"):
            cols = st.columns(len(msg["image_results"]))
            for col, img in zip(cols, msg["image_results"]):
                with col:
                    st.image(img["image_url"], caption=img.get("title", ""), use_container_width=True)


def render_admin_tab():
    st.header("🔐 Admin — Student Chats")
    st.caption(
        "Only visible to the admin account. Browse any student's Tutor chat "
        "by subject/chapter, and clear it if needed."
    )

    try:
        all_users = list_all_users()
    except BridgeUnavailableError:
        st.error("Could not reach the storage bridge — can't load the student list right now.")
        return

    students = sorted(u["username"] for u in all_users if u["username"] != ADMIN_USERNAME)
    if not students:
        st.info("No student accounts yet.")
        return

    selected_student = st.selectbox("Student", students, key="admin_selected_student")

    try:
        subjects = list_subjects(selected_student)
    except BridgeUnavailableError:
        st.error("Could not reach the storage bridge — can't load this student's subjects.")
        return

    if not subjects:
        st.info(f"**{selected_student}** hasn't created any subjects yet.")
        return

    selected_subject = st.selectbox("Subject", subjects, key="admin_selected_subject")
    chapters = _chapters_for(selected_student, selected_subject)

    if not chapters:
        st.info(f"No chapters uploaded yet under **{selected_subject}**.")
        return

    selected_chapter = st.selectbox("Chapter", chapters, key="admin_selected_chapter")

    st.markdown("---")

    messages = content_store.load_chat_messages(selected_student, selected_subject, selected_chapter)
    if not messages:
        st.caption(f"No chat history for **{selected_student}** in this chapter yet.")
    else:
        for msg in messages:
            _render_readonly_message(msg)

    st.markdown("---")
    st.subheader("⚠️ Danger zone")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            f"🗑️ Clear chat — {selected_chapter}",
            use_container_width=True,
            key="admin_clear_chapter_chat",
        ):
            content_store.clear_chat_messages(selected_student, selected_subject, selected_chapter)
            st.success(f"Cleared {selected_student}'s chat for {selected_subject} / {selected_chapter}.")
            st.rerun()

    with col2:
        with st.popover(f"🗑️ Clear ALL chats in {selected_subject}", use_container_width=True):
            st.warning(
                f"This clears **{selected_student}**'s chat history for every chapter under "
                f"**{selected_subject}** — not just {selected_chapter}. This can't be undone."
            )
            if st.button(
                "Confirm clear all chats for this subject",
                type="primary",
                key="admin_confirm_clear_subject_chats",
            ):
                content_store.clear_all_chats_for_subject(selected_student, selected_subject)
                st.success(f"Cleared all chats for {selected_student} in {selected_subject}.")
                st.rerun()
