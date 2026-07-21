"""
core/analytics_store.py
=========================
The single source of truth for everything the Dashboard and Progress tabs
read: quiz attempt history, per-topic mastery scores, study session logs,
and streak data. Every feature that produces a gradeable or trackable
event (quiz submission, flashcard review, study session) should call into
this module so the analytics layer has one consistent place to read from.

Storage: ONE BLOB PER USER on the storage bridge (see core/bridge_client.py
and storage_bridge.py's /blobs/* routes), under the key "analytics".

CHANGED: this used to write one JSON file per user to local disk
(users/<username>/analytics.json). That worked fine on Bhishma's own
machine, but Streamlit Cloud's container filesystem is ephemeral --
wiped on every restart/redeploy -- so streak and Progress & Analytics
data was silently lost, often showing a 0/1-day streak even for
students who'd been studying for weeks. This mirrors the same fix
already applied to subjects/uploaded files: move it onto the bridge,
which persists on Bhishma's own machine indefinitely. The in-memory
schema and every public function's signature are unchanged, so nothing
calling into this module needs to change.

Schema (same as before, just stored remotely now):
{
  "quiz_attempts": [
      {
        "timestamp": "2026-06-19T10:00:00",
        "subject": "Biology",
        "chapter": "Cell Structure",
        "score": 7,
        "max_score": 10,
        "negative_marking": false,
        "topic_breakdown": {"Mitochondria": {"correct": 2, "total": 2}, ...}
      }, ...
  ],
  "study_sessions": [
      {"timestamp": "...", "subject": "Biology", "chapter": "Cell Structure", "duration_minutes": 25}
  ],
  "topic_mastery": {
      "Biology::Cell Structure::Mitochondria": {"correct": 5, "total": 6, "last_seen": "..."}
  },
  "struggle_log": {
      "Biology::Cell Structure": {"flagged_count": 3, "last_flagged": "..."}
  },
  "streak": {"current_streak_days": 4, "last_study_date": "2026-06-19", "longest_streak_days": 9}
}
"""
import json
from datetime import datetime, date, timedelta

from core import bridge_client

_BLOB_KEY = "analytics"


def _default_store() -> dict:
    return {
        "quiz_attempts": [],
        "study_sessions": [],
        "topic_mastery": {},
        "struggle_log": {},
        "streak": {"current_streak_days": 0, "last_study_date": None, "longest_streak_days": 0},
    }


def _load(username: str) -> dict:
    raw = bridge_client.blob_get(username, _BLOB_KEY)
    if raw is None:
        return _default_store()
    try:
        data = json.loads(raw)
        # Backfill any keys missing from an older schema version.
        defaults = _default_store()
        for key, val in defaults.items():
            data.setdefault(key, val)
        return data
    except (json.JSONDecodeError, TypeError):
        return _default_store()


def _save(username: str, data: dict) -> None:
    bridge_client.blob_set(username, _BLOB_KEY, json.dumps(data))


def _topic_key(subject: str, chapter: str, topic: str = None) -> str:
    return f"{subject}::{chapter}::{topic}" if topic else f"{subject}::{chapter}"


# ---------------------------------------------------------------------
# Quiz attempts & topic mastery
# ---------------------------------------------------------------------
def record_quiz_attempt(
    username: str,
    subject: str,
    chapter: str,
    score: float,
    max_score: float,
    negative_marking: bool,
    topic_breakdown: dict = None,
) -> None:
    """
    topic_breakdown: optional {topic_name: {"correct": float, "total": float}}
    Used to drive the per-topic mastery heatmap. If omitted, the attempt
    still counts toward chapter-level analytics but not topic-level ones.
    """
    data = _load(username)
    data["quiz_attempts"].append(
        {
            "timestamp": datetime.now().isoformat(),
            "subject": subject,
            "chapter": chapter,
            "score": score,
            "max_score": max_score,
            "negative_marking": negative_marking,
            "topic_breakdown": topic_breakdown or {},
        }
    )

    if topic_breakdown:
        for topic, result in topic_breakdown.items():
            key = _topic_key(subject, chapter, topic)
            entry = data["topic_mastery"].setdefault(key, {"correct": 0, "total": 0, "last_seen": None})
            entry["correct"] += result.get("correct", 0)
            entry["total"] += result.get("total", 0)
            entry["last_seen"] = datetime.now().isoformat()

            # A topic where the student is consistently weak gets flagged for revision.
            if entry["total"] > 0 and (entry["correct"] / entry["total"]) < 0.5:
                chap_key = _topic_key(subject, chapter)
                flag = data["struggle_log"].setdefault(chap_key, {"flagged_count": 0, "last_flagged": None})
                flag["flagged_count"] += 1
                flag["last_flagged"] = datetime.now().isoformat()

    _save(username, data)
    record_study_activity(username)  # any quiz attempt counts toward the daily streak


def get_topic_mastery(username: str) -> dict:
    """Returns the raw topic_mastery dict: {topic_key: {correct, total, last_seen}}."""
    return _load(username)["topic_mastery"]


def get_quiz_attempts(username: str, subject: str = None, chapter: str = None) -> list:
    attempts = _load(username)["quiz_attempts"]
    if subject:
        attempts = [a for a in attempts if a["subject"] == subject]
    if chapter:
        attempts = [a for a in attempts if a["chapter"] == chapter]
    return attempts


