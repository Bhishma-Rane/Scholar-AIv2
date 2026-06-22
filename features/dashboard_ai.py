"""
features/dashboard_ai.py
===========================
Generates a natural-language AI summary of the student's progress:
what they've mastered, what needs improvement, and what to focus on next
— by feeding the (already-computed, factual) analytics data into the LLM
and asking it to narrate it, rather than asking the LLM to invent
judgments from nothing. The LLM's job here is explanation, not analysis;
the actual numbers come entirely from core.analytics_store.
"""
import json

from core.llm import get_llm
from core.analytics_store import (
    get_topic_mastery,
    get_weak_topics,
    get_quiz_attempts,
    get_streak_info,
    compute_readiness_score,
)


def _build_data_summary(username: str, subject: str = None) -> dict:
    """Assembles the raw factual data the AI will narrate. No LLM calls here — pure data."""
    mastery = get_topic_mastery(username)
    if subject:
        mastery = {k: v for k, v in mastery.items() if k.startswith(f"{subject}::")}

    mastered = []
    needs_work = []
    for key, stats in mastery.items():
        if stats["total"] < 2:
            continue
        accuracy = stats["correct"] / stats["total"]
        topic_label = key.split("::")[-1]
        if accuracy >= 0.8:
            mastered.append({"topic": topic_label, "accuracy": round(accuracy * 100)})
        elif accuracy < 0.5:
            needs_work.append({"topic": topic_label, "accuracy": round(accuracy * 100)})

    weak_topics = get_weak_topics(username, threshold=0.6, min_attempts=2)
    if subject:
        weak_topics = [w for w in weak_topics if w["subject"] == subject]

    attempts = get_quiz_attempts(username, subject=subject)
    streak = get_streak_info(username)
    readiness = compute_readiness_score(username, subject=subject)

    return {
        "mastered_topics": mastered,
        "needs_work_topics": needs_work,
        "weak_topics": weak_topics[:5],
        "total_quizzes_taken": len(attempts),
        "current_streak_days": streak["current_streak_days"],
        "readiness_score": readiness["score"],
        "readiness_label": readiness["label"],
    }


def generate_dashboard_summary(username: str, subject: str = None, language: str = "English") -> dict:
    """
    Returns {"has_data": bool, "summary_markdown": str, "raw_data": dict}.

    If there isn't enough quiz history yet, has_data is False and
    summary_markdown explains that plainly instead of asking the LLM to
    fabricate an assessment from nothing.
    """
    data = _build_data_summary(username, subject)

    if data["total_quizzes_taken"] < 1:
        return {
            "has_data": False,
            "summary_markdown": (
                "No quiz data yet! Take a quiz in the Assessment tab and this dashboard will tell you "
                "exactly what you've mastered and what needs work."
            ),
            "raw_data": data,
        }

    prompt = f"""You are an encouraging but honest academic coach. Respond entirely in {language}.
Here is the student's real, factual performance data (DO NOT invent any numbers beyond what's given):

{json.dumps(data, indent=2)}

TASK: Write a short, warm, motivating progress report using ONLY this data. Use EXACTLY this
Markdown structure:

### ✅ What You've Perfected
(List mastered topics by name with their accuracy. If none yet, say so encouragingly.)

### 📈 What Needs Improvement
(List weak/needs-work topics by name with their accuracy. Be specific and constructive, not harsh.)

### 🎯 Recommended Next Steps
(2-3 concrete, specific actions, e.g. "Review X with flashcards", "Take a focused quiz on Y".)

### 💪 Overall Momentum
(One or two sentences on their streak/consistency and readiness score, framed encouragingly.)

Keep the whole thing concise — this should be skimmable in under a minute. Do not use placeholder
data; if a section has nothing to report, say so plainly rather than making something up.
"""
    try:
        llm = get_llm()
        if not llm:
            return {
                "has_data": True,
                "summary_markdown": "AI summary unavailable: LLM engine offline. Raw data is still shown below.",
                "raw_data": data,
            }
        summary = llm.invoke(prompt).content
        return {"has_data": True, "summary_markdown": summary, "raw_data": data}
    except Exception as e:
        return {
            "has_data": True,
            "summary_markdown": f"AI summary generation failed ({str(e)}). Raw data is still shown below.",
            "raw_data": data,
        }
