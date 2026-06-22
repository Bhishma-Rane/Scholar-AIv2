"""
core/onboarding_store.py
===========================
Tracks whether a given user has completed (or skipped) the first-run
tutorial, so it's shown automatically once per account rather than on
every login. Stored as a tiny flag file per user — deliberately separate
from analytics.json since this is UI-state, not study data.
"""
import os
import json

from core.paths import get_user_paths

FLAG_FILENAME = "onboarding.json"


def _flag_path(username: str) -> str:
    paths = get_user_paths(username)
    return os.path.join(paths["root"], FLAG_FILENAME)


def has_completed_tutorial(username: str) -> bool:
    path = _flag_path(username)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("completed", False))
    except (json.JSONDecodeError, OSError):
        return False


def mark_tutorial_complete(username: str) -> None:
    path = _flag_path(username)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"completed": True}, f)


def reset_tutorial(username: str) -> None:
    """Used by the sidebar 'Replay Tutorial' button."""
    path = _flag_path(username)
    if os.path.exists(path):
        os.remove(path)
