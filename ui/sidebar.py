"""
ui/sidebar.py
=============
Renders the sidebar: profile/logout, focus timer, data source, and
language. Subject/chapter workspace management (create/upload/delete)
moved to ui/tab_workspace.py so it's a guided first tab instead of a
cramped sidebar section.

Returns a small dict of selections (data_source, target_language) that
the rest of the UI needs.
"""

import base64

import streamlit as st

from config import DATA_SOURCES, LANGUAGES
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


def render_sidebar(username: str) -> dict:
    with st.sidebar:
        st.title("🎓 ScholarAI")
        st.caption("*Learn. Understand. Master.*")
        st.caption(f"👤 Profile: **{username.capitalize()}**")

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
        data_source = st.radio("Data Source:", DATA_SOURCES)
        target_language = st.selectbox("Target Language:", LANGUAGES)

        return {
            "data_source": data_source,
            "target_language": target_language,
        }
