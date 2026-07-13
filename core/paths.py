"""
core/paths.py
=============
Dynamic, per-user / per-subject / per-chapter path management.

CHANGED: subjects and uploaded source PDFs/TXT files are no longer
created/stored on local disk — they live on the storage bridge (see
core/bridge_client.py), since Streamlit Cloud's local filesystem doesn't
survive container restarts.

What STAYS local (and is still managed by this file, under USERS_DIR):
  - ChromaDB ("chroma_db") — rebuilt fresh from bridge-fetched PDFs on
    each cold start (see core/vectorstore.py). Not worth persisting the
    DB itself; re-embedding is simpler and the cost is one-time per
    container lifetime, not per-request.
  - Generated study content (quizzes, mock exams, study guides,
    flashcards) — NOTE: this is still on local disk for now and will
    still be lost on a container restart. This is a known follow-up,
    not yet covered by the bridge. Tracked separately from the
    subjects/PDFs fix requested here.

Every other module that needs a folder on disk should go through here
so the directory layout stays consistent and is defined in exactly one
place.
"""
import os
import re
from config import USERS_DIR
from core import bridge_client


def sanitize_filename(name: str) -> str:
    """Fixes Issue #30: Prevent weird directory names."""
    clean = re.sub(r'[^a-zA-Z0-9_\- ]', '', name).strip()
    return clean if clean else "unnamed"


def get_user_paths(username: str, subject: str = None) -> dict:
    """
    Returns a dict of canonical LOCAL folders for a user, optionally
    scoped to a subject. These are scratch/working directories on
    Streamlit Cloud's container disk — used for ChromaDB and generated
    study content, NOT for subjects/source PDFs (those live on the
    bridge now; see bridge_client.list_subjects / list_files / etc.).
    """
    safe_user = sanitize_filename(username).lower()
    user_root = os.path.join(USERS_DIR, safe_user)
    paths = {
        "root": user_root,
        "study": os.path.join(user_root, "study"),
        "chroma": os.path.join(user_root, "chroma_db"),
    }
    if subject:
        safe_subject = sanitize_filename(subject)
        paths["subject_study"] = os.path.join(paths["study"], safe_subject)
        os.makedirs(paths["subject_study"], exist_ok=True)

    os.makedirs(paths["root"], exist_ok=True)
    os.makedirs(paths["study"], exist_ok=True)
    return paths


def get_chapter_paths(username: str, subject: str, chapter_name: str) -> dict:
    """
    Returns canonical sub-folders for a single chapter's generated content
    (quizzes, mock exams, study guides, flashcards). These remain LOCAL
    for now (see module docstring) — a future pass should move these to
    the bridge too, the same way subjects/PDFs were just moved.
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


# ---------------------------------------------------------------------
# Bridge-backed subject/file helpers
# ---------------------------------------------------------------------
# These thin wrappers exist so callers (ui/sidebar.py, core/vectorstore.py)
# go through core.paths consistently rather than importing bridge_client
# directly everywhere — keeping the "is this local or remote?" decision
# centralized in this one file, matching the module's original purpose.

def list_subjects(username: str) -> list:
    """Returns the user's subject names, fetched from the bridge."""
    return bridge_client.list_subjects(username)


def create_subject(username: str, subject: str) -> None:
    """Registers a new subject on the bridge."""
    bridge_client.create_subject(username, subject)


def delete_subject_remote(username: str, subject: str) -> None:
    """Deletes a subject and all its files from the bridge."""
    bridge_client.delete_subject(username, subject)


def list_subject_files(username: str, subject: str) -> list:
    """Returns filenames (with extension) for a subject, fetched from the bridge."""
    return bridge_client.list_files(username, subject)


def upload_subject_file(username: str, subject: str, filename: str, file_bytes: bytes) -> str:
    """Uploads a PDF/TXT's raw bytes to the bridge. Returns the stored filename."""
    return bridge_client.upload_file(username, subject, filename, file_bytes)


def download_subject_file(username: str, subject: str, filename: str) -> bytes:
    """Fetches a single file's raw bytes from the bridge (used when
    rebuilding ChromaDB locally, or when a chapter's raw text is needed)."""
    return bridge_client.download_file(username, subject, filename)


def delete_subject_file(username: str, subject: str, filename: str) -> None:
    """Deletes a single file from the bridge."""
    bridge_client.delete_file(username, subject, filename)


# ---------------------------------------------------------------------
# Local cleanup helpers (chapter/subject/account deletes)
# ---------------------------------------------------------------------
# Generated study content (quizzes, mock exams, guides, flashcards) still
# lives on local disk per the module docstring above, so every delete
# path that removes a subject/chapter/account on the bridge must ALSO
# clear the matching local folder here, or orphaned generated content
# keeps showing up after the "source of truth" (the bridge) has already
# forgotten the subject/chapter/account ever existed.

def delete_chapter_local_content(username: str, subject: str, chapter: str) -> None:
    """Removes a single chapter's locally-generated content (quizzes,
    mock exams, study guides, flashcards). Safe to call even if nothing
    was ever generated for this chapter."""
    import shutil
    paths = get_user_paths(username, subject)
    safe_chapter = sanitize_filename(chapter)
    chapter_folder = os.path.join(paths["subject_study"], safe_chapter)
    if os.path.exists(chapter_folder):
        shutil.rmtree(chapter_folder)


def delete_subject_local_content(username: str, subject: str) -> None:
    """Removes ALL locally-generated content for every chapter under a
    subject (used when the whole subject is deleted, not just one
    chapter). Does not touch the bridge -- call delete_subject_remote()
    separately for that."""
    import shutil
    paths = get_user_paths(username, subject)
    if os.path.exists(paths["subject_study"]):
        shutil.rmtree(paths["subject_study"])


def wipe_local_user_data(username: str) -> None:
    """Removes EVERYTHING local for this user: generated study content
    for every subject, analytics.json (quiz history/streaks), and the
    local ChromaDB. Used by full account deletion. Does not touch the
    bridge (credentials, subjects, uploaded files, question papers,
    login tokens) -- call bridge_client.delete_account() separately for
    that, since those live on the bridge, not here."""
    import shutil
    safe_user = sanitize_filename(username).lower()
    user_root = os.path.join(USERS_DIR, safe_user)
    if os.path.exists(user_root):
        shutil.rmtree(user_root)
