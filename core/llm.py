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


def invoke_with_timeout(llm: ChatOllama, prompt: str, timeout_seconds: int = 30) -> Optional[str]:
    """
    Call ChatOllama.invoke() with a timeout. Since ChatOllama doesn't support
    native timeouts, we use threading to kill the call if it exceeds the limit.
    
    Returns the response content or None if timeout occurs.
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
    thread.join(timeout=timeout_seconds)
    
    if thread.is_alive():
        # Thread is still running — timeout occurred
        print(f"[ScholarAI] LLM timeout after {timeout_seconds}s — Ollama may be stuck or overloaded")
        return None
    
    if result["error"]:
        raise result["error"]
    
    return result["content"]


def get_llm(model_type: str = "main"):
    """
    Get a ChatOllama instance configured for the specified task.
    
    Args:
        model_type: "main" for standard inference, "quiz" for quiz generation
    
    Returns:
        ChatOllama instance or None if connection fails.
    """
    try:
        common_kwargs = dict(
            model=OLLAMA_MAIN_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1,
            top_p=0.2,
            client_kwargs={"headers": {"ngrok-skip-browser-warning": "true"}},
        )
        if model_type == "quiz":
            return ChatOllama(**common_kwargs, num_ctx=8192, num_predict=2048)
        return ChatOllama(**common_kwargs, num_ctx=8192)
    except Exception as e:
        print(f"[ScholarAI] Error connecting to Ollama: {e}")
        return None
