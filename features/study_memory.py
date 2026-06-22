"""
features/study_memory.py
===========================
"Study Chat Memory" — surfaces what the student has historically struggled
with (from core.analytics_store's weak-topic data) so the tutor chat can
be reminded of it, e.g. to proactively reference past trouble spots or
explain things with extra care around them. This is the bridge between
the analytics data layer and the conversational tutor.
"""
from core.analytics_store import get_weak_topics, get_struggle_log


def build_memory_context(username: str, subject: str = None, max_topics: int = 5) -> str:
    """
    Returns a short text blurb summarizing the student's known weak spots,
    suitable for prepending to an LLM prompt as background context. Returns
    an empty string if there isn't enough data yet, so callers can safely
    always call this without checking first.
    """
    weak_topics = get_weak_topics(username, threshold=0.6, min_attempts=2)
    if subject:
        weak_topics = [t for t in weak_topics if t["subject"] == subject]
    weak_topics = weak_topics[:max_topics]

    if not weak_topics:
        return ""

    topic_lines = []
    for t in weak_topics:
        label = t["topic"] or t["chapter"]
        topic_lines.append(f"- {label} (accuracy: {int(t['accuracy'] * 100)}% over {t['total_attempts']} attempts)")

    return (
        "[Student context: this student has historically struggled with the following topics. "
        "Be extra clear and patient when these come up, and consider gently checking their "
        "understanding rather than assuming mastery:\n"
        + "\n".join(topic_lines)
        + "]"
    )


def get_flagged_chapters(username: str, min_flags: int = 2) -> list:
    """
    Returns chapters that have been flagged as struggled-with at least
    `min_flags` times, sorted most-flagged first. Used to recommend
    revision targets.
    """
    struggle_log = get_struggle_log(username)
    flagged = [
        {"chapter_key": key, "flagged_count": data["flagged_count"], "last_flagged": data["last_flagged"]}
        for key, data in struggle_log.items()
        if data["flagged_count"] >= min_flags
    ]
    flagged.sort(key=lambda x: x["flagged_count"], reverse=True)
    return flagged
