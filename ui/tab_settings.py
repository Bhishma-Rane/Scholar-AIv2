"""
ui/tab_settings.py
=====================
The "Settings" tab: replay the tutorial (instead of it being its own
always-visible tab, since most people skipped it anyway), pick an accent
color (persisted per-account on the bridge, follows you everywhere), and
basic account info.
"""
import streamlit as st

from config import DEFAULT_THEME_COLOR
from core.bridge_client import (
    set_theme_color,
    verify_password,
    delete_account,
    BridgeUnavailableError,
    BridgeRequestError,
)
from core.paths import wipe_local_user_data
from core.onboarding_store import reset_tutorial
from ui.auth import logout as auth_logout


def _render_tutorial_section(username: str):
    st.subheader("📘 Tutorial")
    st.caption("Walk through ScholarAI's core features again, step by step.")
    if st.button("▶ Replay Tutorial"):
        reset_tutorial(username)
        st.session_state.tutorial_selected_pathway = None
        st.session_state.tutorial_step_index = 0
        st.session_state["show_tutorial_overlay"] = True
        st.rerun()


def _render_theme_section(username: str):
    st.subheader("🎨 Appearance")
    st.caption("Pick an accent color — it's saved to your account and follows you on any device.")

    current_color = st.session_state.get("theme_color", DEFAULT_THEME_COLOR)

    col1, col2 = st.columns([1, 3])
    with col1:
        picked_color = st.color_picker("Accent color", value=current_color, key="settings_color_picker")
    with col2:
        st.markdown(
            f"<div style='margin-top:28px;'>Preview: "
            f"<span style='background:{picked_color}; padding:4px 16px; border-radius:6px; color:white; font-weight:bold;'>Aa</span></div>",
            unsafe_allow_html=True,
        )

    presets = {
        "Olive Green (default)": "#5a691d",
        "Ocean Blue": "#1d5a69",
        "Plum": "#5a1d69",
        "Slate": "#3a3a45",
    }
    preset_cols = st.columns(len(presets))
    for col, (label, hex_val) in zip(preset_cols, presets.items()):
        with col:
            if st.button(label, key=f"preset_{hex_val}", use_container_width=True):
                picked_color = hex_val

    if st.button("Save Color", type="primary"):
        try:
            set_theme_color(username, picked_color)
            st.session_state["theme_color"] = picked_color
            st.success("Saved! Your new color is now active.")
            st.rerun()
        except BridgeRequestError as e:
            st.error(f"Couldn't save: {e.detail}")
        except BridgeUnavailableError:
            st.error("Can't reach the server right now — your color choice wasn't saved. Try again shortly.")


def _render_account_section(username: str):
    st.subheader("👤 Account")
    st.markdown(f"**Username:** {username}")


def _render_danger_zone(username: str):
    st.subheader("⚠️ Danger Zone")
    st.caption("Permanently delete your account and everything in it. This cannot be undone.")

    with st.expander("🗑️ Delete My Account", expanded=False):
        st.error(
            "This permanently deletes your account: every subject, uploaded file, quiz, "
            "flashcard deck, study guide, question paper, and your entire study history. "
            "**There is no way to recover this once it's done.**"
        )

        st.markdown("**Step 1 — confirm your password**")
        confirm_password = st.text_input(
            "Enter your current password:", type="password", key="wipe_confirm_password"
        )

        st.markdown(f"**Step 2 — type your username to confirm**")
        confirm_username = st.text_input(
            f"Type \"{username}\" exactly:", key="wipe_confirm_username"
        )

        ready = bool(confirm_password) and confirm_username.strip().lower() == username.strip().lower()

        st.markdown("**Step 3 — confirm the deletion**")
        if st.button(
            "🔥 Permanently Delete My Account",
            type="primary",
            disabled=not ready,
            key="wipe_final_confirm",
        ):
            # Re-verify the password ourselves before even attempting the
            # wipe, so a wrong password fails fast with a clear message
            # rather than surfacing as a generic bridge error. The bridge's
            # own /account/delete route re-checks the password again
            # server-side regardless -- this call is a UX nicety, not the
            # actual security boundary.
            try:
                if not verify_password(username, confirm_password):
                    st.error("Incorrect password. Nothing was deleted.")
                    return
            except BridgeUnavailableError:
                st.error("Can't reach the account server right now. Please try again in a moment.")
                return

            try:
                delete_account(username, confirm_password)
            except BridgeRequestError as e:
                st.error(f"Couldn't delete account: {e.detail}")
                return
            except BridgeUnavailableError:
                st.error("Can't reach the account server right now. Your account was NOT deleted. Please try again shortly.")
                return

            # Bridge-side data (credentials, subjects, files, papers, quiz
            # history) is gone. Now clear what's local to this container
            # (generated quizzes/flashcards/guides, analytics.json) --
            # see core/paths.py's wipe_local_user_data() docstring for why
            # these two calls are separate.
            wipe_local_user_data(username)

            st.success("Your account has been permanently deleted.")
            auth_logout()
            st.rerun()

        if not ready:
            st.caption("Fill in both fields above with matching, correct values to enable this button.")


def render_settings_tab(username: str):
    st.header("⚙️ Settings")

    _render_account_section(username)
    st.markdown("---")
    _render_theme_section(username)
    st.markdown("---")
    _render_tutorial_section(username)
    st.markdown("---")
    _render_danger_zone(username)