def get_weak_topics(username: str, threshold: float = 0.5, min_attempts: int = 2) -> list:
    """
    Returns topics where accuracy is below `threshold`, sorted weakest first.
    Requires at least `min_attempts` graded questions before flagging, so a
    single unlucky question doesn't trigger a false "weak topic" alarm.
    """
    mastery = get_topic_mastery(username)
    weak = []
    for key, stats in mastery.items():
        if stats["total"] < min_attempts:
            continue
        accuracy = stats["correct"] / stats["total"] if stats["total"] else 0
        if accuracy < threshold:
            subject, chapter, topic = (key.split("::") + [None, None, None])[:3]
            weak.append(
                {
                    "subject": subject,
                    "chapter": chapter,
                    "topic": topic,
                    "accuracy": round(accuracy, 2),
                    "total_attempts": stats["total"],
                }
            )
    weak.sort(key=lambda x: x["accuracy"])
    return weak


# ---------------------------------------------------------------------
# Study sessions & streaks
# ---------------------------------------------------------------------
def record_study_activity(username: str) -> None:
    """
    Marks that the user did *something* study-related today. Called
    automatically by quiz attempts, flashcard reviews, and material
    generation. Updates the daily streak counter.
    """
    data = _load(username)
    today = date.today().isoformat()
    streak = data["streak"]

    if streak["last_study_date"] == today:
        pass  # already counted today
    elif streak["last_study_date"] is None:
        streak["current_streak_days"] = 1
    else:
        last_date = date.fromisoformat(streak["last_study_date"])
        if date.today() - last_date == timedelta(days=1):
            streak["current_streak_days"] += 1
        elif date.today() - last_date > timedelta(days=1):
            streak["current_streak_days"] = 1  # streak broken, restart

    streak["last_study_date"] = today
    streak["longest_streak_days"] = max(streak["longest_streak_days"], streak["current_streak_days"])
    data["streak"] = streak
    _save(username, data)


def log_study_session(username: str, subject: str, chapter: str, duration_minutes: float) -> None:
    data = _load(username)
    data["study_sessions"].append(
        {
            "timestamp": datetime.now().isoformat(),
            "subject": subject,
            "chapter": chapter,
            "duration_minutes": duration_minutes,
        }
    )
    _save(username, data)
    record_study_activity(username)


def get_streak_info(username: str) -> dict:
    return _load(username)["streak"]


def get_total_study_minutes(username: str, subject: str = None) -> float:
    sessions = _load(username)["study_sessions"]
    if subject:
        sessions = [s for s in sessions if s["subject"] == subject]
    return sum(s["duration_minutes"] for s in sessions)


# ---------------------------------------------------------------------
# Struggle log (used by Study Chat Memory)
# ---------------------------------------------------------------------
def flag_struggle(username: str, subject: str, chapter: str) -> None:
    """Manually flag a chapter as struggled-with (e.g. from the Socratic tutor noticing repeated confusion)."""
    data = _load(username)
    key = _topic_key(subject, chapter)
    flag = data["struggle_log"].setdefault(key, {"flagged_count": 0, "last_flagged": None})
    flag["flagged_count"] += 1
    flag["last_flagged"] = datetime.now().isoformat()
    _save(username, data)


def get_struggle_log(username: str) -> dict:
    return _load(username)["struggle_log"]


# ---------------------------------------------------------------------
# Exam readiness
# ---------------------------------------------------------------------
def compute_readiness_score(username: str, subject: str = None) -> dict:
    """
    A simple, transparent readiness heuristic (not a black-box ML score):
    - 60% weight: average accuracy across recent quiz attempts (last 10)
    - 25% weight: topic coverage (fraction of attempted topics with >=2 attempts that are NOT weak)
    - 15% weight: consistency (current streak, capped at 14 days = full credit)

    Returns {"score": 0-100, "label": "...", "breakdown": {...}}.
    Returns score=None if there isn't enough data yet (avoids a misleadingly
    confident number from one or two quizzes).
    """
    attempts = get_quiz_attempts(username, subject=subject)
    if len(attempts) < 2:
        return {"score": None, "label": "Not enough data yet", "breakdown": {}}

    recent = attempts[-10:]
    accuracy_scores = [a["score"] / a["max_score"] for a in recent if a["max_score"] > 0]
    avg_accuracy = sum(accuracy_scores) / len(accuracy_scores) if accuracy_scores else 0

    mastery = get_topic_mastery(username)
    if subject:
        mastery = {k: v for k, v in mastery.items() if k.startswith(f"{subject}::")}
    qualifying = {k: v for k, v in mastery.items() if v["total"] >= 2}
    if qualifying:
        not_weak = sum(1 for v in qualifying.values() if (v["correct"] / v["total"]) >= 0.5)
        coverage_score = not_weak / len(qualifying)
    else:
        coverage_score = avg_accuracy  # fall back to accuracy if no topic-level data exists

    streak = get_streak_info(username)
    consistency_score = min(streak["current_streak_days"] / 14, 1.0)

    final_score = round((avg_accuracy * 0.60 + coverage_score * 0.25 + consistency_score * 0.15) * 100)

    if final_score >= 80:
        label = "Exam Ready"
    elif final_score >= 60:
        label = "Almost There"
    elif final_score >= 40:
        label = "Needs More Practice"
    else:
        label = "Early Stage"

    return {
        "score": final_score,
        "label": label,
        "breakdown": {
            "accuracy": round(avg_accuracy * 100),
            "topic_coverage": round(coverage_score * 100),
            "consistency": round(consistency_score * 100),
        },
    }
