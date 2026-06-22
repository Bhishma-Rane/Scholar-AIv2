"""
ui/tab_progress.py
=====================
The "Progress" tab: visual analytics built entirely from core.analytics_store
data. Knowledge Heatmap (mastery by topic), Weak Topic Detection with
revision recommendations, Study Streaks, Learning Analytics (time spent,
accuracy trends), and a Predicted Exam Readiness Score.
"""
import pandas as pd
import streamlit as st

from core.analytics_store import (
    get_topic_mastery,
    get_quiz_attempts,
    get_weak_topics,
    get_streak_info,
    get_total_study_minutes,
    compute_readiness_score,
)


def _mastery_color(accuracy: float) -> str:
    """Green = mastered, yellow = improving, red = needs work."""
    if accuracy >= 0.8:
        return "#2e7d32"  # green
    elif accuracy >= 0.5:
        return "#f9a825"  # amber
    else:
        return "#c62828"  # red


def _render_heatmap(username: str, active_subject: str):
    st.subheader("🟩 Knowledge Heatmap")
    mastery = get_topic_mastery(username)
    if active_subject and active_subject != "Select Subject":
        mastery = {k: v for k, v in mastery.items() if k.startswith(f"{active_subject}::")}

    if not mastery:
        st.info("No quiz data yet. Take a few quizzes in the Assessment tab to populate your heatmap.")
        return

    rows = []
    for key, stats in mastery.items():
        parts = key.split("::")
        subject = parts[0] if len(parts) > 0 else ""
        chapter = parts[1] if len(parts) > 1 else ""
        topic = parts[2] if len(parts) > 2 else "(general)"
        accuracy = stats["correct"] / stats["total"] if stats["total"] else 0
        rows.append(
            {
                "Subject": subject,
                "Chapter": chapter,
                "Topic": topic,
                "Accuracy": round(accuracy * 100, 1),
                "Attempts": stats["total"],
            }
        )

    rows.sort(key=lambda r: r["Accuracy"])

    for row in rows:
        color = _mastery_color(row["Accuracy"] / 100)
        st.markdown(
            f"""
            <div style="display:flex; align-items:center; margin-bottom:6px;">
                <div style="width:14px; height:14px; border-radius:3px; background:{color}; margin-right:10px; flex-shrink:0;"></div>
                <div style="flex:1;"><b>{row['Topic']}</b> <span style="color:#888; font-size:12px;">({row['Chapter']})</span></div>
                <div style="width:80px; text-align:right; font-weight:bold;">{row['Accuracy']}%</div>
                <div style="width:90px; text-align:right; color:#888; font-size:12px;">{row['Attempts']} attempts</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_weak_topics(username: str, active_subject: str):
    st.subheader("⚠️ Weak Topic Detection")
    subject_filter = active_subject if active_subject and active_subject != "Select Subject" else None
    weak = get_weak_topics(username, threshold=0.6, min_attempts=2)
    if subject_filter:
        weak = [w for w in weak if w["subject"] == subject_filter]

    if not weak:
        st.success("No consistently weak topics detected yet — keep it up, or take more quizzes for a fuller picture.")
        return

    st.caption("Topics below 60% accuracy (with at least 2 attempts) — recommended for revision, weakest first.")
    for w in weak:
        st.markdown(
            f"- **{w['topic'] or w['chapter']}** ({w['chapter']}) — {int(w['accuracy']*100)}% accuracy "
            f"over {w['total_attempts']} attempts. *Recommended: revisit with flashcards or a focused quiz.*"
        )


def _render_streaks(username: str):
    st.subheader("🔥 Study Streaks")
    streak = get_streak_info(username)
    col1, col2 = st.columns(2)
    col1.metric("Current Streak", f"{streak['current_streak_days']} days")
    col2.metric("Longest Streak", f"{streak['longest_streak_days']} days")
    if streak["current_streak_days"] == 0:
        st.caption("Study something today to start a new streak!")


def _render_analytics(username: str, active_subject: str):
    st.subheader("📈 Learning Analytics")
    subject_filter = active_subject if active_subject and active_subject != "Select Subject" else None
    attempts = get_quiz_attempts(username, subject=subject_filter)
    total_minutes = get_total_study_minutes(username, subject=subject_filter)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Study Time", f"{total_minutes:.0f} min")
    col2.metric("Quizzes Taken", len(attempts))

    if attempts:
        accuracies = [a["score"] / a["max_score"] * 100 if a["max_score"] else 0 for a in attempts]
        avg_accuracy = sum(accuracies) / len(accuracies)
        col3.metric("Average Accuracy", f"{avg_accuracy:.0f}%")

        st.caption("Accuracy trend across your last attempts (left = earliest, right = most recent):")
        df = pd.DataFrame({"Attempt #": range(1, len(accuracies) + 1), "Accuracy (%)": accuracies})
        st.line_chart(df.set_index("Attempt #"))

        if len(accuracies) >= 4:
            first_half_avg = sum(accuracies[: len(accuracies) // 2]) / (len(accuracies) // 2)
            second_half_avg = sum(accuracies[len(accuracies) // 2 :]) / (len(accuracies) - len(accuracies) // 2)
            delta = second_half_avg - first_half_avg
            trend_word = "improving 📈" if delta > 3 else ("declining 📉" if delta < -3 else "steady ➡️")
            st.caption(f"Trend: {trend_word} ({delta:+.1f} percentage points, earlier half vs. later half)")
    else:
        col3.metric("Average Accuracy", "—")
        st.info("Take a quiz to start building your accuracy trend.")


def _render_readiness(username: str, active_subject: str):
    st.subheader("🎯 Predicted Exam Readiness")
    subject_filter = active_subject if active_subject and active_subject != "Select Subject" else None
    readiness = compute_readiness_score(username, subject=subject_filter)

    if readiness["score"] is None:
        st.info("Take at least 2 quizzes to unlock your readiness score.")
        return

    st.metric("Readiness Score", f"{readiness['score']} / 100", help=readiness["label"])
    st.progress(readiness["score"] / 100, text=readiness["label"])

    b = readiness["breakdown"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Accuracy", f"{b['accuracy']}%")
    c2.metric("Topic Coverage", f"{b['topic_coverage']}%")
    c3.metric("Consistency", f"{b['consistency']}%")
    st.caption(
        "This is a transparent heuristic (60% recent accuracy, 25% topic coverage, 15% study "
        "consistency) — not a guarantee, just a directional signal."
    )


def render_progress_tab(username: str, active_subject: str):
    st.header("📊 Progress & Analytics")

    _render_readiness(username, active_subject)
    st.markdown("---")
    _render_streaks(username)
    st.markdown("---")
    _render_heatmap(username, active_subject)
    st.markdown("---")
    _render_weak_topics(username, active_subject)
    st.markdown("---")
    _render_analytics(username, active_subject)
