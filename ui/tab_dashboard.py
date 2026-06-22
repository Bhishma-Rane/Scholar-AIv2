"""
ui/tab_dashboard.py
======================
The "Dashboard" tab: an AI-narrated progress report — what's mastered,
what needs work, recommended next steps, and overall momentum — built
from real analytics data (core.analytics_store via features.dashboard_ai),
not invented by the LLM from nothing.
"""
import streamlit as st

from features.dashboard_ai import generate_dashboard_summary


def render_dashboard_tab(username: str, active_subject: str, target_language: str):
    st.header("🤖 AI Dashboard")
    st.caption("Your personal coach, reading directly from your real quiz history.")

    scope_label = active_subject if active_subject and active_subject != "Select Subject" else "All Subjects"
    st.markdown(f"**Scope:** {scope_label}")

    if st.button("🔄 Refresh My Progress Report", type="primary"):
        st.session_state.pop("dashboard_cache", None)

    if "dashboard_cache" not in st.session_state:
        with st.spinner("Reviewing your study history..."):
            subject_filter = active_subject if active_subject and active_subject != "Select Subject" else None
            st.session_state.dashboard_cache = generate_dashboard_summary(
                username, subject=subject_filter, language=target_language
            )

    result = st.session_state.dashboard_cache
    if isinstance(result, dict):
        summary = result.get("summary_markdown", "⚠️ 'summary_markdown' key not found in result.")
        st.markdown(summary)

        if result.get("has_data"):
            with st.expander("📋 View raw underlying data"):
                st.json(result.get("raw_data"))
        else:
            st.session_state.pop("dashboard_cache", None)
    else:
        st.error("Error: Expected 'result' to be a dictionary.")
        st.write("Actual result content:", result)
        st.session_state.pop("dashboard_cache", None)