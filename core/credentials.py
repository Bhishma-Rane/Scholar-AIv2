"""
core/credentials.py
=====================
Username/password storage for the login gate.

Design notes:
- Credentials live in a single JSON file at users/_credentials.json,
  separate from any individual user's data folder.
- Passwords are NEVER stored in plain text. Each password gets a unique
  random salt (via hashlib.pbkdf2_hmac), so two users with the same
  password produce different stored hashes, and the hash can't be
  reversed or directly compared without redoing the same slow KDF work.
- This is a file-based store appropriate for a small number of users
  (single-machine / small-team deployments). See the "production
  readiness" notes for when to swap this for a real database.
"""
import os
import json
import hmac
import hashlib
import secrets

from config import USERS_DIR

CREDENTIALS_FILE = os.path.join(USERS_DIR, "_credentials.json")

# PBKDF2 tuning. 260,000 iterations is the (then-)current OWASP-recommended
# minimum for PBKDF2-HMAC-SHA256 as of this writing — re-check periodically
# and bump upward as hardware gets faster.
PBKDF2_ITERATIONS = 260_000
SALT_BYTES = 16


def _load_credentials() -> dict:
    """Returns the {username: {salt, hash}} dict, or {} if the file doesn't exist yet."""
    if not os.path.exists(CREDENTIALS_FILE):
        return {}
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupted or unreadable file: fail safe to "no accounts" rather than crashing.
        return {}


def _save_credentials(data: dict) -> None:
    os.makedirs(USERS_DIR, exist_ok=True)
    # Write to a temp file then replace, so a crash mid-write can't corrupt the store.
    tmp_path = CREDENTIALS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CREDENTIALS_FILE)


def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return derived.hex()


def user_exists(username: str) -> bool:
    return username in _load_credentials()


def create_account(username: str, password: str) -> bool:
    """
    Creates a new account. Returns False if the username is already taken
    (caller should treat that as "use login instead, not signup").
    """
    creds = _load_credentials()
    if username in creds:
        return False

    salt = secrets.token_bytes(SALT_BYTES)
    creds[username] = {
        "salt": salt.hex(),
        "hash": _hash_password(password, salt),
    }
    _save_credentials(creds)
    return True


def verify_password(username: str, password: str) -> bool:
    """Returns True only if the username exists AND the password matches."""
    creds = _load_credentials()
    record = creds.get(username)
    if record is None:
        return False

    salt = bytes.fromhex(record["salt"])
    expected_hash = record["hash"]
    candidate_hash = _hash_password(password, salt)

    # Constant-time comparison to avoid leaking timing information about
    # how many characters of the hash matched.
    return hmac.compare_digest(candidate_hash, expected_hash)
