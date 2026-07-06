"""
core/bridge_client.py
======================
Streamlit Cloud's side of the storage bridge. Every function here makes
an HTTP call to storage_bridge.py running on Bhishma's Windows laptop
(reached via its own ngrok tunnel — separate from the Ollama tunnel).

This is the ONLY module that should know about the bridge's URL/secret
or its HTTP details. Everything else (core/credentials.py, ui/sidebar.py,
ui/auth.py, core/llm.py) should call these functions and not touch
`requests` directly, so the bridge's transport details stay swappable
in one place.

CHANGE LOG (this revision):
  - Added ollama_embed() -- routes embedding requests through
    storage_bridge.py's /ollama/embed route instead of calling Ollama
    directly. Mirrors ollama_chat()/ollama_generate() below, which fixed
    the same bypass for chat/generate calls. Without this, core/
    vectorstore.py's OllamaEmbeddings was hitting a path_proxy.py route
    ("/ollama/api/embed") that no longer exists, since path_proxy.py's
    direct "/ollama" route was removed on purpose to close the bypass --
    see path_proxy.py's own change log.
  - Added ollama_chat() / ollama_generate() -- these were previously
    MISSING, which meant core/llm.py was building a ChatOllama client
    pointed straight at OLLAMA_BASE_URL (raw Ollama), completely
    bypassing storage_bridge.py's /ollama/chat and /ollama/generate
    routes -- and therefore bypassing _require_active_subscription()
    and _require_tier() entirely. This is why tier/subscription changes
    made in the admin GUI had zero effect on actual AI usage: the
    enforcement code was never in the request path.
  - Added start_quiz_attempt() / submit_quiz_attempt() / get_usage_today()
    for the same reason -- storage_bridge.py already defined these
    routes, but nothing in this client called them.

All functions fail SAFE-but-LOUD: if the bridge is unreachable (laptop
off, tunnel down, wrong URL), they raise BridgeUnavailableError rather
than silently returning empty/false results, since "no accounts exist"
and "can't reach the credential store" must never look the same to the
caller — confusing those two would either lock everyone out or make
account creation seem to fail when it's really a connectivity problem.

BridgeRequestError (added alongside the password-reset feature) is the
other failure mode: the bridge WAS reached and responded, but rejected
this specific request as invalid (expired/used/bad token, password too
short, tier too low, daily cap hit, etc.). Carries the bridge's own
.detail message, which is safe to show directly in the UI. This is
distinct from BridgeUnavailableError on purpose — one means "try again
later, something's down", the other means "this specific thing you
typed/did was wrong, here's why."

NOTE: this file must NOT import from core.paths, core.vectorstore, or
anything that itself imports bridge_client -- core/paths.py imports
bridge_client at module load time, so any reverse import here creates
a circular import that crashes the app on startup. Keep this file
purely about HTTP transport to the bridge; file-system/path helpers
and document-loading logic (get_chapter_text, etc.) belong in
core/vectorstore.py, not here.
"""
import requests

from config import BRIDGE_BASE_URL, BRIDGE_SHARED_SECRET

REQUEST_TIMEOUT = 15  # seconds — bridge calls are small JSON/file ops, should be fast
LLM_REQUEST_TIMEOUT = 300  # seconds — chat/generate/embed calls proxy through to Ollama and can run long


class BridgeUnavailableError(Exception):
    """Raised when the storage bridge can't be reached at all (network/DNS/timeout),
    as opposed to a normal application-level failure like 'wrong password'."""
    pass


class BridgeRequestError(Exception):
    """
    Raised when the bridge is reachable and responded, but rejected the
    request as invalid (4xx other than a bad shared secret) -- e.g. an
    expired or already-used password reset token, an inactive
    subscription (402), a tier that's too low (403), or a daily usage
    cap that's been hit (429). Carries the bridge's own detail message,
    which is safe to show directly to the user.
    """
    def __init__(self, detail: str, status_code: int):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _headers() -> dict:
    return {"x-bridge-secret": BRIDGE_SHARED_SECRET}


