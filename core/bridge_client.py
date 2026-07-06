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
"""
import requests

from config import BRIDGE_BASE_URL, BRIDGE_SHARED_SECRET

REQUEST_TIMEOUT = 15  # seconds — bridge calls are small JSON/file ops, should be fast
LLM_REQUEST_TIMEOUT = 300  # seconds — chat/generate calls proxy through to Ollama and can run long


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
# on the bridge side -- see storage_bridge.py's /ollama/chat and
# /ollama/generate routes). ALL LLM calls from the app must go through
# these two functions, not straight to Ollama, or the tier/subscription
# system has no effect (this was the root cause of the tier bug).
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

"""
core/vectorstore.py
====================
Per-subject Chroma vector store management, plus raw chapter text retrieval
(used by features that need the full, exact text rather than embedded chunks).

CHANGED (this revision): embeddings now route through storage_bridge.py's
/ollama/embed route instead of calling Ollama directly via
langchain_ollama.OllamaEmbeddings. The old direct path
(OllamaEmbeddings(base_url=OLLAMA_BASE_URL) -> .../ollama/api/embed
through the tunnel) was a leftover bypass: path_proxy.py's "/ollama"
route was removed specifically to force all AI calls through the
bridge's subscription checks (see path_proxy.py's change log), but this
file was never updated to match -- it kept hitting the now-deleted
route, which is why get_vector_store(force_rebuild=True) started
404ing with "No route configured for path: /ollama/api/embed".

BridgeEmbeddings below is the embeddings-side counterpart to
core/llm.py's BridgeChatLLM, using core/llm.py's embed_texts() (which
itself calls bridge_client.ollama_embed()) instead of talking to
Ollama directly.

CHANGED (earlier revision): source PDFs/TXT files no longer live in a
persistent local folder (paths["subject_source"] no longer exists —
see core/paths.py). They live on the storage bridge now. Both
functions in this file fetch the relevant file(s) from the bridge into
a TEMPORARY scratch directory first, then use the existing
PyPDFLoader/TextLoader exactly as before — those loader classes need
real file paths, so this is the minimal change that keeps everything
downstream (chunking, embeddings, Chroma) untouched.

The temp scratch directory is cleaned up after use. ChromaDB itself
(paths["chroma"]) is still rebuilt and cached locally on Streamlit
Cloud's container disk — that part is unchanged. It just gets rebuilt
from bridge-fetched files instead of a local "sources" folder.

