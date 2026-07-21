"""
core/paths.py
=============
Dynamic, per-user / per-subject / per-chapter path management.

CHANGED: subjects and uploaded source PDFs/TXT files are no longer
created/stored on local disk — they live on the storage bridge (see
core/bridge_client.py), since Streamlit Cloud's local filesystem doesn't
survive container restarts.

CHANGED (follow-up): generated study content (quizzes, mock exams,
study guides, flashcards) and analytics/streak data are now ALSO
bridge-backed -- see core/content_store.py and core/analytics_store.py.
They no longer live under the local folders this file hands out; this
module's cleanup helpers below purge the bridge-side data too, so
deleting a chapter/subject/account doesn't leave orphaned blobs behind.

What STAYS local (and is still managed by this file, under USERS_DIR):
  - ChromaDB ("chroma_db") — rebuilt fresh from bridge-fetched PDFs on
    each cold start (see core/vectorstore.py). Not worth persisting the
    DB itself; re-embedding is simpler and the cost is one-time per
    container lifetime, not per-request.

Every other module that needs a folder on disk should go through here
so the directory layout stays consistent and is defined in exactly one
place.
"""
import os
import re
from config import USERS_DIR
from core import bridge_client
from core import content_store


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
# Cleanup helpers (chapter/subject/account deletes)
# ---------------------------------------------------------------------
# Generated content and analytics/streak data live on the bridge now
# (see core/content_store.py, core/analytics_store.py), not on local
# disk -- so every delete path that removes a subject/chapter/account
# on the bridge's subjects/files tables must ALSO purge the matching
# content_store blobs here, or orphaned generated content and stale
# analytics keep showing up after the "source of truth" has already
# forgotten the subject/chapter/account ever existed. The local
# ChromaDB folder is still cleaned up here too since that stays local.

def delete_chapter_local_content(username: str, subject: str, chapter: str) -> None:
    """Removes a single chapter's generated content (quizzes, mock exams,
    study guides, flashcards) from the bridge. Safe to call even if
    nothing was ever generated for this chapter."""
    content_store.delete_chapter_content(username, subject, chapter)


def delete_subject_local_content(username: str, subject: str) -> None:
    """Removes ALL generated content for every chapter under a subject
    from the bridge (used when the whole subject is deleted, not just
    one chapter). Does not touch the bridge's subjects/files tables --
    call delete_subject_remote() separately for that."""
    content_store.delete_subject_content(username, subject)


def wipe_local_user_data(username: str) -> None:
    """Removes EVERYTHING for this user that isn't already covered by
    bridge_client.delete_account(): all generated study content and
    analytics/streak data (both bridge-backed blobs), plus the local
    ChromaDB folder. Used by full account deletion. Does not touch
    credentials, subjects, uploaded files, question papers, or login
    tokens -- call bridge_client.delete_account() separately for those."""
    import shutil
    bridge_client.blob_delete_prefix(username, "content:")
    bridge_client.blob_delete(username, "analytics")
    safe_user = sanitize_filename(username).lower()
    user_root = os.path.join(USERS_DIR, safe_user)
    if os.path.exists(user_root):
        shutil.rmtree(user_root)