def _post(path: str, **kwargs) -> dict:
    timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
    try:
        resp = requests.post(
            f"{BRIDGE_BASE_URL}{path}",
            headers=_headers(),
            timeout=timeout,
            **kwargs,
        )
    except requests.exceptions.RequestException as e:
        raise BridgeUnavailableError(f"Could not reach storage bridge at {BRIDGE_BASE_URL}{path}: {e}")

    if resp.status_code == 401:
        raise BridgeUnavailableError(
            "Storage bridge rejected the request (bad shared secret). "
            "Check BRIDGE_SHARED_SECRET matches on both sides."
        )

    # Any other 4xx means the bridge understood the request but rejected
    # it as invalid -- e.g. an expired password reset token, an inactive
    # subscription (402), a tier that's too low for this feature (403),
    # or a daily usage cap hit (429). Raise BridgeRequestError with the
    # bridge's own message instead of letting raise_for_status() below
    # throw an opaque HTTPError.
    if 400 <= resp.status_code < 500:
        try:
            detail = resp.json().get("detail", f"Request failed ({resp.status_code})")
        except ValueError:
            detail = f"Request failed ({resp.status_code})"
        raise BridgeRequestError(detail, resp.status_code)

    resp.raise_for_status()
    return resp.json()


def _get(path: str, **kwargs) -> dict:
    timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
    try:
        resp = requests.get(
            f"{BRIDGE_BASE_URL}{path}",
            headers=_headers(),
            timeout=timeout,
            **kwargs,
        )
    except requests.exceptions.RequestException as e:
        raise BridgeUnavailableError(f"Could not reach storage bridge at {BRIDGE_BASE_URL}{path}: {e}")

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
        timeout=60,
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


# ---------------------------------------------------------------------
# AI / LLM proxy (gated by _require_active_subscription + _require_tier
# on the bridge side -- see storage_bridge.py's /ollama/chat,
# /ollama/generate, and /ollama/embed routes). ALL LLM/embedding calls
# from the app must go through these functions, not straight to Ollama,
# or the tier/subscription system has no effect (this was the root
# cause of the tier bug).
# ---------------------------------------------------------------------
def ollama_chat(username: str, model: str, messages: list, options: dict = None, format: str = None) -> dict:
    """
    messages: list of {"role": "user"|"assistant"|"system", "content": str}
    Raises BridgeRequestError(status_code=402) if the account isn't active,
    403 if the tier is too low for "ai_chat", 429 if today's daily cap is
    hit. Returns the raw Ollama chat response dict (same shape Ollama's
    own /api/chat returns), e.g. result["message"]["content"].

    `options`, if given, is forwarded as-is to Ollama's /api/chat as its
    per-request generation params dict -- most usefully {"num_predict":
    N} to bound response length. `format`, if given (e.g. "json"),
    requests Ollama's native structured-JSON output mode. Both are
    omitted from the request body entirely when not set, so existing
    callers that don't pass them see no change in behavior.
    """
    payload = {"username": username, "model": model, "messages": messages}
    if options:
        payload["options"] = options
    if format:
        payload["format"] = format
    return _post(
        "/ollama/chat",
        json=payload,
        timeout=LLM_REQUEST_TIMEOUT,
    )


def ollama_generate(username: str, model: str, prompt: str, system: str = None) -> dict:
    """
    Same gating as ollama_chat(). Returns the raw Ollama generate response
    dict, e.g. result["response"].
    """
    payload = {"username": username, "model": model, "prompt": prompt}
    if system:
        payload["system"] = system
    return _post("/ollama/generate", json=payload, timeout=LLM_REQUEST_TIMEOUT)


def ollama_embed(username: str, model: str, input: list) -> dict:
    """
    input: list of strings to embed.
    Raises BridgeRequestError(status_code=402) if the account isn't active.
    Returns the raw Ollama embed response dict, e.g. result["embeddings"]
    (a list of float-vectors, one per input string, in the same order).

    No tier gate on the bridge side for this one (see storage_bridge.py's
    /ollama/embed route) -- embeddings power RAG indexing of the user's
    own uploaded docs, not a chat/generation call that should count
    against a daily "ai_chat" cap.
    """
    payload = {"username": username, "model": model, "input": input}
    return _post(
        "/ollama/embed",
        json=payload,
        timeout=LLM_REQUEST_TIMEOUT,
    )


def get_usage_today(username: str) -> dict:
    """Returns {"tier": str, "usage": {feature: {"used", "cap", "remaining"}}} --
    lets the UI show 'you've used 7/10 chat messages today'."""
    return _get("/usage/today", params={"username": username})


