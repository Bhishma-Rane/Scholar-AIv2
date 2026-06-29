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
from core.bridge_client import set_theme_color, BridgeUnavailableError, BridgeRequestError
from core.onboarding_store import reset_tutorial


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


def render_settings_tab(username: str):
    st.header("⚙️ Settings")

    _render_account_section(username)
    st.markdown("---")
    _render_theme_section(username)
    st.markdown("---")
    _render_tutorial_section(username)
