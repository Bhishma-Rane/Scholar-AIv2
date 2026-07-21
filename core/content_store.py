"""
core/content_store.py
========================
Bridge-backed storage for GENERATED study content: flashcards, study
guides, concept maps, mistake-notebook profiles, mock exams, and
chapter MCQ decks.

CHANGED: this content used to live only in local files under
core/paths.get_chapter_paths() (e.g. .../flashcards/Flashcards.json).
core/paths.py's own docstring flagged this as a known, unfixed gap:
Streamlit Cloud's container filesystem is ephemeral, so every one of
these files -- generated study materials, flashcard decks, mock exams
-- was silently lost on every restart/redeploy, the same class of bug
that subjects/uploaded files and analytics/streak data hit before
being moved onto the bridge. This module closes that gap using the
bridge's generic blob storage (see storage_bridge.py's /blobs/* routes
and core/bridge_client.py's blob_* wrappers).

Keys are namespaced as:
    content:<subject>:<chapter>:<category>:<filename>
so an entire chapter's or subject's generated content can be listed or
wiped in one call via delete_chapter_content() / delete_subject_content()
-- mirrors what core/paths.py's delete_chapter_local_content() /
delete_subject_local_content() used to do against the local folders.

`category` is a free-form label for what kind of content this is, e.g.
"flashcards", "guides", "mock", "mcq" -- matching the sub-folder names
core/paths.get_chapter_paths() used to hand out, so callers migrating
off local disk can reuse the same names they already had.
"""
import json

from core import bridge_client


def _key(subject: str, chapter: str, category: str, filename: str) -> str:
    return f"content:{subject}:{chapter}:{category}:{filename}"


def save_text(username: str, subject: str, chapter: str, category: str, filename: str, content: str) -> None:
    bridge_client.blob_set(username, _key(subject, chapter, category, filename), content)


def load_text(username: str, subject: str, chapter: str, category: str, filename: str):
    """Returns the stored text, or None if nothing has been saved under this name yet."""
    return bridge_client.blob_get(username, _key(subject, chapter, category, filename))


def exists(username: str, subject: str, chapter: str, category: str, filename: str) -> bool:
    return load_text(username, subject, chapter, category, filename) is not None


def save_json(username: str, subject: str, chapter: str, category: str, filename: str, data) -> None:
    save_text(username, subject, chapter, category, filename, json.dumps(data))


def load_json(username: str, subject: str, chapter: str, category: str, filename: str):
    """Returns the parsed JSON, or None if nothing has been saved under this name yet."""
    raw = load_text(username, subject, chapter, category, filename)
    if raw is None:
        return None
    return json.loads(raw)


def delete(username: str, subject: str, chapter: str, category: str, filename: str) -> None:
    """Deletes one piece of content (e.g. an entire flashcard deck, one mock exam file)."""
    bridge_client.blob_delete(username, _key(subject, chapter, category, filename))


def list_category(username: str, subject: str, chapter: str, category: str) -> list:
    """Returns just the filenames (not full keys) stored under one category,
    e.g. list_category(u, s, c, "guides") -> ["Study Roadmap.txt", "Concept_Map.txt", ...]."""
    prefix = f"content:{subject}:{chapter}:{category}:"
    keys = bridge_client.blob_list(username, prefix)
    return [k[len(prefix):] for k in keys]


def list_chapter_content(username: str, subject: str, chapter: str) -> list:
    """Returns every content item stored for one chapter (across all
    categories), as a list of {"category", "filename", "key"} dicts."""
    prefix = f"content:{subject}:{chapter}:"
    keys = bridge_client.blob_list(username, prefix)
    items = []
    for key in keys:
        rest = key[len(prefix):]
        if ":" not in rest:
            continue
        category, filename = rest.split(":", 1)
        items.append({"category": category, "filename": filename, "key": key})
    return items


def delete_chapter_content(username: str, subject: str, chapter: str) -> None:
    """Wipes every piece of generated content (flashcards, guides, mock exams,
    MCQs -- everything) for one chapter. Call this alongside
    core.paths.delete_chapter_local_content() when a chapter is deleted."""
    bridge_client.blob_delete_prefix(username, f"content:{subject}:{chapter}:")


def delete_subject_content(username: str, subject: str) -> None:
    """Wipes every piece of generated content for every chapter under a subject.
    Call this alongside core.paths.delete_subject_local_content() when a
    whole subject is deleted."""
    bridge_client.blob_delete_prefix(username, f"content:{subject}:")
