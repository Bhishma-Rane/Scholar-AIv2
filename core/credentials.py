"""
core/credentials.py
=====================
Username/password handling for the login gate.

CHANGED: credentials no longer live in a local JSON file on Streamlit
Cloud's ephemeral filesystem (which was wiped every time the container
recycled, silently deleting every account). They now live on the
storage bridge running on Bhishma's Windows laptop (see
core/bridge_client.py and storage_bridge.py), which persists indefinitely.

The public function signatures (create_account, user_exists,
verify_password) are UNCHANGED on purpose, so ui/auth.py and anywhere
else that calls them needs no changes beyond what's needed for the new
token-based auto-login (see issue_login_token / verify_login_token below,
which are new additions, not replacements).

Password hashing itself (PBKDF2-HMAC-SHA256, per-user random salt,
constant-time comparison) still happens — it just happens on the bridge
server now instead of in this process. See storage_bridge.py for the
actual hashing code, which is identical to what used to live here.
"""
from core.bridge_client import (
    create_account as _bridge_create_account,
    user_exists as _bridge_user_exists,
    verify_password as _bridge_verify_password,
    issue_login_token as _bridge_issue_login_token,
    verify_login_token as _bridge_verify_login_token,
    revoke_login_token as _bridge_revoke_login_token,
    BridgeUnavailableError,
)

# Re-exported so callers can catch this without importing bridge_client directly.
__all__ = [
    "create_account",
    "user_exists",
    "verify_password",
    "issue_login_token",
    "verify_login_token",
    "revoke_login_token",
    "BridgeUnavailableError",
]


def user_exists(username: str) -> bool:
    return _bridge_user_exists(username)


def create_account(username: str, password: str) -> bool:
    """
    Creates a new account on the storage bridge. Returns False if the
    username is already taken (caller should treat that as "use login
    instead, not signup").
    """
    return _bridge_create_account(username, password)


def verify_password(username: str, password: str) -> bool:
    """Returns True only if the username exists AND the password matches."""
    return _bridge_verify_password(username, password)


def issue_login_token(username: str, password: str):
    """
    Verifies credentials and, on success, returns a long-lived token that
    can be stored in the browser's URL query params to enable auto-login
    on a future page reload (see ui/auth.py). Returns None on bad credentials.
    """
    return _bridge_issue_login_token(username, password)


def verify_login_token(token: str):
    """Returns the username if the token is valid and unexpired, else None."""
    return _bridge_verify_login_token(token)


def revoke_login_token(token: str) -> None:
    """Invalidates a login token — called on logout."""
    _bridge_revoke_login_token(token)
