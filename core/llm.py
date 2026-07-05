"""
core/llm.py
===========
Lazy-loaded LLM access and the shared internet/image search tools.

CHANGE LOG (this revision) -- WHY THIS FILE CHANGED:
  Previously, get_llm() returned a langchain_ollama.ChatOllama instance
  built with `base_url=OLLAMA_BASE_URL`, which talks DIRECTLY to Ollama's
  native HTTP API. That request has no concept of "username" and never
  passes through storage_bridge.py's /ollama/chat or /ollama/generate
  routes -- which is where _require_active_subscription() and
  _require_tier() actually live. The result: every AI feature (chat,
  quiz generation, question papers) completely bypassed subscription
  and tier enforcement. Toggling a student's tier or active status in
  the admin GUI had zero effect on whether they could use AI features,
  because the enforcement code was never in the request path.

  This revision replaces ChatOllama with BridgeChatLLM, a minimal shim
  that exposes the SAME .invoke(prompt) -> response.content interface
  your features/*.py code already expects, but internally calls
  bridge_client.ollama_chat(), which DOES enforce tier/subscription on
  the bridge side before proxying to Ollama.

  THE ONE UNAVOIDABLE CHANGE AT CALL SITES: because the bridge's gate
  is keyed on username, every call to get_llm() and invoke_with_timeout()
  now needs a `username` passed in. Search your codebase for:
      get_llm(          -> now get_llm(model_type=..., username=...)
      invoke_with_timeout(  -> now needs username= too
  and update each call site accordingly. Without a username, the shim
  cannot ask the bridge whether this request is even allowed.
"""
import threading
from typing import Optional
from langchain_community.tools import DuckDuckGoSearchRun
from ddgs import DDGS

import bridge_client
from bridge_client import BridgeRequestError, BridgeUnavailableError
from config import OLLAMA_MAIN_MODEL

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


class _LLMResponse:
    """Minimal stand-in for langchain's AIMessage, so callers that do
    `response = llm.invoke(prompt); text = response.content` keep working
    unchanged."""
    __slots__ = ("content",)

    def __init__(self, content: str):
        self.content = content


class BridgeChatLLM:
    """
    Drop-in replacement for ChatOllama's .invoke(prompt) -> response.content
    interface, EXCEPT calls now go through storage_bridge.py's /ollama/chat
    route (via bridge_client.ollama_chat()) instead of straight to Ollama.
    This is what makes _require_active_subscription() and _require_tier()
    actually run before any inference happens.

    Raises BridgeRequestError if the bridge rejects the request (inactive
    subscription -> 402, tier too low -> 403, daily cap hit -> 429) --
    callers should catch this and show req.detail to the user, same as
    they already do for other bridge_client calls elsewhere in the app.
    Raises BridgeUnavailableError if the bridge itself can't be reached.
    """

    def __init__(self, model: str, username: str, system: Optional[str] = None,
                 num_predict: Optional[int] = None):
        self.model = model
        self.username = username
        self.system = system
        # num_predict is accepted for API-compatibility with the old
        # get_llm() call sites (quiz/paper generation passed this to
        # ChatOllama to bound response length) but isn't currently
        # forwarded -- storage_bridge.py's /ollama/chat route doesn't
        # accept options/num_predict yet. If you need this enforced,
        # add an `options` field to OllamaChatRequest in storage_bridge.py
        # and thread it through bridge_client.ollama_chat().
        self.num_predict = num_predict

    def invoke(self, prompt: str) -> _LLMResponse:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})

        result = bridge_client.ollama_chat(
            username=self.username,
            model=self.model,
            messages=messages,
        )
        # storage_bridge.py's /ollama/chat proxies Ollama's own response
        # shape verbatim: {"message": {"role": "assistant", "content": "..."}, ...}
        content = result.get("message", {}).get("content", "")
        return _LLMResponse(content)


def invoke_with_timeout(llm: "BridgeChatLLM", prompt: str, timeout_seconds: int = 60) -> Optional[str]:
    """
    Call BridgeChatLLM.invoke() with a timeout.

    NOTE: the timeout here is a client-side thread-join safety net only
    (same caveat as before: a bare thread.join() can't forcibly kill a
    stuck thread, it just stops waiting for it). The bridge's own HTTP
    call to Ollama has its own timeout (LLM_REQUEST_TIMEOUT in
    bridge_client.py, currently 300s) which is the one that actually
    aborts the upstream connection.

    Returns the response content, or None on timeout/error. On a
    BridgeRequestError (tier too low, inactive subscription, daily cap
    hit), re-raises it rather than swallowing it as None -- the caller
    needs to distinguish "Ollama is slow" from "this user isn't allowed
    to do this" so it can show the right message.
    """
    result = {"content": None, "error": None}

    def invoke_thread():
        try:
            response = llm.invoke(prompt)
            result["content"] = response.content
        except (BridgeRequestError, BridgeUnavailableError):
            raise
        except Exception as e:
            result["error"] = e

    thread = threading.Thread(target=invoke_thread, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds + 5)

    if thread.is_alive():
        print(f"[ScholarAI] LLM timeout after {timeout_seconds}s — bridge/Ollama may be stuck or overloaded")
        return None

    if result["error"]:
        print(f"[ScholarAI] LLM error: {type(result['error']).__name__}: {result['error']}")
        return None

    return result["content"]


def get_llm(model_type: str = "main", username: str = None, request_timeout: float = 60.0):
    """
    Get a BridgeChatLLM instance configured for the specified task.
    ALL inference now goes through storage_bridge.py's /ollama/chat route,
    which enforces subscription + tier before proxying to Ollama.

    Args:
        model_type: "main" for standard inference, "quiz" for quiz generation,
            "paper" for question-paper generation (one question type per call).
        username: REQUIRED. The bridge's tier/subscription gate is keyed on
            this. Every existing call site that previously did
            `get_llm("quiz")` etc. now needs `get_llm("quiz", username=username)`.
        request_timeout: currently unused directly here (the bridge enforces
            its own upstream timeout) -- kept as a parameter for call-site
            compatibility; pass through to invoke_with_timeout()'s
            timeout_seconds instead.

    Returns:
        BridgeChatLLM instance, or None if username is missing (fails
        loudly via a printed error rather than silently making an
        unauthenticated call).
    """
    if not username:
        print("[ScholarAI] get_llm() called without a username -- cannot route through the "
              "bridge's tier/subscription check. Every call site must now pass username=.")
        return None

    try:
        num_predict = None
        if model_type == "quiz":
            # Sized for a SINGLE CHUNK's worth of questions (chunked quiz
            # generation sends many small requests rather than one giant
            # one -- see features/chat_graph.py).
            num_predict = 1024
        elif model_type == "paper":
            # Question papers generate ONE QUESTION TYPE AT A TIME (see
            # features/question_paper_generator.py) -- 3072 tokens covers
            # up to MAX_COUNT_PER_TYPE (25) questions of a single type.
            num_predict = 3072

        return BridgeChatLLM(model=OLLAMA_MAIN_MODEL, username=username, num_predict=num_predict)
    except Exception as e:
        print(f"[ScholarAI] Error setting up bridge-routed LLM: {e}")
        return None
