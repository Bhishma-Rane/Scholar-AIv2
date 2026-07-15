"""
storage_bridge.py
==================
Runs on Bhishma's Windows laptop (NOT on Streamlit Cloud). Persists
everything that Streamlit Cloud's ephemeral container filesystem cannot:
  - User credentials (replacing core/credentials.py's local JSON file)
  - Login tokens, for auto-login across page reloads
  - Subject lists and uploaded PDF/TXT files (raw bytes)
  - Subscription status + tier (manual, pen-and-paper access control)
  - Feedback (bug reports / suggestions / ratings)
  - Password reset tokens (admin-issued)
  - Quiz attempts (practice vs test mode, with server-enforced timer)

Backed by SQLite (one file, easy to back up, no separate DB server needed)
plus a plain folder on disk for the actual PDF/TXT bytes.

EXPOSE THIS VIA NGROK with a static domain, the same way the Ollama
tunnel works, e.g.:
    ngrok http 8800 --domain=your-static-bridge-domain.ngrok-free.app

Then set BRIDGE_BASE_URL in Streamlit Cloud's secrets to that domain.

Run with:
    uvicorn storage_bridge:app --host 0.0.0.0 --port 8800

Requires (in addition to fastapi/uvicorn/requests):
    pip install python-multipart
(needed for the Form(...) based routes below -- without it the app
fails to even start)

SECURITY NOTE: this bridge has no authentication of its own beyond the
shared secret header check below. Since it's exposed to the internet via
ngrok, the BRIDGE_SHARED_SECRET must be set to a long random value, kept
only in Streamlit Cloud's secrets and this server's environment — never
committed to git. Anyone with the secret can read/write all stored data,
so treat it like a password.
"""
import os
import json
import sqlite3
import secrets
import hashlib
import hmac
import re
import time
import contextlib
from pathlib import Path
from typing import Optional
from datetime import datetime, date
import requests

from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from grading_dispatcher import grade_paper_attempt

# ---------------------------------------------------------------------
# Storage locations (on the Windows machine's own disk — persists
# indefinitely, since this isn't a recycled container).
# ---------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).resolve().parent / "bridge_storage"
BRIDGE_DIR.mkdir(exist_ok=True)
DB_PATH = BRIDGE_DIR / "scholarai_bridge.db"
FILES_DIR = BRIDGE_DIR / "files"
FILES_DIR.mkdir(exist_ok=True)

# Shared secret for authenticating Streamlit Cloud -> bridge requests.
# Set this as a real environment variable before starting the server, e.g.
# (PowerShell):  $env:BRIDGE_SHARED_SECRET = "your-long-random-string"
# Never hardcode a real secret here / never commit one to git.
BRIDGE_SHARED_SECRET = os.environ.get("BRIDGE_SHARED_SECRET", "")
if not BRIDGE_SHARED_SECRET:
    raise RuntimeError(
        "BRIDGE_SHARED_SECRET environment variable is not set. "
        "Set it before starting the bridge — see the module docstring."
    )

PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16
TOKEN_BYTES = 32
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
PASSWORD_RESET_TTL_MINUTES = 15

# Tier ranking + per-feature rules. EDIT THESE TWO DICTS FREELY any time
# you want to change what each tier unlocks -- nothing else in this file
# needs to move.
#
# FEATURE_MIN_TIER: minimum tier required to use a feature AT ALL.
# "free" means everyone can use it -- DAILY_CAPS below is what actually
# limits Free/Gold usage of an otherwise-available feature.
#
# DAILY_CAPS: per-tier daily usage limits. A tier not listed for a
# feature (or set to None) means UNLIMITED for that tier on that feature.
TIER_RANK = {"free": 0, "gold": 1, "diamond": 2}
FEATURE_MIN_TIER = {
    "ai_chat": "free",
    "quiz_generation": "gold",
    "flashcards": "gold",
    "test_mode": "diamond",        # Free tier cannot use timed Test mode at all
    "question_paper": "diamond",   # Free tier cannot take Question Papers at all
}
DAILY_CAPS = {
    "ai_chat": {"free": 10, "gold": 50, "diamond": None},
    "quiz_generation": {"free": 1, "gold": 5, "diamond": None},
}

app = FastAPI(title="ScholarAI Storage Bridge")


