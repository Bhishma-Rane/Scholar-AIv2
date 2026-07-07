"""
ui/tab_practice.py
=====================
The "Practice & Exams" tab: generate a digital quiz, take it (objective
+ AI-graded subjective, with negative marking), or take a formal,
section-based Question Paper (VSA/SA/LA/case-based/fill-blank/
assertion-reason, with Practice/Test mode and a timer in Test mode).

REPLACES the old split across three tabs:
  - Batch Gen's "Mixed Interactive Quiz (JSON)" mode -> now the
    "Generate Quiz" sub-tab here.
  - Batch Gen's "Custom Printable Mock Exam" mode -> REMOVED ENTIRELY.
    Question Paper is its replacement -- build_mock_exam_paper() in
    features/mock_exams.py is no longer called from anywhere; the
    function can stay in that file unused or be deleted, your call.
  - Assessment tab -> now the "Take Quiz" sub-tab here (unchanged logic).
  - Question Paper tab -> now the "Question Paper" sub-tab here. Users
    generate their own papers on demand from this tab now -- see
    ui/tab_question_paper.py -- rather than picking from an
    admin-published list, so it needs active_subject/active_chapter
    passed through in addition to username/target_language.
"""
import os
import json

import streamlit as st

from core.paths import get_chapter_paths
from core.analytics_store import record_quiz_attempt
from features.mock_exams import grade_full_quiz
from features.chat_graph import vedic_graph
from ui.tab_question_paper import render_question_paper_tab

_ASSESSMENT_CSS = """
<style>
div[data-testid="stSubheader"] p,
div[data-testid="stSubheader"],
div[data-testid="stSubheader"] h3 {
    font-size: 1.25rem !important;
    line-height: 1.5 !important;
    font-weight: 400 !important;
}
div[data-testid="stRadio"] label p {
    font-size: 1.15rem !important;
    line-height: 1.5 !important;
}
div[data-testid="stTextArea"] textarea {
    font-size: 1.15rem !important;
}
</style>
"""


def _inject_assessment_css():
    st.markdown(_ASSESSMENT_CSS, unsafe_allow_html=True)


def _render_generate_quiz_subtab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.caption("Generate a digital, auto-graded quiz from this chapter — then take it in the next sub-tab.")
    num_qs = st.number_input("Number of Questions", min_value=5, max_value=50, value=5, step=5)
    if st.button("🚀 Generate Digital Quiz", type="primary"):
        if active_chapter == "Select Chapter":
            st.error("Select Chapter.")
        else:
            with st.status("Building Perfect JSON Exam...", expanded=True) as status:
                res = vedic_graph.invoke(
                    {
                        "question": f"ver {active_chapter} ! quiz {num_qs}",
                        "chat_history": st.session_state.session_history,
                        "context": "",
                        "relevance_score": 0.0,
                        "source_file": "Unknown",
                        "command_mode": "standard",
                        "quiz_count": num_qs,
                        "response": "",
                        "active_chapter": active_chapter,
                        "data_source": "Local Docs",
                        "language": target_language,
                        "username": username,
                        "subject": active_subject,
                    }
                )
                status.update(label="Complete!", state="complete", expanded=False)
            if "Failed" not in res["response"]:
                st.success(res["response"])
                st.info("Switch to the 'Take Quiz' sub-tab to start it.")
            else:
                st.error(res["response"])


def _render_quiz_setup_screen(username: str, active_subject: str, active_chapter: str, data_file: str):
    if os.path.exists(data_file):
        st.session_state.negative_marking_enabled = st.toggle(
            "Enable Negative Marking",
            value=st.session_state.get("negative_marking_enabled", False),
            help="Deducts 25% of a question's marks for each wrong (but attempted) objective answer. "
            "Skipped questions are never penalized. Subjective answers are never penalized.",
        )
        if st.button("▶ Load Evaluation Engine", type="primary"):
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    quiz_data = json.load(f)
                st.session_state.update(
                    {
                        "quiz_data": quiz_data,
                        "user_answers": {i: None for i in range(len(quiz_data))},
                        "marked_review": {i: False for i in range(len(quiz_data))},
                        "current_q": 0,
                        "exam_submitted": False,
                        "quiz_active": True,
                        "ai_summary": "",
                        "final_score": 0,
                        "grading_result": None,
                    }
                )
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load JSON: {e}")
    else:
        st.info("No generated quiz found for this chapter. Use the 'Generate Quiz' sub-tab to create one.")


