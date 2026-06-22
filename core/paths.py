"""
core/paths.py
=============
Dynamic, per-user / per-subject / per-chapter path management.
Every other module that needs a folder on disk should go through here
so the directory layout stays consistent and is defined in exactly one place.
"""
import os
import re

from config import USERS_DIR


def sanitize_filename(name: str) -> str:
    """Fixes Issue #30: Prevent weird directory names."""
    clean = re.sub(r'[^a-zA-Z0-9_\- ]', '', name).strip()
    return clean if clean else "unnamed"


def get_user_paths(username: str, subject: str = None) -> dict:
    """
    Returns a dict of canonical folders for a user, optionally scoped
    to a subject. Folders are created as needed.
    """
    safe_user = sanitize_filename(username).lower()
    user_root = os.path.join(USERS_DIR, safe_user)

    paths = {
        "root": user_root,
        "sources": os.path.join(user_root, "source_materials"),
        "study": os.path.join(user_root, "study"),
        "chroma": os.path.join(user_root, "chroma_db"),
    }

    if subject:
        safe_subject = sanitize_filename(subject)
        paths["subject_source"] = os.path.join(paths["sources"], safe_subject)
        paths["subject_study"] = os.path.join(paths["study"], safe_subject)
        os.makedirs(paths["subject_source"], exist_ok=True)
        os.makedirs(paths["subject_study"], exist_ok=True)

    for p in paths.values():
        if "subject" not in p and p != paths.get("chroma"):
            os.makedirs(p, exist_ok=True)

    return paths


def get_chapter_paths(username: str, subject: str, chapter_name: str) -> dict:
    """
    Returns canonical sub-folders for a single chapter's generated content
    (quizzes, mock exams, study guides, flashcards).
    """
    paths = get_user_paths(username, subject)
    safe_name = sanitize_filename(chapter_name)

    chap_paths = {
        "mcq": os.path.join(paths["subject_study"], safe_name, "interactive_quizzes"),
        "mock": os.path.join(paths["subject_study"], safe_name, "mock_exams"),
        "guides": os.path.join(paths["subject_study"], safe_name, "study_guides"),
        "flashcards": os.path.join(paths["subject_study"], safe_name, "flashcards"),
    }
    for p in chap_paths.values():
        os.makedirs(p, exist_ok=True)
    return chap_paths