# ---------------------------------------------------------------------
# Quiz attempts (practice vs test mode, server-enforced timer + gating)
# ---------------------------------------------------------------------
def start_quiz_attempt(username: str, subject: str = None, mode: str = "practice",
                        timer_seconds: int = None) -> dict:
    """
    mode="test" requires the "test_mode" tier gate on the bridge and a
    positive timer_seconds. Raises BridgeRequestError(402/403) if the
    account is inactive or under-tiered for test mode.
    """
    payload = {"username": username, "mode": mode}
    if subject:
        payload["subject"] = subject
    if timer_seconds is not None:
        payload["timer_seconds"] = timer_seconds
    return _post("/quiz/start_attempt", json=payload)


def submit_quiz_attempt(attempt_id: int, username: str, score: float, max_score: float) -> dict:
    return _post(
        "/quiz/submit_attempt",
        json={"attempt_id": attempt_id, "username": username, "score": score, "max_score": max_score},
    )


# ---------------------------------------------------------------------
# Question Papers
# ---------------------------------------------------------------------
def list_papers(username: str, subject: str = None) -> list:
    """
    Returns the papers belonging to `username`, newest first. There's no
    publish/draft step anymore -- a paper is listed the moment it's created.
    """
    params = {"username": username}
    if subject:
        params["subject"] = subject
    result = _get("/papers/list", params=params)
    return result["papers"]


def create_paper(username: str, title: str, subject: str = None) -> dict:
    """Creates an empty paper shell (no sections/questions yet). Returns
    {"paper_id": int, "title": str, "subject": str|None}."""
    return _post("/papers/create", json={"username": username, "title": title, "subject": subject})


def add_paper_section(paper_id: int, title: str, instructions: str = None, order_index: int = 0) -> dict:
    return _post(
        "/papers/add_section",
        json={"paper_id": paper_id, "title": title, "instructions": instructions, "order_index": order_index},
    )


def add_paper_question(
    section_id: int,
    type: str,
    marks: float,
    order_index: int = 0,
    question_text: str = None,
    parent_question_id: int = None,
    extra: dict = None,
) -> dict:
    return _post(
        "/papers/add_question",
        json={
            "section_id": section_id,
            "type": type,
            "marks": marks,
            "order_index": order_index,
            "question_text": question_text,
            "parent_question_id": parent_question_id,
            "extra": extra,
        },
    )


def get_paper(paper_id: int) -> dict:
    return _get("/papers/get", params={"paper_id": paper_id})


def start_paper_attempt(username: str, paper_id: int, mode: str, timer_seconds: int = None) -> dict:
    """
    Requires the "question_paper" tier gate on the bridge (and "test_mode"
    too, if mode == "test"). Raises BridgeRequestError(402/403) if the
    account is inactive or under-tiered.
    """
    payload = {"username": username, "paper_id": paper_id, "mode": mode}
    if timer_seconds is not None:
        payload["timer_seconds"] = timer_seconds
    return _post("/papers/start_attempt", json=payload)


def submit_paper_attempt(attempt_id: int, username: str, answers: list,
                          lang: str = "English", negative_marking: bool = False) -> dict:
    """
    answers: list of dicts, each shaped like:
        {"question_id": int, "answer_text": str|None, "answer_blanks": list|None,
         "answer_option": str|None, "answer_x_pct": float|None, "answer_y_pct": float|None}
    Only the fields relevant to that question's type need to be set; the
    others can be omitted/None.
    """
    return _post(
        "/papers/submit_attempt",
        json={
            "attempt_id": attempt_id, "username": username, "answers": answers,
            "lang": lang, "negative_marking": negative_marking,
        },
    )


def get_paper_attempt_result(attempt_id: int, username: str) -> dict:
    return _get("/papers/attempt_result", params={"attempt_id": attempt_id, "username": username})


# ---------------------------------------------------------------------
# Theme color preference (persists per-account, follows them everywhere)
# ---------------------------------------------------------------------
def get_theme_color(username: str) -> str:
    result = _get("/users/theme_color", params={"username": username})
    return result["theme_color"]


def set_theme_color(username: str, theme_color: str) -> None:
    _post("/users/theme_color", json={"username": username, "theme_color": theme_color})


# ---------------------------------------------------------------------
# Tutorial-completed flag (persists per-account, same pattern as theme_color)
# ---------------------------------------------------------------------
def get_tutorial_completed(username: str) -> bool:
    result = _get("/users/tutorial_completed", params={"username": username})
    return bool(result["completed"])


def set_tutorial_completed(username: str, completed: bool) -> None:
    _post("/users/tutorial_completed", json={"username": username, "completed": completed})
