"""
ui/tab_feedback.py
=====================
The "Feedback" tab: lets students report a problem, suggest an
improvement, or rate ScholarAI. Calls the bridge's /feedback/submit
endpoint. You (the developer) read submissions with view_feedback.py
from your terminal, or by calling /feedback/list directly.

Follows the same render_*_tab(username, ...) signature pattern as the
other tabs in ui/ (tab_dashboard.py, tab_chat.py, etc.) so it plugs into
app.py's existing st.tabs() dispatch without anything special.
"""
import streamlit as st
import requests

from config import BRIDGE_BASE_URL, BRIDGE_SHARED_SECRET


def _submit_feedback(username: str, kind: str, message: str = None, rating: int = None) -> dict:
    """
    Calls /feedback/submit. Returns {"success": True} or
    {"success": False, "error": "..."} -- never raises, so the tab can
    always show a clean message either way.
    """
    payload = {"username": username, "kind": kind}
    if message is not None:
        payload["message"] = message
    if rating is not None:
        payload["rating"] = rating

    try:
        resp = requests.post(
            f"{BRIDGE_BASE_URL}/feedback/submit",
            headers={"x-bridge-secret": BRIDGE_SHARED_SECRET},
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return {"success": True}
        try:
            detail = resp.json().get("detail", "Something went wrong.")
        except ValueError:
            detail = "Something went wrong."
        return {"success": False, "error": detail}
    except requests.RequestException as e:
        return {"success": False, "error": f"Could not reach the server: {e}"}


def render_feedback_tab(username: str):
    st.header("📝 Feedback")
    st.caption("Found a bug, have an idea, or just want to rate ScholarAI? Let us know below.")

    tab_bug, tab_suggestion, tab_rating = st.tabs(
        ["🐛 Report a problem", "💡 Suggest an improvement", "⭐ Rate ScholarAI"]
    )

    with tab_bug:
        st.write("Tell us what went wrong — the more detail, the faster it gets fixed.")
        bug_message = st.text_area(
            "What happened?",
            placeholder="e.g. The quiz page froze after I submitted question 4...",
            key="feedback_bug_message",
            height=120,
        )
        if st.button("Submit problem report", key="feedback_submit_bug"):
            if not bug_message.strip():
                st.error("Please describe the problem before submitting.")
            else:
                result = _submit_feedback(username, "bug", message=bug_message.strip())
                if result["success"]:
                    st.success("Thanks — your report has been sent.")
                else:
                    st.error(f"Couldn't submit: {result['error']}")

    with tab_suggestion:
        st.write("Have an idea that would make ScholarAI better? We're listening.")
        suggestion_message = st.text_area(
            "Your suggestion",
            placeholder="e.g. It would be great to have a dark mode...",
            key="feedback_suggestion_message",
            height=120,
        )
        if st.button("Submit suggestion", key="feedback_submit_suggestion"):
            if not suggestion_message.strip():
                st.error("Please write your suggestion before submitting.")
            else:
                result = _submit_feedback(username, "suggestion", message=suggestion_message.strip())
                if result["success"]:
                    st.success("Thanks for the suggestion!")
                else:
                    st.error(f"Couldn't submit: {result['error']}")

    with tab_rating:
        st.write("How would you rate your experience with ScholarAI so far?")
        rating = st.slider("Rating", min_value=1, max_value=5, value=5, key="feedback_rating_value")
        st.write("⭐" * rating + "☆" * (5 - rating))
        if st.button("Submit rating", key="feedback_submit_rating"):
            result = _submit_feedback(username, "rating", rating=rating)
            if result["success"]:
                st.success("Thanks for rating us!")
            else:
                st.error(f"Couldn't submit: {result['error']}")