# ---------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------
def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrate_add_mcq_question_type(conn):
    """
    'mcq' was added to VALID_QUESTION_TYPES and the app's UI/generator
    (ui/tab_question_paper.py, features/question_paper_generator.py)
    without ever being added to paper_questions' `type` CHECK constraint
    here. Every MCQ question generated since then has been silently
    rejected by add_question() with a 400 -- SQLite CHECK constraints
    can't be altered with ALTER TABLE, so CREATE TABLE IF NOT EXISTS
    above is a no-op on a DB that already has this table. This rebuilds
    paper_questions with 'mcq' included, preserving all existing rows,
    IDs, and the two FKs that point at it (paper_answers.question_id,
    and paper_questions.parent_question_id referencing itself).

    Safe to run every time the bridge starts: it checks the table's
    actual CREATE TABLE sql in sqlite_master first and does nothing if
    'mcq' is already present.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'paper_questions'"
    ).fetchone()
    if row is None or "'mcq'" in row[0]:
        return  # table doesn't exist yet (fresh DB -- CREATE TABLE above already has it) or already migrated

    # FK checks must be off for this -- otherwise inserting a child row
    # whose parent_question_id was inserted earlier in the SAME copy
    # statement, or before paper_answers is repointed, can be checked
    # against a mid-migration state and fail.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        ALTER TABLE paper_questions RENAME TO paper_questions_old_pre_mcq;

        CREATE TABLE paper_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            parent_question_id INTEGER,
            type TEXT NOT NULL CHECK (type IN ('vsa', 'sa', 'la', 'case_based', 'fill_blank', 'assertion_reason', 'map_marking', 'mcq')),
            marks REAL NOT NULL,
            order_index INTEGER NOT NULL DEFAULT 0,
            question_text TEXT,
            extra TEXT,
            FOREIGN KEY (section_id) REFERENCES paper_sections(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_question_id) REFERENCES paper_questions(id) ON DELETE CASCADE
        );

        INSERT INTO paper_questions
            (id, section_id, parent_question_id, type, marks, order_index, question_text, extra)
        SELECT id, section_id, parent_question_id, type, marks, order_index, question_text, extra
        FROM paper_questions_old_pre_mcq;

        DROP TABLE paper_questions_old_pre_mcq;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    print("[ScholarAI] Migrated paper_questions to allow type='mcq' (existing rows preserved).")


def _init_db():
    with contextlib.closing(_get_conn()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS login_tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS subjects (
                username TEXT NOT NULL,
                subject TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (username, subject)
            );

            CREATE TABLE IF NOT EXISTS files (
                username TEXT NOT NULL,
                subject TEXT NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                uploaded_at REAL NOT NULL,
                PRIMARY KEY (username, subject, filename)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('bug', 'suggestion', 'rating')),
                message TEXT,
                rating INTEGER,
                created_at REAL NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                subject TEXT,
                mode TEXT NOT NULL CHECK (mode IN ('practice', 'test')),
                timer_seconds INTEGER,
                time_taken_seconds INTEGER,
                started_at REAL NOT NULL,
                submitted_at REAL,
                auto_submitted INTEGER NOT NULL DEFAULT 0,
                score REAL,
                max_score REAL
            );

            CREATE TABLE IF NOT EXISTS question_papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                title TEXT NOT NULL,
                subject TEXT,
                total_marks REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                published INTEGER NOT NULL DEFAULT 0  -- vestigial, no longer read/written; kept so existing DBs don't need a migration
            );

            CREATE TABLE IF NOT EXISTS paper_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                instructions TEXT,
                order_index INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (paper_id) REFERENCES question_papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id INTEGER NOT NULL,
                parent_question_id INTEGER,
                type TEXT NOT NULL CHECK (type IN ('vsa', 'sa', 'la', 'case_based', 'fill_blank', 'assertion_reason', 'map_marking', 'mcq')),
                marks REAL NOT NULL,
                order_index INTEGER NOT NULL DEFAULT 0,
                question_text TEXT,
                extra TEXT,
                FOREIGN KEY (section_id) REFERENCES paper_sections(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_question_id) REFERENCES paper_questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('practice', 'test')),
                timer_seconds INTEGER,
                time_taken_seconds INTEGER,
                started_at REAL NOT NULL,
                submitted_at REAL,
                auto_submitted INTEGER NOT NULL DEFAULT 0,
                total_score REAL,
                max_score REAL,
                FOREIGN KEY (paper_id) REFERENCES question_papers(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS paper_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                answer_text TEXT,
                answer_blanks TEXT,
                answer_option TEXT,
                answer_x_pct REAL,
                answer_y_pct REAL,
                score REAL,
                max_score REAL,
                ai_feedback TEXT,
                graded INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (attempt_id) REFERENCES paper_attempts(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES paper_questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS daily_usage (
                username TEXT NOT NULL,
                feature TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (username, feature, usage_date)
            );
            """
        )

        _migrate_add_mcq_question_type(conn)

        # Columns added on top of the original `users` table (manual
        # access control + tiers). ALTER TABLE has no "IF NOT EXISTS" in
        # SQLite, so we check first -- this keeps _init_db() safe to run
        # every time the bridge starts, same as the rest of this function.
        if not _column_exists(conn, "users", "subscription_status"):
            conn.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'inactive'")
        if not _column_exists(conn, "users", "subscription_expires_at"):
            conn.execute("ALTER TABLE users ADD COLUMN subscription_expires_at TEXT")
        if not _column_exists(conn, "users", "subscription_plan"):
            conn.execute("ALTER TABLE users ADD COLUMN subscription_plan TEXT")
        if not _column_exists(conn, "users", "subscription_notes"):
            conn.execute("ALTER TABLE users ADD COLUMN subscription_notes TEXT")
        if not _column_exists(conn, "users", "tier"):
            conn.execute("ALTER TABLE users ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'")
        if not _column_exists(conn, "users", "theme_color"):
            # Stores the user's chosen accent color (hex string, e.g.
            # "#5a691d") so their preference follows them across devices/
            # sessions -- not just session_state, which resets per browser tab.
            conn.execute("ALTER TABLE users ADD COLUMN theme_color TEXT DEFAULT '#5a691d'")

        if not _column_exists(conn, "users", "tutorial_completed"):
            # Tracks whether this account has finished/skipped the
            # first-run tutorial, so it's shown once per account rather
            # than on every login. Previously lived in a local JSON
            # file on Streamlit Cloud's container disk
            # (core/onboarding_store.py) -- wiped on every container
            # restart, same class of bug subjects/files hit before they
            # moved to this bridge.
            conn.execute("ALTER TABLE users ADD COLUMN tutorial_completed INTEGER NOT NULL DEFAULT 0")

        conn.commit()


_init_db()


# ---------------------------------------------------------------------
# Auth helpers (mirrors core/credentials.py's hashing approach exactly,
# so behavior is unchanged for the end user — just relocated.)
# ---------------------------------------------------------------------
def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return derived.hex()


def _sanitize(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_\- ]", "", name).strip()
    return clean if clean else "unnamed"


