"""
core/bridge_client.py
======================
Streamlit Cloud's side of the storage bridge. Every function here makes
an HTTP call to storage_bridge.py running on Bhishma's Windows laptop
(reached via its own ngrok tunnel — separate from the Ollama tunnel).

This is the ONLY module that should know about the bridge's URL/secret
or its HTTP details. Everything else (core/credentials.py, ui/sidebar.py,
ui/auth.py) should call these functions and not touch `requests` directly,
so the bridge's transport details stay swappable in one place.

All functions fail SAFE-but-LOUD: if the bridge is unreachable (laptop
off, tunnel down, wrong URL), they raise BridgeUnavailableError rather
than silently returning empty/false results, since "no accounts exist"
and "can't reach the credential store" must never look the same to the
caller — confusing those two would either lock everyone out or make
account creation seem to fail when it's really a connectivity problem.

BridgeRequestError (added alongside the password-reset feature) is the
other failure mode: the bridge WAS reached and responded, but rejected
this specific request as invalid (expired/used/bad token, password too
short, etc.). Carries the bridge's own .detail message, which is safe to
show directly in the UI. This is distinct from BridgeUnavailableError on
purpose — one means "try again later, something's down", the other means
"this specific thing you typed was wrong, here's why."
"""
import requests

from config import BRIDGE_BASE_URL, BRIDGE_SHARED_SECRET

REQUEST_TIMEOUT = 15  # seconds — bridge calls are small JSON/file ops, should be fast


class BridgeUnavailableError(Exception):
    """Raised when the storage bridge can't be reached at all (network/DNS/timeout),
    as opposed to a normal application-level failure like 'wrong password'."""
    pass


class BridgeRequestError(Exception):
    """
    Raised when the bridge is reachable and responded, but rejected the
    request as invalid (4xx other than a bad shared secret) -- e.g. an
    expired or already-used password reset token. Carries the bridge's
    own detail message, which is safe to show directly to the user.
    """
    def __init__(self, detail: str, status_code: int):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _headers() -> dict:
    return {"x-bridge-secret": BRIDGE_SHARED_SECRET}


def _post(path: str, **kwargs) -> dict:
    try:
        resp = requests.post(
            f"{BRIDGE_BASE_URL}{path}",
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.exceptions.RequestException as e:
        raise BridgeUnavailableError(f"Could not reach storage bridge at {path}: {e}") from e

    if resp.status_code == 401:
        raise BridgeUnavailableError(
            "Storage bridge rejected the request (bad shared secret). "
            "Check BRIDGE_SHARED_SECRET matches on both sides."
        )

    # Any other 4xx means the bridge understood the request but rejected
    # it as invalid (e.g. an expired password reset token) -- raise
    # BridgeRequestError with the bridge's own message instead of letting
    # raise_for_status() below throw an opaque HTTPError. None of the
    # existing routes (create_account, verify_password, etc.) ever return
    # a 4xx for normal "wrong answer" cases -- they return {"success":
    # False} / {"valid": False} in a 200 instead -- so this only starts
    # mattering for reset_password() below.
    if 400 <= resp.status_code < 500:
        try:
            detail = resp.json().get("detail", f"Request failed ({resp.status_code})")
        except ValueError:
            detail = f"Request failed ({resp.status_code})"
        raise BridgeRequestError(detail, resp.status_code)

    resp.raise_for_status()
    return resp.json()


def _get(path: str, **kwargs) -> dict:
    try:
        resp = requests.get(
            f"{BRIDGE_BASE_URL}{path}",
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.exceptions.RequestException as e:
        raise BridgeUnavailableError(f"Could not reach storage bridge at {path}: {e}") from e

    if resp.status_code == 401:
        raise BridgeUnavailableError(
            "Storage bridge rejected the request (bad shared secret). "
            "Check BRIDGE_SHARED_SECRET matches on both sides."
        )

    if 400 <= resp.status_code < 500:
        try:
            detail = resp.json().get("detail", f"Request failed ({resp.status_code})")
        except ValueError:
            detail = f"Request failed ({resp.status_code})"
        raise BridgeRequestError(detail, resp.status_code)

    resp.raise_for_status()
    return resp.json()


def is_bridge_reachable() -> bool:
    try:
        resp = requests.get(f"{BRIDGE_BASE_URL}/health", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def create_account(username: str, password: str) -> bool:
    result = _post("/auth/create_account", json={"username": username, "password": password})
    return result["success"]


def user_exists(username: str) -> bool:
    result = _post("/auth/user_exists", json={"username": username})
    return result["exists"]


def verify_password(username: str, password: str) -> bool:
    result = _post("/auth/verify_password", json={"username": username, "password": password})
    return result["valid"]


def issue_login_token(username: str, password: str):
    result = _post("/auth/issue_token", json={"username": username, "password": password})
    return result["token"] if result["valid"] else None


def verify_login_token(token: str):
    result = _post("/auth/verify_token", json={"token": token})
    return result["username"] if result["valid"] else None


def revoke_login_token(token: str) -> None:
    _post("/auth/revoke_token", json={"token": token})


# ---------------------------------------------------------------------
# Password reset (admin-issued -- see reset_password.py on Bhishma's
# machine, which generates the token a student is given out-of-band)
# ---------------------------------------------------------------------
def reset_password(username: str, token: str, new_password: str) -> None:
    """
    Applies an admin-issued password reset token. Raises BridgeRequestError
    with a human-readable .detail (e.g. "This token has expired") if the
    token is invalid/expired/already used, or if the new password fails
    the bridge's own minimum-length check. Raises BridgeUnavailableError
    if the bridge can't be reached at all. Returns None on success.
    """
    _post(
        "/auth/reset_password",
        json={"username": username, "token": token, "new_password": new_password},
    )


# ---------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------
def create_subject(username: str, subject: str) -> None:
    _post("/subjects/create", data={"username": username, "subject": subject})


def list_subjects(username: str) -> list:
    result = _get("/subjects/list", params={"username": username})
    return result["subjects"]


def delete_subject(username: str, subject: str) -> None:
    _post("/subjects/delete", data={"username": username, "subject": subject})


# ---------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------
def upload_file(username: str, subject: str, filename: str, file_bytes: bytes) -> str:
    """Returns the (possibly sanitized) filename the bridge actually stored it as."""
    result = _post(
        "/files/upload",
        data={"username": username, "subject": subject},
        files={"file": (filename, file_bytes)},
    )
    return result["filename"]


def list_files(username: str, subject: str) -> list:
    result = _get("/files/list", params={"username": username, "subject": subject})
    return result["files"]


def download_file(username: str, subject: str, filename: str) -> bytes:
    """Fetches the raw bytes of a single stored file (used when rebuilding
    ChromaDB locally on Streamlit Cloud after a cold start)."""
    resp = requests.get(
        f"{BRIDGE_BASE_URL}/files/download",
        headers=_headers(),
        params={"username": username, "subject": subject, "filename": filename},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        raise BridgeUnavailableError("Storage bridge rejected the request (bad shared secret).")
    resp.raise_for_status()
    return resp.content


def delete_file(username: str, subject: str, filename: str) -> None:
    _post("/files/delete", data={"username": username, "subject": subject, "filename": filename})
