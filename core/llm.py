"""
core/llm.py
===========
Lazy-loaded LLM access and the shared internet/image search tools.

CHANGE LOG (this revision):
  - BridgeChatLLM and get_llm() now carry a `feature` string end-to-end
    and send it to bridge_client.ollama_chat() on every call, which
    forwards it to storage_bridge.py's /ollama/chat route as
    req.feature. Previously `model_type` ("main"/"quiz"/"paper") only
    ever affected num_predict -- it was NEVER turned into a feature name
    on the wire, so the bridge's /ollama/chat route had no way to tell
    "this is a chat message" apart from "this is quiz/flashcard/paper
    generation" and gated every single call as "ai_chat". That's why
    FEATURE_MIN_TIER's "quiz_generation": "gold" and "flashcards": "gold"
    entries were dead config -- this fixes that.
  - get_llm() gained an explicit `feature` override parameter, separate
    from model_type. This matters because features/flashcards.py calls
    get_llm("quiz", ...) (same model_type as real quiz-question
    generation, since both want the same num_predict budget) but needs
    to be gated as "flashcards", not "quiz_generation" -- pass
    feature="flashcards" explicitly at that call site.
  - Added embed_texts() -- the embeddings-side counterpart to
    BridgeChatLLM. Routes embedding requests through
    storage_bridge.py's /ollama/embed route (via
    bridge_client.ollama_embed()) instead of calling Ollama directly.
    Used by core/vectorstore.py's BridgeEmbeddings.
  - BridgeChatLLM now ACTUALLY ATTEMPTS to forward `num_predict` (as an
    `options={"num_predict": ...}` kwarg) and JSON mode (as a `format=
    "json"` kwarg) to bridge_client.ollama_chat(). Previously num_predict
    was accepted by __init__ for call-site compatibility but silently
    dropped -- the "paper" model_type's 3072-token budget documented in
    get_llm() was never actually reaching Ollama, which means every
    question-paper generation call was running with Ollama's own
    default num_predict instead of the budget this file claimed to set.
    That's a very plausible contributor to the "model truncates output
    early" symptom, independent of anything in
    features/question_paper_generator.py's retry logic.
  - THIS ONLY TAKES EFFECT IF bridge_client.ollama_chat() AND
    storage_bridge.py's /ollama/chat ROUTE ARE ALSO UPDATED to accept
    and forward `options`, `format`, and now `feature`. All three have
    since been updated (see bridge_client.py and storage_bridge.py) --
    the TypeError fallback below is now dead code for that reason, but
    left in place as a safety net in case those files ever regress.
  - invoke(), invoke_messages(), and invoke_with_timeout() all gained an
    optional `json_mode: bool = False` parameter that threads down to
    the same format="json" attempt, for callers (like
    features/question_paper_generator.py's _invoke_llm) that want
    Ollama's native JSON mode to cut down on chatty/markdown-wrapped
    responses.

PREVIOUS REVISION'S CHANGE LOG (why BridgeChatLLM exists at all):
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
    - .invoke(prompt: str) -- single flat string prompt, used by
      features/question_paper_generator.py
      and the quiz chunk generator.
    - .invoke_messages(messages) -- a list of {"role", "content"} dicts
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

import requests
from langchain_community.tools import DuckDuckGoSearchRun

from core import bridge_client
from core.bridge_client import BridgeRequestError, BridgeUnavailableError
from config import OLLAMA_MAIN_MODEL

# Single shared search tool instance.
internet_search = DuckDuckGoSearchRun()

# Printed at most once per process -- see _invoke_messages_raw. Without
# this, every single LLM call would print the same "bridge doesn't
# support options/format yet" line until bridge_client.py and
# storage_bridge.py are updated, which would be extremely noisy for a
# question paper generating 8+ items in parallel.
_warned_bridge_missing_kwargs = False

# Maps get_llm()'s model_type to the FEATURE_MIN_TIER/DAILY_CAPS key it
# should be gated as on the bridge, when the caller doesn't pass an
# explicit `feature=` override. Keep this in sync with
# storage_bridge.py's FEATURE_MIN_TIER dict.
_MODEL_TYPE_TO_FEATURE = {
    "main": "ai_chat",
    "quiz": "quiz_generation",
    "paper": "question_paper",
}


_COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
_COMMONS_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp")

# CHANGED: was DuckDuckGo image search via the `ddgs` library. That library
# scrapes DDG's HTML to extract a "vqd" auth token per-query, and DDG has
# started blocking that scrape -- every single call now fails with
# DDGSException("Could not extract vqd."), regardless of query or network
# health. This isn't something we can fix on our end (no API key, no
# request param changes get around it), and it's broken this way before
# and will likely break again since it's an unofficial scrape. Swapped to
# Wikimedia Commons' public MediaWiki API instead: no API key, no rate-limit
# auth dance, and it's well suited to this use case since Commons is full of
# labeled educational/anatomy diagrams (the exact thing "!diagram the human
# heart" wants).
def search_images(query: str, max_results: int = 4) -> list:
    """
    Free, no-API-key image search via the Wikimedia Commons API. Used for
    "VISUAL" diagram requests (e.g. "the human heart") where a real labeled
    image is needed rather than a Mermaid diagram.

    Returns a list of {"title": str, "image_url": str, "source_url": str}.
    Returns an empty list on any failure rather than raising, since this
    is a best-effort enhancement, not a critical path.
    """
    try:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap|drawing {query}",
            "gsrnamespace": 6,  # File: namespace only
            "gsrlimit": max_results,
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": 800,
            "format": "json",
        }
        resp = requests.get(
            _COMMONS_API_URL,
            params=params,
            headers={"User-Agent": "ScholarAI-v2/1.0 (study companion app)"},
            timeout=8,
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})

        results = []
        for page in pages.values():
            title = page.get("title", query).replace("File:", "", 1)
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            url = info.get("thumburl") or info.get("url", "")
            if not url.lower().endswith(_COMMONS_IMAGE_EXTS):
                continue
            results.append(
                {
                    "title": title,
                    "image_url": url,
                    "source_url": info.get("descriptionurl", url),
                }
            )
        return results[:max_results]
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
                 num_predict: Optional[int] = None, feature: str = "ai_chat"):
        self.model = model
        self.username = username
        self.system = system
        self.num_predict = num_predict
        # Which FEATURE_MIN_TIER/DAILY_CAPS key this instance's calls
        # should be gated as on the bridge -- see get_llm() below for how
        # this gets set. Always sent, so the bridge never has to guess.
        self.feature = feature

    def invoke(self, prompt: str, json_mode: bool = False, timeout: float = None) -> _LLMResponse:
        """Single flat-string prompt -- optionally prefixed with self.system
        as a system message. Use this for one-shot generation (quiz/paper
        question generation) where there's no multi-turn history to carry.

        `json_mode`, if True, attempts to request Ollama's native JSON
        output mode -- see _invoke_messages_raw.

        `timeout`, if given, is passed down to bridge_client.ollama_chat()
        as its socket-level timeout -- see invoke_with_timeout() for why
        this matters."""
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})
        return self._invoke_messages_raw(messages, json_mode=json_mode, timeout=timeout)

    def invoke_messages(self, messages: list, json_mode: bool = False, timeout: float = None) -> _LLMResponse:
        """Full message list -- e.g. [system, *chat_history, latest human turn].
        Use this instead of LangChain's `prompt | llm` piping to preserve
        multi-turn context."""
        normalized = [_normalize_message(m) for m in messages]
        return self._invoke_messages_raw(normalized, json_mode=json_mode, timeout=timeout)

    def _invoke_messages_raw(self, messages: list, json_mode: bool = False, timeout: float = None) -> _LLMResponse:
        global _warned_bridge_missing_kwargs
        kwargs = {"feature": self.feature}
        if self.num_predict:
            kwargs["options"] = {"num_predict": self.num_predict}
        if json_mode:
            kwargs["format"] = "json"
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            result = bridge_client.ollama_chat(
                username=self.username,
                model=self.model,
                messages=messages,
                **kwargs,
            )
        except TypeError:
            if kwargs and not _warned_bridge_missing_kwargs:
                print(
                    "[ScholarAI] bridge_client.ollama_chat() doesn't accept options/format/timeout/feature "
                    "yet -- num_predict, json_mode, timeout, and feature are being silently ignored until "
                    "bridge_client.py and storage_bridge.py's /ollama/chat route are updated "
                    "to forward them."
                )
                _warned_bridge_missing_kwargs = True
            result = bridge_client.ollama_chat(
                username=self.username,
                model=self.model,
                messages=messages,
            )

        # storage_bridge.py's /ollama/chat proxies Ollama's own response
        # shape verbatim: {"message": {"role": "assistant", "content": "..."}, ...}
        content = result.get("message", {}).get("content", "")
        return _LLMResponse(content)


def embed_texts(username: str, texts: list, model: Optional[str] = None) -> list:
    """
    Routes embedding requests through storage_bridge.py's /ollama/embed
    route instead of calling Ollama directly -- the embeddings-side
    counterpart to BridgeChatLLM. Used by core/vectorstore.py's
    BridgeEmbeddings so Chroma indexing goes through the bridge like
    every other AI call does.

    Raises BridgeRequestError if the bridge rejects the request (inactive
    subscription -> 402) and BridgeUnavailableError if the bridge can't
    be reached -- same exceptions BridgeChatLLM raises, so callers can
    handle both the same way.
    """
    if model is None:
        from config import OLLAMA_EMBED_MODEL
        model = OLLAMA_EMBED_MODEL

    result = bridge_client.ollama_embed(
        username=username,
        model=model,
        input=texts,
    )
    return result["embeddings"]


def invoke_with_timeout(
    llm: "BridgeChatLLM",
    prompt: Union[str, list],
    timeout_seconds: int = 60,
    json_mode: bool = False,
) -> Optional[str]:
    """
    Call BridgeChatLLM.invoke() (if `prompt` is a string) or
    .invoke_messages() (if `prompt` is a list of messages) with a timeout.

    NOTE: thread.join() can't forcibly kill a stuck thread -- it just
    stops WAITING for it. Previously the underlying HTTP call still ran
    for bridge_client.py's full LLM_REQUEST_TIMEOUT (300s) in the
    background after this function gave up and returned None, silently
    occupying a slot in Ollama's (single-worker) queue as an orphan the
    caller had already written off -- and if the caller then retried,
    the retry piled a second call on top of that same queue, compounding
    the problem. To avoid that, we now pass `timeout_seconds` down to
    BridgeChatLLM so the HTTP request itself gives up at roughly the
    same time this function does, instead of running up to 5 minutes
    longer than the caller thinks it will.

    Returns the response content, or None on timeout/generic error.

    IMPORTANT: if the bridge rejects the request (BridgeRequestError --
    inactive subscription, tier too low, daily cap hit) or is flat-out
    unreachable (BridgeUnavailableError), that exception is RE-RAISED
    from this function, not swallowed into a None return. Callers of
    invoke_with_timeout() should wrap it in:

        try:
            text = invoke_with_timeout(llm, prompt)
        except BridgeRequestError as e:
            show_user_facing_message(e.detail)  # e.g. "Daily limit reached..."
        except BridgeUnavailableError as e:
            show_user_facing_message(str(e))
    """
    result = {"content": None, "error": None}

    # Give the HTTP call itself a bound close to timeout_seconds (plus a
    # small cushion for network/bridge overhead) so it doesn't keep
    # running as an orphan for bridge_client's full 300s default after
    # this function has already given up on it.
    http_timeout = timeout_seconds + 10

    def invoke_thread():
        try:
            if isinstance(prompt, list):
                response = llm.invoke_messages(prompt, json_mode=json_mode, timeout=http_timeout)
            else:
                response = llm.invoke(prompt, json_mode=json_mode, timeout=http_timeout)
            result["content"] = response.content
        except Exception as e:
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


def get_llm(model_type: str = "main", username: str = None, system: str = None,
            request_timeout: float = 60.0, feature: Optional[str] = None):
    """
    Get a BridgeChatLLM instance configured for the specified task.
    ALL inference now goes through storage_bridge.py's /ollama/chat route,
    which enforces subscription + tier before proxying to Ollama.

    Args:
        model_type: "main" for standard inference, "quiz" for quiz generation,
            "paper" for question-paper generation (one question type per call).
            Also used to derive num_predict AND (via _MODEL_TYPE_TO_FEATURE)
            the default feature gate, unless `feature` overrides it.
        username: REQUIRED. The bridge's tier/subscription gate is keyed on
            this. Every existing call site that previously did
            `get_llm("quiz")` etc. now needs `get_llm("quiz", username=username)`.
        system: optional system prompt, used by .invoke(prompt_str) call
            sites that don't build their own messages list.
        request_timeout: currently unused directly here (the bridge enforces
            its own upstream timeout) -- kept as a parameter for call-site
            compatibility with older code; pass the same value through to
            invoke_with_timeout()'s timeout_seconds instead.
        feature: explicit override for which FEATURE_MIN_TIER/DAILY_CAPS key
            this LLM's calls should be gated as (e.g. "ai_chat",
            "quiz_generation", "flashcards", "question_paper"). Use this
            whenever model_type's default mapping doesn't match what the
            call actually is -- e.g. features/flashcards.py calls
            get_llm("quiz", username=username, feature="flashcards")
            because it wants "quiz" model_type's num_predict budget but
            needs to be gated as "flashcards", not "quiz_generation".
            If omitted, defaults from model_type via _MODEL_TYPE_TO_FEATURE.

    Returns:
        BridgeChatLLM instance, or None if username is missing (fails
        loudly via a printed error rather than silently making an
        unauthenticated call).
    """
    if not username:
        print("[ScholarAI] get_llm() called without a username -- cannot route through the "
              "bridge's tier/subscription check. Every call site must now pass username=.")
        return None

    resolved_feature = feature or _MODEL_TYPE_TO_FEATURE.get(model_type, "ai_chat")

    try:
        num_predict = None
        if model_type == "quiz":
            num_predict = 1024
        elif model_type == "paper":
            num_predict = 3072
        return BridgeChatLLM(model=OLLAMA_MAIN_MODEL, username=username, system=system,
                              num_predict=num_predict, feature=resolved_feature)
    except Exception as e:
        print(f"[ScholarAI] Error setting up bridge-routed LLM: {e}")
        return None