def _render_quiz_question_screen():
    _inject_assessment_css()

    q_idx = st.session_state.current_q
    quiz_data = st.session_state.quiz_data
    current_q = quiz_data[q_idx]

    st.progress((q_idx + 1) / len(quiz_data), text=f"Question {q_idx + 1} of {len(quiz_data)}")
    topic = current_q.get("topic")
    if topic:
        st.caption(f"Topic: {topic}")
    st.subheader(f"📍 Q{q_idx + 1}: {current_q.get('q', 'Error reading question')}")

    if current_q.get("type") == "objective":
        st.session_state.user_answers[q_idx] = st.radio(
            "Options:",
            current_q.get("options", []),
            index=None,
            key=f"q_{q_idx}",
        )
    else:
        st.session_state.user_answers[q_idx] = st.text_area("Answer:", key=f"q_{q_idx}")

    c1, c2, c3 = st.columns(3)
    if c1.button("⬅️ Previous", use_container_width=True) and q_idx > 0:
        st.session_state.current_q -= 1
        st.rerun()
    if c2.button("Next ➡️", use_container_width=True) and q_idx < len(quiz_data) - 1:
        st.session_state.current_q += 1
        st.rerun()
    if c3.button("✅ Submit Assessment", type="primary", use_container_width=True):
        st.session_state.exam_submitted = True
        st.rerun()


def _render_quiz_results_screen(username: str, active_subject: str, active_chapter: str, target_language: str):
    if st.session_state.grading_result is None:
        with st.spinner("Grading your answers... (subjective answers are AI-graded, this may take a moment)"):
            result = grade_full_quiz(
                st.session_state.quiz_data,
                st.session_state.user_answers,
                negative_marking=st.session_state.get("negative_marking_enabled", False),
                lang=target_language,
            )
            st.session_state.grading_result = result

            record_quiz_attempt(
                username=username,
                subject=active_subject,
                chapter=active_chapter,
                score=result["total_score"],
                max_score=result["max_score"],
                negative_marking=st.session_state.get("negative_marking_enabled", False),
                topic_breakdown=result["topic_breakdown"],
            )

    result = st.session_state.grading_result
    pct = (result["total_score"] / result["max_score"] * 100) if result["max_score"] else 0

    st.success("Exam Graded!")
    col1, col2 = st.columns(2)
    col1.metric("Score", f"{result['total_score']} / {result['max_score']}")
    col2.metric("Percentage", f"{pct:.1f}%")

    st.markdown("---")
    st.subheader("Question-by-Question Breakdown")
    for r in result["per_question"]:
        q = st.session_state.quiz_data[r["index"]]
        icon = "✅" if r["marks_earned"] == r["marks_possible"] else ("⚠️" if r["marks_earned"] > 0 else "❌")
        with st.expander(f"{icon} Q{r['index'] + 1}: {q.get('q', '')} — {r['marks_earned']}/{r['marks_possible']} marks"):
            st.markdown(f"**Your answer:** {st.session_state.user_answers.get(r['index']) or '*Skipped*'}")
            if q.get("type") != "objective":
                st.markdown(f"**Official answer:** {q.get('answer', '')}")
            if r.get("feedback"):
                st.markdown(f"**Feedback:** {r['feedback']}")

    if st.button("Restart"):
        st.session_state.quiz_active = False
        st.session_state.grading_result = None
        st.rerun()


def _render_take_quiz_subtab(username: str, active_subject: str, active_chapter: str, target_language: str):
    if active_chapter == "Select Chapter":
        st.warning("Please select a chapter.")
        return

    if "grading_result" not in st.session_state:
        st.session_state.grading_result = None

    paths = get_chapter_paths(username, active_subject, active_chapter)
    data_file = os.path.join(paths["mcq"], f"{active_chapter}_Data.json")

    if not st.session_state.quiz_active:
        _render_quiz_setup_screen(username, active_subject, active_chapter, data_file)
    elif not st.session_state.exam_submitted:
        _render_quiz_question_screen()
    else:
        _render_quiz_results_screen(username, active_subject, active_chapter, target_language)


def render_practice_tab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.header("📝 Practice & Exams")

    # NOTE: this used to be a second, NESTED st.tabs() inside the outer
    # st.tabs() in app.py. Streamlit's tab panels are always present in
    # the DOM (just hidden with CSS) rather than actually unmounted, and
    # calling st.rerun() from inside an INNER tab -- which both the
    # Generate Quiz and Question Paper sub-tabs do, to swap in a very
    # different widget tree (setup form -> success message, or setup
    # form -> question-taking screen) -- could desync the frontend's
    # bookkeeping of which panel belongs to which tab bar two levels
    # deep. Once desynced it stayed broken until a hard refresh, which
    # is what showed up as "all tabs stacked into one long scrollable
    # page." A plain st.radio styled as a tab bar sidesteps this
    # entirely: only the selected branch's content is ever rendered --
    # there's no hidden-panel DOM for anything to lose track of.
    sub_choice = st.radio(
        "Practice & Exams section:",
        ["⚡ Generate Quiz", "✏️ Take Quiz", "📋 Question Paper"],
        horizontal=True,
        label_visibility="collapsed",
        key="practice_sub_choice",
    )
    st.markdown("---")

    if sub_choice == "⚡ Generate Quiz":
        _render_generate_quiz_subtab(username, active_subject, active_chapter, target_language)
    elif sub_choice == "✏️ Take Quiz":
        _render_take_quiz_subtab(username, active_subject, active_chapter, target_language)
    else:
        render_question_paper_tab(username, active_subject, active_chapter, target_language)
