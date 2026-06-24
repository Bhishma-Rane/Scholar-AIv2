"""
core/llm.py
===========
Lazy-loaded LLM access and the shared internet/image search tools.
Centralizing this means model names/params are changed in one place,
and a downed Ollama instance fails gracefully instead of crashing imports.
"""
import threading
from typing import Optional
from langchain_ollama import ChatOllama
from langchain_community.tools import DuckDuckGoSearchRun
from ddgs import DDGS
from config import OLLAMA_MAIN_MODEL, OLLAMA_BASE_URL

# Single shared search tool instance.
internet_search = DuckDuckGoSearchRun()


def search_images(query: str, max_results: int = 4) -> list:
    """
    Free, no-API-key image search via DuckDuckGo. Used for "VISUAL" diagram
    requests (e.g. "the human heart") where a real labeled image is needed
    rather than a Mermaid diagram.
    Returns a list of {"title": str, "image_url": str, "source_url": str}.
    Returns an empty list on any failure rather than raising, since this
    is a best-effort enhancement, not a critical path.
    """
    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.images(query, max_results=max_results))
        return [
            {
                "title": r.get("title", query),
                "image_url": r.get("image", ""),
                "source_url": r.get("url", ""),
            }
            for r in raw_results
            if r.get("image")
        ]
    except Exception:
        return []


def invoke_with_timeout(llm: ChatOllama, prompt: str, timeout_seconds: int = 60) -> Optional[str]:
    """
    Call ChatOllama.invoke() with a timeout.

    IMPORTANT: the real timeout enforcement lives in the httpx client itself
    (see client_kwargs={"timeout": ...} in get_llm() below). httpx's timeout
    actually aborts the underlying TCP connection to Ollama when exceeded,
    which frees up Ollama's inference slot immediately.

    This thread wrapper is a secondary safety net only — it guards against
    edge cases where the httpx timeout doesn't cleanly surface as an
    exception (e.g. certain ngrok-layer hangs). It is NOT the primary
    cancellation mechanism. A bare thread.join(timeout=...) on its own
    cannot kill a running thread; without the httpx-level timeout, the
    abandoned call would keep running on the server and occupy Ollama's
    single inference slot indefinitely, causing every subsequent call to
    queue up and get progressively slower.

    Returns the response content, or None on timeout/error.
    """
    result = {"content": None, "error": None}

    def invoke_thread():
        try:
            response = llm.invoke(prompt)
            result["content"] = response.content
        except Exception as e:
            result["error"] = e

    thread = threading.Thread(target=invoke_thread, daemon=True)
    thread.start()
    # Small buffer over the httpx timeout, since httpx should fire first
    # and unwind invoke_thread() cleanly before this join() would expire.
    thread.join(timeout=timeout_seconds + 5)

    if thread.is_alive():
        print(f"[ScholarAI] LLM timeout after {timeout_seconds}s — Ollama may be stuck or overloaded")
        return None

    if result["error"]:
        print(f"[ScholarAI] LLM error: {type(result['error']).__name__}: {result['error']}")
        return None

    return result["content"]


def get_llm(model_type: str = "main", request_timeout: float = 60.0):
    """
    Get a ChatOllama instance configured for the specified task.

    Args:
        model_type: "main" for standard inference, "quiz" for quiz generation
        request_timeout: seconds before the underlying httpx client aborts
            the request. This is the REAL timeout — it actually cancels the
            TCP connection to Ollama, unlike a bare Python thread timeout.

    Returns:
        ChatOllama instance or None if connection fails.
    """
    try:
        common_kwargs = dict(
            model=OLLAMA_MAIN_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,
            top_p=0.2,
            client_kwargs={
                "headers": {"ngrok-skip-browser-warning": "true"},
                "timeout": request_timeout,
            },
        )
        if model_type == "quiz":
            # num_predict sized for a SINGLE CHUNK's worth of questions
            # (chunked quiz generation sends many small requests rather than
            # one giant one — see features/chat_graph.py). 1024 tokens is
            # comfortably enough for a handful of questions per chunk while
            # keeping worst-case generation time low.
            return ChatOllama(**common_kwargs, num_ctx=8192, num_predict=1024)
        return ChatOllama(**common_kwargs, num_ctx=8192)
    except Exception as e:
        print(f"[ScholarAI] Error connecting to Ollama: {e}")
        return None