def _require_secret(x_bridge_secret: Optional[str] = Header(None)):
    """FastAPI dependency: every endpoint must present the shared secret."""
    if not x_bridge_secret or not hmac.compare_digest(x_bridge_secret, BRIDGE_SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Invalid or missing bridge secret")


def _require_active_subscription(username: str):
    """
    Checks subscription_status + subscription_expires_at for a user.
    Call this explicitly inside any route you want to paywall (the
    Ollama routes, mainly -- those are what cost you compute).
    """
    username = username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT subscription_status, subscription_expires_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    if row["subscription_status"] != "active":
        raise HTTPException(
            status_code=402,
            detail="Subscription not active. Contact Bhishma to activate your access.",
        )

    if row["subscription_expires_at"]:
        expires_at = datetime.fromisoformat(row["subscription_expires_at"])
        if datetime.now() > expires_at:
            raise HTTPException(
                status_code=402,
                detail="Subscription expired. Contact Bhishma to renew your access.",
            )

    return True


def _require_tier(username: str, feature: str):
    """
    Two-part check:
      1. Does this user's tier meet the MINIMUM tier this feature needs at all?
      2. If allowed in, have they hit today's usage cap for their tier?
    Use ALONGSIDE _require_active_subscription, not instead of it --
    subscription answers "are they allowed in at all", tier answers
    "what can they do now that they're in". Raises HTTPException(403) for
    a tier-gate failure, HTTPException(429) for a daily-cap failure. On
    success, increments today's usage count for this feature -- call
    this ONCE per actual use (e.g. once per chat message sent).
    """
    username = username.strip().lower()
    min_tier = FEATURE_MIN_TIER.get(feature, "free")

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT tier FROM users WHERE username = ?", (username,)).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="User not found")

        user_tier = row["tier"] or "free"

        if TIER_RANK.get(user_tier, 0) < TIER_RANK.get(min_tier, 0):
            raise HTTPException(
                status_code=403,
                detail=f"This feature requires {min_tier} tier or higher. Contact Bhishma to upgrade.",
            )

        cap = (DAILY_CAPS.get(feature) or {}).get(user_tier)
        if cap is not None:
            today = date.today().isoformat()
            usage_row = conn.execute(
                "SELECT count FROM daily_usage WHERE username = ? AND feature = ? AND usage_date = ?",
                (username, feature, today),
            ).fetchone()
            current_count = usage_row["count"] if usage_row else 0

            if current_count >= cap:
                raise HTTPException(
                    status_code=429,
                    detail=f"Daily limit reached ({cap}/day on your current tier). "
                           f"Try again tomorrow, or ask Bhishma about upgrading.",
                )

            conn.execute(
                "INSERT INTO daily_usage (username, feature, usage_date, count) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(username, feature, usage_date) DO UPDATE SET count = count + 1",
                (username, feature, today),
            )
            conn.commit()

    return True


@app.get("/usage/today")
def get_usage_today(username: str, x_bridge_secret: Optional[str] = Header(None)):
    """Lets the Streamlit UI show 'you've used 7/10 chat messages today'."""
    _require_secret(x_bridge_secret)
    username = username.strip().lower()
    today = date.today().isoformat()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT tier FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        user_tier = row["tier"] or "free"

        usage_rows = conn.execute(
            "SELECT feature, count FROM daily_usage WHERE username = ? AND usage_date = ?",
            (username, today),
        ).fetchall()
        usage_by_feature = {r["feature"]: r["count"] for r in usage_rows}

    result = {}
    for feature, caps_by_tier in DAILY_CAPS.items():
        cap = caps_by_tier.get(user_tier)
        used = usage_by_feature.get(feature, 0)
        result[feature] = {"used": used, "cap": cap, "remaining": (cap - used) if cap is not None else None}

    return {"tier": user_tier, "usage": result}


# ---------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------
class CreateAccountRequest(BaseModel):
    username: str
    password: str


class VerifyPasswordRequest(BaseModel):
    username: str
    password: str


class VerifyTokenRequest(BaseModel):
    token: str


class UserExistsRequest(BaseModel):
    username: str


class SetThemeColorRequest(BaseModel):
    username: str
    theme_color: str


class SetTutorialCompletedRequest(BaseModel):
    username: str
    completed: bool


class OllamaGenerateRequest(BaseModel):
    model: str
    prompt: str
    system: Optional[str] = None
    username: str
    # Which FEATURE_MIN_TIER/DAILY_CAPS key this call should be gated as
    # (e.g. "ai_chat", "quiz_generation", "question_paper"). Defaults to
    # "ai_chat" on the route itself if omitted, for backward compatibility
    # with any caller that hasn't been updated yet.
    feature: Optional[str] = None


class OllamaChatRequest(BaseModel):
    model: str
    messages: list
    username: str
    # Which FEATURE_MIN_TIER/DAILY_CAPS key this call should be gated as
    # (e.g. "ai_chat", "quiz_generation", "question_paper"). Defaults to
    # "ai_chat" on the route itself if omitted, for backward compatibility
    # with any caller that hasn't been updated yet. THIS is the fix for
    # the bug where every AI feature (chat, quiz gen, flashcards) was
    # silently checked against "ai_chat" no matter what it actually was --
    # see the route below.
    feature: Optional[str] = None
    # Both optional and forwarded as-is to Ollama's /api/chat. `options`
    # is Ollama's per-request generation params dict -- the one we care
    # about here is num_predict, which bounds response length; without
    # it, calls that ask for a specific question count (see
    # features/question_paper_generator.py's "paper" model_type) had no
    # server-side cap and were truncating early with no obvious reason
    # tied to a knob callers had actually set. `format` set to "json"
    # requests Ollama's native structured-JSON output mode, which cuts
    # down on markdown-fenced or chatty-preamble responses that
    # core/llm.py's brace-depth JSON extraction otherwise has to route
    # around.
    options: Optional[dict] = None
    format: Optional[str] = None

class OllamaEmbedRequest(BaseModel):
    model: str
    # Ollama's /api/embed accepts a string or list of strings under "input".
    # langchain_ollama.OllamaEmbeddings always calls embed_documents() with
    # a list, so this is typed as list here.
    input: list
    username: str

class FeedbackRequest(BaseModel):
    username: str
    kind: str          # "bug" | "suggestion" | "rating"
    message: Optional[str] = None
    rating: Optional[int] = None


class ResetPasswordRequest(BaseModel):
    username: str
    token: str
    new_password: str


class StartQuizAttemptRequest(BaseModel):
    username: str
    subject: Optional[str] = None
    mode: str                            # "practice" | "test"
    timer_seconds: Optional[int] = None  # required if mode == "test"


class SubmitQuizAttemptRequest(BaseModel):
    attempt_id: int
    username: str
    score: float
    max_score: float


# ---------------------------------------------------------------------
# Credential endpoints
# ---------------------------------------------------------------------
@app.post("/auth/create_account")
def create_account(req: CreateAccountRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            return {"success": False, "reason": "username_taken"}

        salt = secrets.token_bytes(SALT_BYTES)
        password_hash = _hash_password(req.password, salt)
        # Explicitly set subscription_status='active' and tier='free' on
        # insert (rather than relying on the columns' own defaults) so a
        # brand-new signup can use Free-tier chat immediately, without
        # needing toggle_access.py run by hand first. Change 'active' back
        # to relying on the column default ('inactive') here if you decide
        # you want to require manual activation even for Free tier.
        conn.execute(
            "INSERT INTO users (username, salt, password_hash, created_at, "
            "subscription_status, tier) VALUES (?, ?, ?, ?, 'active', 'free')",
            (username, salt.hex(), password_hash, time.time()),
        )
        conn.commit()

    return {"success": True}


@app.post("/auth/user_exists")
def user_exists(req: UserExistsRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()
    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    return {"exists": row is not None}


@app.get("/users/theme_color")
def get_theme_color(username: str, x_bridge_secret: Optional[str] = Header(None)):
    """Lets the Streamlit app load the saved accent color on every login,
    so the choice follows the account across devices/sessions."""
    _require_secret(x_bridge_secret)
    username = username.strip().lower()
    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT theme_color FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"theme_color": row["theme_color"] or "#5a691d"}


@app.post("/users/theme_color")
def set_theme_color(req: SetThemeColorRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    color = req.theme_color.strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise HTTPException(status_code=400, detail="theme_color must be a 6-digit hex color like #5a691d")

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("UPDATE users SET theme_color = ? WHERE username = ?", (color, username))
        conn.commit()

    return {"success": True, "theme_color": color}


@app.get("/users/tutorial_completed")
def get_tutorial_completed(username: str, x_bridge_secret: Optional[str] = Header(None)):
    """Lets the Streamlit app know whether to show the first-run tutorial,
    so it's shown once per account rather than on every login."""
    _require_secret(x_bridge_secret)
    username = username.strip().lower()
    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT tutorial_completed FROM users WHERE username = ?", (username,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"completed": bool(row["tutorial_completed"])}


@app.post("/users/tutorial_completed")
def set_tutorial_completed(req: SetTutorialCompletedRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "UPDATE users SET tutorial_completed = ? WHERE username = ?",
            (1 if req.completed else 0, username),
        )
        conn.commit()

    return {"success": True, "completed": req.completed}


@app.post("/auth/verify_password")
def verify_password(req: VerifyPasswordRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()

    if row is None:
        return {"valid": False}

    salt = bytes.fromhex(row["salt"])
    candidate_hash = _hash_password(req.password, salt)
    valid = hmac.compare_digest(candidate_hash, row["password_hash"])
    return {"valid": valid}


@app.post("/auth/issue_token")
def issue_token(req: VerifyPasswordRequest, x_bridge_secret: Optional[str] = Header(None)):
    """
    Verifies the password AND, if valid, issues a long-lived login token.
    Called once at successful login; the token then goes into the
    browser's URL so a page reload can skip the password form.
    """
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return {"valid": False, "token": None}

        salt = bytes.fromhex(row["salt"])
        candidate_hash = _hash_password(req.password, salt)
        if not hmac.compare_digest(candidate_hash, row["password_hash"]):
            return {"valid": False, "token": None}

        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        conn.execute(
            "INSERT INTO login_tokens (token, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, username, now, now + TOKEN_TTL_SECONDS),
        )
        conn.commit()

    return {"valid": True, "token": token}


@app.post("/auth/verify_token")
def verify_token(req: VerifyTokenRequest, x_bridge_secret: Optional[str] = Header(None)):
    """Used on every page load to silently re-authenticate from the URL token."""
    _require_secret(x_bridge_secret)

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM login_tokens WHERE token = ?", (req.token,)
        ).fetchone()

        if row is None or row["expires_at"] < time.time():
            return {"valid": False, "username": None}

        return {"valid": True, "username": row["username"]}


@app.post("/auth/revoke_token")
def revoke_token(req: VerifyTokenRequest, x_bridge_secret: Optional[str] = Header(None)):
    """Called on logout, so the old token can't be reused."""
    _require_secret(x_bridge_secret)
    with contextlib.closing(_get_conn()) as conn:
        conn.execute("DELETE FROM login_tokens WHERE token = ?", (req.token,))
        conn.commit()
    return {"success": True}


@app.post("/auth/reset_password")
def reset_password(req: ResetPasswordRequest, x_bridge_secret: Optional[str] = Header(None)):
    """
    Admin-issued password reset. The token itself is generated by you,
    on your machine, via reset_password.py -- this endpoint just
    validates whatever token the student types in and applies the new
    password. See reset_password.py for the full flow.
    """
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT expires_at, used FROM password_reset_tokens WHERE token = ? AND username = ?",
            (req.token, username),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=400, detail="Invalid reset token")
        if row["used"]:
            raise HTTPException(status_code=400, detail="This token has already been used")
        if row["expires_at"] < time.time():
            raise HTTPException(status_code=400, detail="This token has expired")

        if len(req.new_password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

        salt = secrets.token_bytes(SALT_BYTES)
        new_hash = _hash_password(req.new_password, salt)

        conn.execute(
            "UPDATE users SET salt = ?, password_hash = ? WHERE username = ?",
            (salt.hex(), new_hash, username),
        )
        conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (req.token,))
        # Revoke existing sessions so an old logged-in device can't keep
        # using the account after the password's been changed out from
        # under it.
        conn.execute("DELETE FROM login_tokens WHERE username = ?", (username,))
        conn.commit()

    return {"success": True}


class DeleteAccountRequest(BaseModel):
    username: str
    password: str


@app.post("/account/delete")
def delete_account(req: DeleteAccountRequest, x_bridge_secret: Optional[str] = Header(None)):
    """
    Permanently deletes an account and everything tied to it:
    credentials, login tokens, subjects + uploaded files (DB rows AND
    the actual files on disk), question papers (sections/questions/
    attempts/answers cascade automatically via their ON DELETE CASCADE
    foreign keys once the question_papers rows go), quiz_attempts,
    daily_usage, password_reset_tokens, and feedback.

    Requires the account's own current password, re-verified HERE,
    server-side -- the same check /auth/verify_password does. This is
    deliberate: the Streamlit UI's password/username re-confirmation
    step (see ui/tab_settings.py) is a UX guard against misclicks, not
    a security boundary on its own. If it were, a forged or replayed
    request straight to this route with no real password check would
    be able to wipe any account. Re-checking here means the bridge
    itself refuses the delete regardless of what the caller claims.
    """
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No such account")

        salt = bytes.fromhex(row["salt"])
        candidate_hash = _hash_password(req.password, salt)
        if not hmac.compare_digest(candidate_hash, row["password_hash"]):
            raise HTTPException(status_code=403, detail="Incorrect password")

        # question_papers rows cascade to paper_sections -> paper_questions
        # and to paper_attempts -> paper_answers via their ON DELETE CASCADE
        # foreign keys (see _init_db()), so deleting question_papers alone
        # is enough for that whole tree.
        conn.execute("DELETE FROM question_papers WHERE username = ?", (username,))
        conn.execute("DELETE FROM quiz_attempts WHERE username = ?", (username,))
        conn.execute("DELETE FROM files WHERE username = ?", (username,))
        conn.execute("DELETE FROM subjects WHERE username = ?", (username,))
        conn.execute("DELETE FROM daily_usage WHERE username = ?", (username,))
        conn.execute("DELETE FROM password_reset_tokens WHERE username = ?", (username,))
        conn.execute("DELETE FROM feedback WHERE username = ?", (username,))
        # login_tokens cascades automatically via its FK to users (see
        # _init_db()), but deleting it explicitly first costs nothing and
        # doesn't rely on the per-connection "PRAGMA foreign_keys = ON"
        # having actually taken effect.
        conn.execute("DELETE FROM login_tokens WHERE username = ?", (username,))
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()

    user_dir = FILES_DIR / username
    if user_dir.exists():
        import shutil
        shutil.rmtree(user_dir, ignore_errors=True)

    return {"success": True}


# ---------------------------------------------------------------------
# Subject + file endpoints
# ---------------------------------------------------------------------
@app.post("/subjects/create")
def create_subject(username: str = Form(...), subject: str = Form(...),
                    x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)

    with contextlib.closing(_get_conn()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subjects (username, subject, created_at) VALUES (?, ?, ?)",
            (username, subject, time.time()),
        )
        conn.commit()

    (FILES_DIR / username / subject).mkdir(parents=True, exist_ok=True)
    return {"success": True}


@app.get("/subjects/list")
def list_subjects(username: str, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    with contextlib.closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT subject FROM subjects WHERE username = ? ORDER BY subject", (username,)
        ).fetchall()
    return {"subjects": [r["subject"] for r in rows]}


@app.post("/subjects/delete")
def delete_subject(username: str = Form(...), subject: str = Form(...),
                    x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)

    with contextlib.closing(_get_conn()) as conn:
        conn.execute("DELETE FROM subjects WHERE username = ? AND subject = ?", (username, subject))
        conn.execute("DELETE FROM files WHERE username = ? AND subject = ?", (username, subject))
        conn.commit()

    subj_dir = FILES_DIR / username / subject
    if subj_dir.exists():
        import shutil
        shutil.rmtree(subj_dir, ignore_errors=True)

    return {"success": True}


@app.post("/files/upload")
def upload_file(username: str = Form(...), subject: str = Form(...),
                 file: UploadFile = File(...),
                 x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("pdf", "txt"):
        raise HTTPException(status_code=400, detail="Only .pdf and .txt files are accepted")

    base_name = file.filename.rsplit(".", 1)[0]
    safe_name = f"{_sanitize(base_name)}.{ext}"

    subj_dir = FILES_DIR / username / subject
    subj_dir.mkdir(parents=True, exist_ok=True)
    dest_path = subj_dir / safe_name

    contents = file.file.read()
    with open(dest_path, "wb") as f:
        f.write(contents)

    with contextlib.closing(_get_conn()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (username, subject, filename, stored_path, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, subject, safe_name, str(dest_path), time.time()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO subjects (username, subject, created_at) VALUES (?, ?, ?)",
            (username, subject, time.time()),
        )
        conn.commit()

    return {"success": True, "filename": safe_name}


@app.get("/files/list")
def list_files(username: str, subject: str, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)

    with contextlib.closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT filename FROM files WHERE username = ? AND subject = ? ORDER BY filename",
            (username, subject),
        ).fetchall()
    return {"files": [r["filename"] for r in rows]}


@app.get("/files/download")
def download_file(username: str, subject: str, filename: str,
                   x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)
    safe_filename = os.path.basename(filename)  # defense in depth against path traversal

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT stored_path FROM files WHERE username = ? AND subject = ? AND filename = ?",
            (username, subject, safe_filename),
        ).fetchone()

    if row is None or not os.path.exists(row["stored_path"]):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(row["stored_path"], filename=safe_filename)


@app.post("/files/delete")
def delete_file(username: str = Form(...), subject: str = Form(...), filename: str = Form(...),
                 x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = _sanitize(username).lower()
    subject = _sanitize(subject)
    safe_filename = os.path.basename(filename)

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT stored_path FROM files WHERE username = ? AND subject = ? AND filename = ?",
            (username, subject, safe_filename),
        ).fetchone()
        conn.execute(
            "DELETE FROM files WHERE username = ? AND subject = ? AND filename = ?",
            (username, subject, safe_filename),
        )
        conn.commit()

    if row and os.path.exists(row["stored_path"]):
        os.remove(row["stored_path"])

    return {"success": True}


# ---------------------------------------------------------------------
# Feedback endpoints (bug reports / suggestions / ratings)
# ---------------------------------------------------------------------
@app.post("/feedback/submit")
def submit_feedback(req: FeedbackRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    if req.kind not in ("bug", "suggestion", "rating"):
        raise HTTPException(status_code=400, detail="kind must be 'bug', 'suggestion', or 'rating'")

    if req.kind == "rating":
        if req.rating is None or not (1 <= req.rating <= 5):
            raise HTTPException(status_code=400, detail="rating must be an integer 1-5")
    else:
        if not req.message or not req.message.strip():
            raise HTTPException(status_code=400, detail="message is required for bug/suggestion")

    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        conn.execute(
            "INSERT INTO feedback (username, kind, message, rating, created_at, resolved) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (username, req.kind, req.message, req.rating, time.time()),
        )
        conn.commit()

    return {"success": True}


@app.get("/feedback/list")
def list_feedback(x_bridge_secret: Optional[str] = Header(None)):
    """
    Developer-only view of all feedback. Gated by the same shared secret
    as everything else -- only you (and your Streamlit app) have that,
    so no separate admin password is needed.
    """
    _require_secret(x_bridge_secret)

    with contextlib.closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, username, kind, message, rating, created_at, resolved "
            "FROM feedback ORDER BY created_at DESC"
        ).fetchall()

    return {
        "feedback": [
            {
                "id": r["id"], "username": r["username"], "kind": r["kind"],
                "message": r["message"], "rating": r["rating"],
                "created_at": r["created_at"], "resolved": bool(r["resolved"]),
            }
            for r in rows
        ]
    }


@app.post("/feedback/resolve")
def resolve_feedback(feedback_id: int = Form(...), x_bridge_secret: Optional[str] = Header(None)):
    """Mark a feedback item as handled."""
    _require_secret(x_bridge_secret)
    with contextlib.closing(_get_conn()) as conn:
        conn.execute("UPDATE feedback SET resolved = 1 WHERE id = ?", (feedback_id,))
        conn.commit()
    return {"success": True}


# ---------------------------------------------------------------------
# Quiz attempt endpoints (practice vs test mode, server-enforced timer)
# ---------------------------------------------------------------------
@app.post("/quiz/start_attempt")
def start_quiz_attempt(req: StartQuizAttemptRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    if req.mode not in ("practice", "test"):
        raise HTTPException(status_code=400, detail="mode must be 'practice' or 'test'")

    if req.mode == "test":
        if not req.timer_seconds or req.timer_seconds <= 0:
            raise HTTPException(status_code=400, detail="test mode requires a positive timer_seconds")
        if req.timer_seconds > 4 * 60 * 60:
            raise HTTPException(status_code=400, detail="timer_seconds must be 4 hours or less")

    _require_active_subscription(req.username)
    if req.mode == "test":
        _require_tier(req.username, "test_mode")

    username = req.username.strip().lower()
    now = time.time()

    with contextlib.closing(_get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO quiz_attempts (username, subject, mode, timer_seconds, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, req.subject, req.mode,
             req.timer_seconds if req.mode == "test" else None, now),
        )
        conn.commit()
        attempt_id = cur.lastrowid

    return {
        "attempt_id": attempt_id,
        "mode": req.mode,
        "timer_seconds": req.timer_seconds if req.mode == "test" else None,
        "started_at": now,
    }


@app.post("/quiz/submit_attempt")
def submit_quiz_attempt(req: SubmitQuizAttemptRequest, x_bridge_secret: Optional[str] = Header(None)):
    """
    Server independently checks elapsed time against the stored
    timer_seconds. If they're well past the limit (beyond a small grace
    window for network lag), the attempt is still recorded but flagged
    auto_submitted=1 -- informational, doesn't block the submission.
    """
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()
    now = time.time()

    with contextlib.closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT username, mode, timer_seconds, started_at, submitted_at "
            "FROM quiz_attempts WHERE id = ?",
            (req.attempt_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Attempt not found")
        if row["username"] != username:
            raise HTTPException(status_code=403, detail="This attempt belongs to a different user")
        if row["submitted_at"] is not None:
            raise HTTPException(status_code=400, detail="This attempt was already submitted")

        time_taken = int(now - row["started_at"])
        auto_submitted = 0

        if row["mode"] == "test" and row["timer_seconds"]:
            grace = 10
            if time_taken > row["timer_seconds"] + grace:
                auto_submitted = 1

        conn.execute(
            "UPDATE quiz_attempts SET submitted_at = ?, time_taken_seconds = ?, "
            "auto_submitted = ?, score = ?, max_score = ? WHERE id = ?",
            (now, time_taken, auto_submitted, req.score, req.max_score, req.attempt_id),
        )
        conn.commit()

    return {
        "success": True,
        "time_taken_seconds": time_taken,
        "auto_submitted": bool(auto_submitted),
    }


@app.get("/quiz/history")
def quiz_history(username: str, x_bridge_secret: Optional[str] = Header(None)):
    """For the dashboard -- past attempts, split by mode."""
    _require_secret(x_bridge_secret)
    username = username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, subject, mode, timer_seconds, time_taken_seconds, "
            "score, max_score, started_at, submitted_at, auto_submitted "
            "FROM quiz_attempts WHERE username = ? ORDER BY started_at DESC",
            (username,),
        ).fetchall()

    return {
        "attempts": [
            {
                "id": r["id"], "subject": r["subject"], "mode": r["mode"],
                "timer_seconds": r["timer_seconds"], "time_taken_seconds": r["time_taken_seconds"],
                "score": r["score"], "max_score": r["max_score"],
                "started_at": r["started_at"], "submitted_at": r["submitted_at"],
                "auto_submitted": bool(r["auto_submitted"]),
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------
# Question Papers: authoring (layer 1)
# ---------------------------------------------------------------------
VALID_QUESTION_TYPES = ("vsa", "sa", "la", "case_based", "fill_blank", "assertion_reason", "map_marking", "mcq")


class CreatePaperRequest(BaseModel):
    username: str
    title: str
    subject: Optional[str] = None


class AddSectionRequest(BaseModel):
    paper_id: int
    title: str
    instructions: Optional[str] = None
    order_index: int = 0


class AddQuestionRequest(BaseModel):
    section_id: int
    type: str
    marks: float
    order_index: int = 0
    question_text: Optional[str] = None
    parent_question_id: Optional[int] = None
    extra: Optional[dict] = None


def _validate_question_extra(qtype: str, extra: Optional[dict]):
    """Raises HTTPException(400) if `extra` doesn't match what this question type needs."""
    extra = extra or {}

    if qtype in ("vsa", "sa", "la"):
        return

    if qtype == "case_based":
        if not extra.get("passage") or not isinstance(extra["passage"], str):
            raise HTTPException(status_code=400, detail="case_based requires extra.passage (string)")

    elif qtype == "mcq":
        options = extra.get("options")
        correct_option = extra.get("correct_option")
        if not options or not isinstance(options, list) or len(options) < 2 or not all(isinstance(o, str) for o in options):
            raise HTTPException(status_code=400, detail="mcq requires extra.options (list of at least 2 strings)")
        if not isinstance(correct_option, int) or isinstance(correct_option, bool) or not (0 <= correct_option < len(options)):
            raise HTTPException(
                status_code=400,
                detail=f"mcq extra.correct_option must be an integer index between 0 and {len(options) - 1}",
            )

    elif qtype == "fill_blank":
        text = extra.get("text_with_blanks")
        blanks = extra.get("blanks")
        if not text or not isinstance(text, str):
            raise HTTPException(status_code=400, detail="fill_blank requires extra.text_with_blanks (string)")
        if not blanks or not isinstance(blanks, list) or not all(isinstance(b, str) for b in blanks):
            raise HTTPException(status_code=400, detail="fill_blank requires extra.blanks (list of strings)")
        blank_count = text.count("___")
        if blank_count != len(blanks):
            raise HTTPException(
                status_code=400,
                detail=f"fill_blank has {blank_count} '___' markers but {len(blanks)} answers in extra.blanks -- these must match",
            )

    elif qtype == "assertion_reason":
        for field in ("assertion", "reason", "correct_option"):
            if not extra.get(field):
                raise HTTPException(status_code=400, detail=f"assertion_reason requires extra.{field}")
        if extra["correct_option"] not in ("A", "B", "C", "D"):
            raise HTTPException(status_code=400, detail="assertion_reason correct_option must be A, B, C, or D")

    elif qtype == "map_marking":
        for field in ("image_path", "correct_x_pct", "correct_y_pct", "radius_pct"):
            if field not in extra:
                raise HTTPException(status_code=400, detail=f"map_marking requires extra.{field}")
        for field in ("correct_x_pct", "correct_y_pct", "radius_pct"):
            val = extra[field]
            if not isinstance(val, (int, float)) or not (0 <= val <= 100):
                raise HTTPException(status_code=400, detail=f"map_marking extra.{field} must be a number 0-100")


@app.post("/papers/create")
def create_paper(req: CreatePaperRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()
    # Was previously ungated -- any tier could create (and store) full
    # question papers here, even though "question_paper" is diamond-only
    # in FEATURE_MIN_TIER. That min-tier was only ever being checked later,
    # at /papers/start_attempt -- i.e. after the paper already existed.
    _require_active_subscription(username)
    _require_tier(username, "question_paper")

    with contextlib.closing(_get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO question_papers (username, title, subject, total_marks, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (username, req.title.strip(), req.subject, time.time()),
        )
        conn.commit()
        paper_id = cur.lastrowid

    return {"paper_id": paper_id, "title": req.title, "subject": req.subject}


@app.post("/papers/add_section")
def add_section(req: AddSectionRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    with contextlib.closing(_get_conn()) as conn:
        paper = conn.execute("SELECT id FROM question_papers WHERE id = ?", (req.paper_id,)).fetchone()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        cur = conn.execute(
            "INSERT INTO paper_sections (paper_id, title, instructions, order_index) VALUES (?, ?, ?, ?)",
            (req.paper_id, req.title.strip(), req.instructions, req.order_index),
        )
        conn.commit()
        section_id = cur.lastrowid

    return {"section_id": section_id, "paper_id": req.paper_id, "title": req.title}


@app.post("/papers/add_question")
def add_question(req: AddQuestionRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    if req.type not in VALID_QUESTION_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {VALID_QUESTION_TYPES}")

    if req.type not in ("case_based", "fill_blank", "assertion_reason", "map_marking") and not req.question_text:
        raise HTTPException(status_code=400, detail="question_text is required for this type")

    # case_based parents carry marks=0 by convention -- the real marks
    # live on their sub-questions. Every other type must have positive marks.
    if req.type == "case_based":
        if req.marks < 0:
            raise HTTPException(status_code=400, detail="marks cannot be negative")
    elif req.marks <= 0:
        raise HTTPException(status_code=400, detail="marks must be a positive number")

    _validate_question_extra(req.type, req.extra)

    with contextlib.closing(_get_conn()) as conn:
        section = conn.execute("SELECT paper_id FROM paper_sections WHERE id = ?", (req.section_id,)).fetchone()
        if section is None:
            raise HTTPException(status_code=404, detail="Section not found")

        if req.parent_question_id is not None:
            parent = conn.execute(
                "SELECT id FROM paper_questions WHERE id = ?", (req.parent_question_id,)
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=404, detail="parent_question_id not found")

        cur = conn.execute(
            "INSERT INTO paper_questions "
            "(section_id, parent_question_id, type, marks, order_index, question_text, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (req.section_id, req.parent_question_id, req.type, req.marks,
             req.order_index, req.question_text, json.dumps(req.extra) if req.extra else None),
        )
        conn.commit()
        question_id = cur.lastrowid

        paper_id = section["paper_id"]
        total = conn.execute(
            "SELECT COALESCE(SUM(marks), 0) FROM paper_questions pq "
            "JOIN paper_sections ps ON pq.section_id = ps.id WHERE ps.paper_id = ?",
            (paper_id,),
        ).fetchone()[0]
        conn.execute("UPDATE question_papers SET total_marks = ? WHERE id = ?", (total, paper_id))
        conn.commit()

    return {"question_id": question_id, "section_id": req.section_id, "type": req.type, "marks": req.marks}


@app.get("/papers/get")
def get_paper(paper_id: int, x_bridge_secret: Optional[str] = Header(None)):
    """Returns the full assembled paper: sections, each with its questions
    (case_based questions include their nested sub_questions)."""
    _require_secret(x_bridge_secret)

    with contextlib.closing(_get_conn()) as conn:
        paper = conn.execute(
            "SELECT id, username, title, subject, total_marks, created_at "
            "FROM question_papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        sections = conn.execute(
            "SELECT id, title, instructions, order_index FROM paper_sections "
            "WHERE paper_id = ? ORDER BY order_index, id",
            (paper_id,),
        ).fetchall()

        result_sections = []
        for sec in sections:
            questions = conn.execute(
                "SELECT id, parent_question_id, type, marks, order_index, question_text, extra "
                "FROM paper_questions WHERE section_id = ? AND parent_question_id IS NULL "
                "ORDER BY order_index, id",
                (sec["id"],),
            ).fetchall()

            result_questions = []
            for q in questions:
                q_dict = {
                    "id": q["id"], "type": q["type"], "marks": q["marks"],
                    "order_index": q["order_index"], "question_text": q["question_text"],
                    "extra": json.loads(q["extra"]) if q["extra"] else None,
                }
                if q["type"] == "case_based":
                    subs = conn.execute(
                        "SELECT id, type, marks, order_index, question_text, extra "
                        "FROM paper_questions WHERE parent_question_id = ? ORDER BY order_index, id",
                        (q["id"],),
                    ).fetchall()
                    q_dict["sub_questions"] = [
                        {
                            "id": s["id"], "type": s["type"], "marks": s["marks"],
                            "order_index": s["order_index"], "question_text": s["question_text"],
                            "extra": json.loads(s["extra"]) if s["extra"] else None,
                        }
                        for s in subs
                    ]
                result_questions.append(q_dict)

            result_sections.append({
                "id": sec["id"], "title": sec["title"], "instructions": sec["instructions"],
                "order_index": sec["order_index"], "questions": result_questions,
            })

    return {
        "id": paper["id"], "username": paper["username"], "title": paper["title"],
        "subject": paper["subject"], "total_marks": paper["total_marks"],
        "created_at": paper["created_at"],
        "sections": result_sections,
    }


@app.get("/papers/list")
def list_papers(
    username: str,
    subject: Optional[str] = None,
    admin_view: bool = False,
    x_bridge_secret: Optional[str] = Header(None),
):
    """
    Returns question papers belonging to `username`, newest first. No publish/
    draft step -- a paper is visible to its owner the moment it's created.

    admin_view=True bypasses the username filter and returns every paper
    (for the admin GUI's management screen). Still requires the shared
    secret, same as every other route here.
    """
    _require_secret(x_bridge_secret)
    username = username.strip().lower()

    query = "SELECT id, username, title, subject, total_marks, created_at FROM question_papers"
    conditions = []
    params = []

    if not admin_view:
        conditions.append("username = ?")
        params.append(username)
    if subject:
        conditions.append("subject = ?")
        params.append(subject)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC"

    with contextlib.closing(_get_conn()) as conn:
        rows = conn.execute(query, params).fetchall()

    return {"papers": [dict(r) for r in rows]}


class DeletePaperRequest(BaseModel):
    username: str
    paper_id: int


@app.post("/papers/delete")
def delete_paper(req: DeletePaperRequest, x_bridge_secret: Optional[str] = Header(None)):
    """
    Deletes a question paper this user owns. paper_sections ->
    paper_questions and paper_attempts -> paper_answers cascade
    automatically via their ON DELETE CASCADE foreign keys (see
    _init_db()), so deleting the question_papers row alone is enough
    to clean up the whole tree.
    """
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        paper = conn.execute(
            "SELECT username FROM question_papers WHERE id = ?", (req.paper_id,)
        ).fetchone()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        if paper["username"] != username:
            raise HTTPException(status_code=403, detail="You don't own this paper")

        conn.execute("DELETE FROM question_papers WHERE id = ?", (req.paper_id,))
        conn.commit()

    return {"success": True}


# ---------------------------------------------------------------------
# Question Papers: attempts + grading (layer 2)
# ---------------------------------------------------------------------
class AnswerSubmission(BaseModel):
    question_id: int
    answer_text: Optional[str] = None
    answer_blanks: Optional[list] = None
    answer_option: Optional[str] = None
    answer_x_pct: Optional[float] = None
    answer_y_pct: Optional[float] = None


class StartPaperAttemptRequest(BaseModel):
    username: str
    paper_id: int
    mode: str
    timer_seconds: Optional[int] = None


class SubmitPaperAttemptRequest(BaseModel):
    attempt_id: int
    username: str
    answers: list[AnswerSubmission]
    lang: str = "English"
    negative_marking: bool = False


@app.post("/papers/start_attempt")
def start_paper_attempt(req: StartPaperAttemptRequest, x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    if req.mode not in ("practice", "test"):
        raise HTTPException(status_code=400, detail="mode must be 'practice' or 'test'")
    if req.mode == "test":
        if not req.timer_seconds or req.timer_seconds <= 0:
            raise HTTPException(status_code=400, detail="test mode requires a positive timer_seconds")
        if req.timer_seconds > 4 * 60 * 60:
            raise HTTPException(status_code=400, detail="timer_seconds must be 4 hours or less")

    # Tier gate: taking ANY question paper requires the "question_paper"
    # feature's minimum tier. Test mode specifically (on top of that)
    # requires the "test_mode" feature's minimum tier -- so a tier that's
    # allowed papers-in-practice-mode but not Test mode is expressible by
    # setting these two FEATURE_MIN_TIER entries differently.
    _require_active_subscription(req.username)
    _require_tier(req.username, "question_paper")
    if req.mode == "test":
        _require_tier(req.username, "test_mode")

    username = req.username.strip().lower()
    now = time.time()

    with contextlib.closing(_get_conn()) as conn:
        paper = conn.execute("SELECT id FROM question_papers WHERE id = ?", (req.paper_id,)).fetchone()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        cur = conn.execute(
            "INSERT INTO paper_attempts (paper_id, username, mode, timer_seconds, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (req.paper_id, username, req.mode, req.timer_seconds if req.mode == "test" else None, now),
        )
        conn.commit()
        attempt_id = cur.lastrowid

    return {
        "attempt_id": attempt_id, "mode": req.mode,
        "timer_seconds": req.timer_seconds if req.mode == "test" else None,
        "started_at": now,
    }


@app.post("/papers/submit_attempt")
def submit_paper_attempt(req: SubmitPaperAttemptRequest, x_bridge_secret: Optional[str] = Header(None)):
    """Grades the attempt via grade_paper_attempt() and persists per-question
    scores into paper_answers, then totals into paper_attempts."""
    _require_secret(x_bridge_secret)
    username = req.username.strip().lower()
    now = time.time()

    with contextlib.closing(_get_conn()) as conn:
        attempt = conn.execute(
            "SELECT paper_id, username, mode, timer_seconds, started_at, submitted_at "
            "FROM paper_attempts WHERE id = ?",
            (req.attempt_id,),
        ).fetchone()

        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")
        if attempt["username"] != username:
            raise HTTPException(status_code=403, detail="This attempt belongs to a different user")
        if attempt["submitted_at"] is not None:
            raise HTTPException(status_code=400, detail="This attempt was already submitted")

    paper = get_paper(attempt["paper_id"], x_bridge_secret)

    answers_by_question_id = {
        a.question_id: {
            "answer_text": a.answer_text, "answer_blanks": a.answer_blanks,
            "answer_option": a.answer_option, "answer_x_pct": a.answer_x_pct,
            "answer_y_pct": a.answer_y_pct,
        }
        for a in req.answers
    }

    grading_result = grade_paper_attempt(paper, answers_by_question_id, lang=req.lang, negative_marking=req.negative_marking)

    time_taken = int(now - attempt["started_at"])
    auto_submitted = 0
    if attempt["mode"] == "test" and attempt["timer_seconds"]:
        grace = 10
        if time_taken > attempt["timer_seconds"] + grace:
            auto_submitted = 1

    with contextlib.closing(_get_conn()) as conn:
        for pq in grading_result["per_question"]:
            answer = answers_by_question_id.get(pq["question_id"], {})
            conn.execute(
                "INSERT INTO paper_answers "
                "(attempt_id, question_id, answer_text, answer_blanks, answer_option, "
                "answer_x_pct, answer_y_pct, score, max_score, ai_feedback, graded) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    req.attempt_id, pq["question_id"], answer.get("answer_text"),
                    json.dumps(answer.get("answer_blanks")) if answer.get("answer_blanks") else None,
                    answer.get("answer_option"), answer.get("answer_x_pct"), answer.get("answer_y_pct"),
                    pq["marks_earned"], pq["marks_possible"], pq["feedback"],
                ),
            )

        conn.execute(
            "UPDATE paper_attempts SET submitted_at = ?, time_taken_seconds = ?, "
            "auto_submitted = ?, total_score = ?, max_score = ? WHERE id = ?",
            (now, time_taken, auto_submitted, grading_result["total_score"],
             grading_result["max_score"], req.attempt_id),
        )
        conn.commit()

    return {
        "success": True,
        "total_score": grading_result["total_score"],
        "max_score": grading_result["max_score"],
        "per_question": grading_result["per_question"],
        "time_taken_seconds": time_taken,
        "auto_submitted": bool(auto_submitted),
    }


@app.get("/papers/attempt_result")
def get_paper_attempt_result(attempt_id: int, username: str, x_bridge_secret: Optional[str] = Header(None)):
    """For re-viewing a past attempt's results screen without re-grading."""
    _require_secret(x_bridge_secret)
    username = username.strip().lower()

    with contextlib.closing(_get_conn()) as conn:
        attempt = conn.execute(
            "SELECT id, paper_id, username, mode, timer_seconds, time_taken_seconds, "
            "started_at, submitted_at, auto_submitted, total_score, max_score "
            "FROM paper_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()

        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")
        if attempt["username"] != username:
            raise HTTPException(status_code=403, detail="This attempt belongs to a different user")

        answers = conn.execute(
            "SELECT question_id, answer_text, answer_blanks, answer_option, "
            "answer_x_pct, answer_y_pct, score, max_score, ai_feedback "
            "FROM paper_answers WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchall()

    return {
        "attempt_id": attempt["id"], "paper_id": attempt["paper_id"], "mode": attempt["mode"],
        "total_score": attempt["total_score"], "max_score": attempt["max_score"],
        "time_taken_seconds": attempt["time_taken_seconds"], "auto_submitted": bool(attempt["auto_submitted"]),
        "answers": [
            {
                "question_id": a["question_id"], "answer_text": a["answer_text"],
                "answer_blanks": json.loads(a["answer_blanks"]) if a["answer_blanks"] else None,
                "answer_option": a["answer_option"], "answer_x_pct": a["answer_x_pct"],
                "answer_y_pct": a["answer_y_pct"], "score": a["score"], "max_score": a["max_score"],
                "feedback": a["ai_feedback"],
            }
            for a in answers
        ],
    }


# ---------------------------------------------------------------------
# Ollama proxy endpoints (paywalled: requires active subscription)
# ---------------------------------------------------------------------

OLLAMA_BASE_URL = "http://127.0.0.1:11434"


@app.get("/ollama/health")
def ollama_health(x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    try:
        r = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=10
        )
        r.raise_for_status()
        return {
            "status": "ok",
            "ollama_running": True
        }
    except Exception as e:
        return {
            "status": "error",
            "ollama_running": False,
            "error": str(e)
        }


@app.get("/ollama/tags")
def ollama_tags(x_bridge_secret: Optional[str] = Header(None)):
    _require_secret(x_bridge_secret)

    try:
        r = requests.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=30
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.post("/ollama/generate")
def ollama_generate(
    req: OllamaGenerateRequest,
    x_bridge_secret: Optional[str] = Header(None)
):
    _require_secret(x_bridge_secret)
    _require_active_subscription(req.username)
    # Was hardcoded to "ai_chat" regardless of what the caller was actually
    # doing (quiz generation, paper generation, etc). Now trusts the
    # caller-declared feature, falling back to "ai_chat" only if omitted.
    _require_tier(req.username, req.feature or "ai_chat")

    payload = {
        "model": req.model,
        "prompt": req.prompt,
        "stream": False,
    }

    if req.system:
        payload["system"] = req.system

    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=300
        )
        r.raise_for_status()
        return r.json()

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ollama request failed: {e}"
        )


@app.post("/ollama/chat")
def ollama_chat(
    req: OllamaChatRequest,
    x_bridge_secret: Optional[str] = Header(None)
):
    _require_secret(x_bridge_secret)
    _require_active_subscription(req.username)
    # Was hardcoded to "ai_chat" regardless of what the caller was actually
    # doing -- this is the root cause of quiz_generation/flashcards tier
    # gates never actually applying. Now trusts the caller-declared
    # feature, falling back to "ai_chat" only if omitted (old callers).
    _require_tier(req.username, req.feature or "ai_chat")

    payload = {
        "model": req.model,
        "messages": req.messages,
        "stream": False,
    }
    # Forwarded only if the caller actually set them -- omitting the
    # keys entirely when unset (rather than sending options=null) keeps
    # this a no-op for every existing caller that doesn't pass them yet,
    # matching Ollama's own "omit for default" convention for these
    # fields.
    if req.options:
        payload["options"] = req.options
    if req.format:
        payload["format"] = req.format

    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=300
        )
        r.raise_for_status()
        return r.json()

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ollama request failed: {e}"
        )

@app.post("/ollama/embed")
def ollama_embed(
    req: OllamaEmbedRequest,
    x_bridge_secret: Optional[str] = Header(None)
):
    _require_secret(x_bridge_secret)
    _require_active_subscription(req.username)
    # Deliberately NOT calling _require_tier(req.username, "ai_chat") here.
    # Embeddings power RAG indexing (chunking a user's own uploaded docs
    # into ChromaDB), not a chat/question-generation call the user
    # perceives as "using their daily AI quota." Gating this behind the
    # same tier check as chat would silently burn a Free user's daily cap
    # just from opening the sidebar and triggering a vector store rebuild.
    # If you decide indexing SHOULD count against tier limits later, add
    # the _require_tier call back with its own check name (e.g. "rag_index").

    payload = {
        "model": req.model,
        "input": req.input,
    }

    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json=payload,
            timeout=300
        )
        r.raise_for_status()
        return r.json()

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ollama request failed: {e}"
        )

@app.get("/debug/db_path")
def debug_db_path():
    """
    TEMPORARY -- added to diagnose a real issue where reset_password.py
    and storage_bridge.py appeared to be reading/writing different
    database files due to relative-path resolution depending on the
    working directory each script was launched from. Safe to delete
    once that's confirmed fixed; doesn't expose anything sensitive
    (no secrets, no user data, just paths and a file size).
    """
    return {
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else None,
        "script_location": str(Path(__file__).resolve().parent),
    }

import secrets
from datetime import datetime, timezone
from fastapi import Header, HTTPException
from pydantic import BaseModel

class SessionCreateRequest(BaseModel):
    username: str
    device_info: str | None = None

class SessionValidateRequest(BaseModel):
    username: str
    session_token: str

def _check_secret(x_bridge_secret: str = Header(...)):
    if x_bridge_secret != os.environ.get("BRIDGE_SHARED_SECRET"):
        raise HTTPException(status_code=401, detail="invalid bridge secret")

@app.post("/session/create")
def create_session(req: SessionCreateRequest, _=Depends(_check_secret)):
    """Called on login. Issues a fresh token and invalidates any prior session."""
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET session_token = ?, session_updated_at = ?, session_device_info = ? WHERE username = ?",
        (token, now, req.device_info, req.username),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="user not found")
    conn.commit()
    conn.close()

    return {"session_token": token, "issued_at": now}

@app.post("/session/validate")
def validate_session(req: SessionValidateRequest, _=Depends(_check_secret)):
    """Called periodically by the client to check if this session is still the active one."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT session_token FROM users WHERE username = ?", (req.username,))
    row = cur.fetchone()
    conn.close()

    if row is None:
        return {"valid": False, "reason": "user_not_found"}

    stored_token = row[0]
    if stored_token is None:
        return {"valid": False, "reason": "no_active_session"}

    if stored_token != req.session_token:
        return {"valid": False, "reason": "superseded_by_other_device"}

    return {"valid": True}

@app.post("/session/logout")
def logout_session(req: SessionValidateRequest, _=Depends(_check_secret)):
    """Explicit logout — clears the active session so no token is valid."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET session_token = NULL WHERE username = ? AND session_token = ?",
        (req.username, req.session_token),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/health")
def health():
    """Simple endpoint to confirm the bridge is reachable (no secret required)."""
    return {"status": "ok", "service": "scholarai-storage-bridge"}