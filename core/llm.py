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
  with two entry points:
    - .invoke(prompt: str)          -- single flat string prompt, used by
                                        features/question_paper_generator.py
                                        and the quiz chunk generator.
    - .invoke_messages(messages)    -- a list of {"role", "content"} dicts
                                        (or LangChain BaseMessage objects),
                                        used by features/chat_graph.py so
                                        multi-turn chat_history is preserved.
  Both internally call bridge_client.ollama_chat(), which enforces
  tier/subscription on the bridge side before proxying to Ollama.

  IMPORTANT -- BridgeChatLLM is a plain Python object, NOT a LangChain
  Runnable. It does NOT support the `prompt | llm | parser` pipe syntax.
  Any code using LCEL piping with the LLM (chat_graph.py previously did)
  needs to be rewritten to build its messages list manually and call
  .invoke_messages() directly -- see chat_graph.py's generate_standard_modes
  for the pattern.

  THE ONE UNAVOIDABLE CHANGE AT CALL SITES: because the bridge's gate is
  keyed on username, every call to get_llm() now needs a `username`
  passed in. Search your codebase for `get_llm(` and update each call
  site. Without a username, get_llm() refuses to hand back an LLM at all
  (fails loudly rather than silently making an unauthenticated call).
"""
import threading
from typing import Optional, Union
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


def _normalize_message(msg) -> dict:
    """
    Accepts either a plain {"role": ..., "content": ...} dict or a
    LangChain BaseMessage (HumanMessage/AIMessage/SystemMessage), and
    returns the plain dict shape Ollama's /api/chat (and therefore
    bridge_client.ollama_chat) expects.
    """
    if isinstance(msg, dict):
        return {"role": msg["role"], "content": msg["content"]}

    # LangChain BaseMessage subclasses expose .type ("human"/"ai"/"system")
    # and .content.
    role_map = {"human": "user", "ai": "assistant", "system": "system"}
    role = role_map.get(getattr(msg, "type", ""), "user")
    return {"role": role, "content": msg.content}


class BridgeChatLLM:
    """
    Drop-in-ish replacement for ChatOllama, EXCEPT calls now go through
    storage_bridge.py's /ollama/chat route (via bridge_client.ollama_chat())
    instead of straight to Ollama. This is what makes
    _require_active_subscription() and _require_tier() actually run
    before any inference happens.

    NOT a LangChain Runnable -- does not support `prompt | llm | parser`
    piping. Use .invoke(prompt_str) for a single flat prompt, or
    .invoke_messages(messages) to send a full message list (system +
    chat history + latest human turn) in one call.

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
        # accept an options/num_predict field yet. If you need this
        # enforced server-side, add an `options` field to
        # OllamaChatRequest in storage_bridge.py and thread it through
        # bridge_client.ollama_chat().
        self.num_predict = num_predict

    def invoke(self, prompt: str) -> _LLMResponse:
        """Single flat-string prompt -- optionally prefixed with self.system
        as a system message. Use this for one-shot generation (quiz/paper
        question generation) where there's no multi-turn history to carry."""
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})
        return self._invoke_messages_raw(messages)

    def invoke_messages(self, messages: list) -> _LLMResponse:
        """Full message list -- e.g. [system, *chat_history, latest human turn].
        Use this instead of LangChain's `prompt | llm` piping to preserve
        multi-turn context."""
        normalized = [_normalize_message(m) for m in messages]
        return self._invoke_messages_raw(normalized)

    def _invoke_messages_raw(self, messages: list) -> _LLMResponse:
        result = bridge_client.ollama_chat(
            username=self.username,
            model=self.model,
            messages=messages,
        )
        # storage_bridge.py's /ollama/chat proxies Ollama's own response
        # shape verbatim: {"message": {"role": "assistant", "content": "..."}, ...}
        content = result.get("message", {}).get("content", "")
        return _LLMResponse(content)


def invoke_with_timeout(
    llm: "BridgeChatLLM",
    prompt: Union[str, list],
    timeout_seconds: int = 60,
) -> Optional[str]:
    """
    Call BridgeChatLLM.invoke() (if `prompt` is a string) or
    .invoke_messages() (if `prompt` is a list of messages) with a timeout.

    NOTE: the timeout here is a client-side thread-join safety net only
    (a bare thread.join() can't forcibly kill a stuck thread, it just
    stops waiting for it). The bridge's own HTTP call to Ollama has its
    own timeout (LLM_REQUEST_TIMEOUT in bridge_client.py, currently 300s)
    which is the one that actually aborts the upstream connection.

    Returns the response content, or None on timeout/generic error.

    IMPORTANT: if the bridge rejects the request (BridgeRequestError --
    inactive subscription, tier too low, daily cap hit) or is flat-out
    unreachable (BridgeUnavailableError), that exception is RE-RAISED
    from this function, not swallowed into a None return. A caller that
    gets None can't tell "Ollama was slow" apart from "this user isn't
    allowed to do this" -- those need different messages shown to the
    user, so callers of invoke_with_timeout() should wrap it in:

        try:
            text = invoke_with_timeout(llm, prompt)
        except BridgeRequestError as e:
            show_user_facing_message(e.detail)   # e.g. "Daily limit reached..."
        except BridgeUnavailableError as e:
            show_user_facing_message(str(e))
    """
    result = {"content": None, "error": None}

    def invoke_thread():
        try:
            if isinstance(prompt, list):
                response = llm.invoke_messages(prompt)
            else:
                response = llm.invoke(prompt)
            result["content"] = response.content
        except Exception as e:
            # Captured here (not re-raised inside the thread -- exceptions
            # raised inside a background thread do NOT propagate to the
            # caller's thread; they'd just print "Exception in thread" and
            # vanish). Stored instead, and re-raised below on the main
            # thread after join() returns.
            result["error"] = e

    thread = threading.Thread(target=invoke_thread, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds + 5)

    if thread.is_alive():
        print(f"[ScholarAI] LLM timeout after {timeout_seconds}s — bridge/Ollama may be stuck or overloaded")
        return None

    if result["error"]:
        if isinstance(result["error"], (BridgeRequestError, BridgeUnavailableError)):
            raise result["error"]
        print(f"[ScholarAI] LLM error: {type(result['error']).__name__}: {result['error']}")
        return None

    return result["content"]


def get_llm(model_type: str = "main", username: str = None, system: str = None, request_timeout: float = 60.0):
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
        system: optional system prompt, used by .invoke(prompt_str) call
            sites that don't build their own messages list.
        request_timeout: currently unused directly here (the bridge enforces
            its own upstream timeout) -- kept as a parameter for call-site
            compatibility with older code; pass the same value through to
            invoke_with_timeout()'s timeout_seconds instead.

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

        return BridgeChatLLM(model=OLLAMA_MAIN_MODEL, username=username, system=system, num_predict=num_predict)
    except Exception as e:
        print(f"[ScholarAI] Error setting up bridge-routed LLM: {e}")
        return None