FIX (readonly database on force_rebuild): chromadb caches a process-level
System client keyed by persist_directory path (see
chromadb.api.client.SharedSystemClient). Since Streamlit Cloud reuses the
same worker process across reruns, doing shutil.rmtree() on chroma_db_dir
and then immediately reopening Chroma at the SAME path returns a stale
cached client pointing at a now-deleted sqlite file, causing
"attempt to write a readonly database" (SQLITE_READONLY_DBMOVED) on the
next write. Clearing the cache right after rmtree forces a genuinely
fresh client to be opened. See get_vector_store() below.
"""
import os
import shutil
import tempfile

from chromadb.api.client import SharedSystemClient
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import OLLAMA_EMBED_MODEL
from core.paths import get_user_paths, sanitize_filename
from core import bridge_client
from core.bridge_client import BridgeUnavailableError
from core.llm import embed_texts


class BridgeEmbeddings:
    """
    Drop-in replacement for langchain_ollama.OllamaEmbeddings that routes
    through storage_bridge.py's /ollama/embed route (via
    core.llm.embed_texts()) instead of calling Ollama directly -- mirrors
    BridgeChatLLM's fix for chat/generate. Implements the minimal
    Embeddings interface Chroma needs: embed_documents(list[str]) ->
    list[list[float]], embed_query(str) -> list[float].

    NOT a LangChain Embeddings subclass -- like BridgeChatLLM, this is a
    plain object. Chroma only actually calls .embed_documents() and
    .embed_query(), both of which are implemented here, so it works as
    a duck-typed embedding_function without inheriting from anything.
    """

    def __init__(self, model: str, username: str):
        self.model = model
        self.username = username

    def embed_documents(self, texts: list) -> list:
        return embed_texts(username=self.username, texts=texts, model=self.model)

    def embed_query(self, text: str) -> list:
        return embed_texts(username=self.username, texts=[text], model=self.model)[0]


def _clear_local_cache(chroma_db_dir: str):
    shutil.rmtree(chroma_db_dir, ignore_errors=True)
    SharedSystemClient.clear_system_cache()


def _fetch_subject_files_to_temp_dir(username: str, subject: str) -> "tuple[str, list]":
    """
    Downloads every source file for a subject from the bridge into a
    fresh temp directory. Returns (temp_dir_path, list_of_local_file_paths).
    Caller is responsible for cleaning up temp_dir_path (shutil.rmtree)
    once done with it.
    """
    temp_dir = tempfile.mkdtemp(prefix="scholarai_src_")
    local_paths = []

    try:
        filenames = bridge_client.list_files(username, subject)
    except BridgeUnavailableError:
        # Bridge is down — return empty rather than crashing the whole
        # chat/quiz flow. Callers already handle "no documents" gracefully.
        return temp_dir, []

    for filename in filenames:
        try:
            file_bytes = bridge_client.download_file(username, subject, filename)
        except BridgeUnavailableError:
            continue  # skip this one file, try the rest
        local_path = os.path.join(temp_dir, filename)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        local_paths.append(local_path)

    return temp_dir, local_paths


def get_vector_store(username: str, subject: str, force_rebuild: bool = False):
    """
    Fix Issue #16, #17, #18, #19: Scoped Vector Store & Rebuilding.
    Loads (or builds) a Chroma vector store scoped to a single user+subject.
    Returns None if there are no source documents to embed.

    Source files are now fetched from the storage bridge into a temp
    directory before being loaded — see _fetch_subject_files_to_temp_dir.

    Embeddings go through BridgeEmbeddings (bridge-routed) rather than
    OllamaEmbeddings (direct-to-Ollama) -- see module docstring.
    """
    paths = get_user_paths(username, subject)
    chroma_db_dir = paths["chroma"]
    embeddings = BridgeEmbeddings(model=OLLAMA_EMBED_MODEL, username=username)

    if force_rebuild:
        _clear_local_cache(chroma_db_dir)

    if not force_rebuild and os.path.exists(chroma_db_dir) and os.listdir(chroma_db_dir):
        try:
            return Chroma(persist_directory=chroma_db_dir, embedding_function=embeddings)
        except Exception as e:
            print(f"[ScholarAI] Local Chroma cache unusable ({e}); rebuilding...")
            _clear_local_cache(chroma_db_dir)

    temp_dir, local_file_paths = _fetch_subject_files_to_temp_dir(username, subject)
    try:
        documents = []
        for local_path in local_file_paths:
            try:
                if local_path.lower().endswith(".pdf"):
                    documents.extend(PyPDFLoader(local_path).load())
                elif local_path.lower().endswith(".txt"):
                    documents.extend(TextLoader(local_path).load())
            except Exception:
                continue  # skip a single corrupt/unreadable file rather than failing the whole subject

        if not documents:
            return None

        chunks, metadatas = [], []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200, separators=["\n\n", "\n", ".", " ", ""]
        )
        for doc in documents:
            clean_chapter_name = sanitize_filename(
                os.path.basename(doc.metadata.get("source", "Unknown File")).rsplit(".", 1)[0]
            )
            doc_chunks = text_splitter.split_text(doc.page_content)
            chunks.extend(doc_chunks)
            metadatas.extend(
                [{"chapter": clean_chapter_name, "source": doc.metadata.get("source", "Unknown")}] * len(doc_chunks)
            )

        if chunks:
            try:
                return Chroma.from_texts(
                    texts=chunks, embedding=embeddings, metadatas=metadatas, persist_directory=chroma_db_dir
                )
            except Exception:
                _clear_local_cache(chroma_db_dir)
                raise
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_chapter_text(username: str, subject: str, chapter: str):
    """
    Returns the exact, full text of a chapter's source file (txt or pdf),
    rather than retrieving embedded chunks. Used wherever a feature needs
    the complete chapter content (e.g. flashcard/quiz generation).

    Fetches the matching file from the bridge into a temp location first,
    reads it, then cleans up — rather than reading a persistent local
    "sources" folder that no longer exists.
    """
    try:
        filenames = bridge_client.list_files(username, subject)
    except BridgeUnavailableError:
        return None

    safe_chapter = sanitize_filename(chapter).lower()
    matched_filename = next(
        (f for f in filenames if safe_chapter in sanitize_filename(f).lower() and f.endswith((".txt", ".pdf"))),
        None,
    )
    if not matched_filename:
        return None

    try:
        file_bytes = bridge_client.download_file(username, subject, matched_filename)
    except BridgeUnavailableError:
        return None

    temp_dir = tempfile.mkdtemp(prefix="scholarai_chap_")
    try:
        local_path = os.path.join(temp_dir, matched_filename)
        with open(local_path, "wb") as f:
            f.write(file_bytes)

        if local_path.lower().endswith(".txt"):
            with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        else:
            return "\n\n".join(page.page_content for page in PyPDFLoader(local_path).load())
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

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
