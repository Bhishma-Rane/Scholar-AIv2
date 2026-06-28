"""
ui/feedback_widget.py
========================
Feedback as a small popover (not a tab) -- addresses "too many tabs,
feedback should be a corner button." Call render_feedback_widget() once,
near the top of app.py (e.g. right after the brand header, or in the
sidebar) -- it renders a small button that opens a popover with the
same three actions as before (report a problem / suggest / rate),
including an optional free-text box on the rating action.

Replaces ui/tab_feedback.py -- remove that tab from app.py's st.tabs()
list and the corresponding "with tab_feedback:" block, then call
render_feedback_widget(username) instead, anywhere outside the tabs.
"""
import streamlit as st
import requests

from config import BRIDGE_BASE_URL, BRIDGE_SHARED_SECRET


def _submit_feedback(username: str, kind: str, message: str = None, rating: int = None) -> dict:
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


def render_feedback_widget(username: str):
    """
    Renders a small '💬 Feedback' button that opens a popover on click.
    Call this once, OUTSIDE your st.tabs() block -- a good spot is right
    after render_brand_header() in app.py, or inside render_sidebar().
    """
    with st.popover("💬 Feedback", use_container_width=False):
        st.caption("Report a problem, suggest something, or rate ScholarAI.")

        kind = st.radio(
            "What's this about?",
            ["Report a problem", "Suggestion", "Rating"],
            key="fb_widget_kind",
            horizontal=True,
        )

        if kind == "Report a problem":
            message = st.text_area("What happened?", key="fb_widget_bug_msg", height=90)
            if st.button("Submit", key="fb_widget_submit_bug"):
                if not message.strip():
                    st.error("Please describe the problem first.")
                else:
                    result = _submit_feedback(username, "bug", message=message.strip())
                    st.success("Thanks — sent.") if result["success"] else st.error(result["error"])

        elif kind == "Suggestion":
            message = st.text_area("Your idea:", key="fb_widget_suggestion_msg", height=90)
            if st.button("Submit", key="fb_widget_submit_suggestion"):
                if not message.strip():
                    st.error("Please write your suggestion first.")
                else:
                    result = _submit_feedback(username, "suggestion", message=message.strip())
                    st.success("Thanks for the idea!") if result["success"] else st.error(result["error"])

        else:  # Rating
            rating = st.slider("Rating", 1, 5, 5, key="fb_widget_rating")
            st.write("⭐" * rating + "☆" * (5 - rating))
            # Optional comment alongside the rating -- addresses "there
            # should also be a box where we can type things, optional."
            comment = st.text_input(
                "Anything you'd like to add? (optional)", key="fb_widget_rating_comment"
            )
            if st.button("Submit", key="fb_widget_submit_rating"):
                # The rating itself goes in `rating`; an optional comment
                # rides along as the message field on the SAME submission
                # (kind stays "rating" -- the bridge's /feedback/submit
                # already accepts rating + message together, no schema
                # change needed).
                result = _submit_feedback(
                    username, "rating", rating=rating,
                    message=comment.strip() if comment.strip() else None,
                )
                st.success("Thanks for rating us!") if result["success"] else st.error(result["error"])
