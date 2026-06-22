"""
ui/tab_assessment.py
======================
The "Assessment" tab: loads a previously-generated JSON quiz, walks the
user through it one question at a time, and — on submit — actually grades
it: objective questions by exact match, subjective/short-answer questions
by AI comparison against the answer key (with partial credit). Supports
an optional negative-marking toggle. Results feed into the analytics store
so the Dashboard and Progress tabs have real data to show.
"""
import os
import json

import streamlit as st

from core.paths import get_chapter_paths
from core.analytics_store import record_quiz_attempt
from features.mock_exams import grade_full_quiz


def _render_setup_screen(username: str, active_subject: str, active_chapter: str, data_file: str):
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
        st.info("No generated quiz found for this chapter. Use Batch Gen to create one.")


def _render_question_screen():
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


def _render_results_screen(username: str, active_subject: str, active_chapter: str, target_language: str):
    if st.session_state.grading_result is None:
        with st.spinner("Grading your answers... (subjective answers are AI-graded, this may take a moment)"):
            result = grade_full_quiz(
                st.session_state.quiz_data,
                st.session_state.user_answers,
                negative_marking=st.session_state.get("negative_marking_enabled", False),
                lang=target_language,
            )
            st.session_state.grading_result = result

            # Persist to the analytics store so Dashboard/Progress have real data.
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


def render_assessment_tab(username: str, active_subject: str, active_chapter: str, target_language: str = "English"):
    st.header("Interactive Assessment Matrix")

    if active_chapter == "Select Chapter":
        st.warning("Please select a chapter.")
        return

    if "grading_result" not in st.session_state:
        st.session_state.grading_result = None

    paths = get_chapter_paths(username, active_subject, active_chapter)
    data_file = os.path.join(paths["mcq"], f"{active_chapter}_Data.json")

    if not st.session_state.quiz_active:
        _render_setup_screen(username, active_subject, active_chapter, data_file)
    elif not st.session_state.exam_submitted:
        _render_question_screen()
    else:
        _render_results_screen(username, active_subject, active_chapter, target_language)
