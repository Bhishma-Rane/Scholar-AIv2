"""
path_proxy.py
==============
A tiny reverse proxy that lets ONE ngrok tunnel serve the ScholarAI
storage bridge (port 8800). This exists as a no-admin-rights
alternative to nginx — it's pure Python, installs with a normal
`pip install` (no sudo, no Homebrew, no system directories touched),
and does the same job: nginx and this script solve the identical
problem.

CHANGE LOG (this revision):
  Removed the "/ollama" -> "http://127.0.0.1:11434" route. Ollama should
  no longer be reachable directly through the tunnel at all -- every AI
  call now goes through storage_bridge.py's own /ollama/chat and
  /ollama/generate routes (via /bridge/ollama/...), which enforce
  subscription + tier before proxying to Ollama internally.
  See core/llm.py's BridgeChatLLM for the client-side half of this fix.

  Leaving a direct "/ollama" route in ROUTES was the root cause of the
  tier/subscription system silently doing nothing: it let
  OLLAMA_BASE_URL point straight at Ollama, bypassing the bridge's
  checks entirely. If nothing in your Streamlit secrets still points at
  ".../ollama" through this proxy, this route was never needed once
  storage_bridge.py grew its own /ollama/* routes -- removing it here
  closes off that bypass for good rather than just stopping using it.

Routing rules:
    https://your-domain.ngrok-free.app/bridge/...  -> http://127.0.0.1:8800/...

Run with:
    python3 path_proxy.py
Then point your single ngrok tunnel at THIS script's port (8080 by
default), not at the bridge directly:
    ngrok http 8080 --domain=your-dev-domain.ngrok-free.app

Then in Streamlit Cloud's secrets:
    BRIDGE_BASE_URL = "https://your-dev-domain.ngrok-free.app/bridge"

    Remove OLLAMA_BASE_URL entirely (or point it, unused, at anything --
    core/llm.py no longer reads it). If any other code still references
    OLLAMA_BASE_URL directly, that's another bypass to hunt down and
    redirect through bridge_client.ollama_chat()/ollama_generate().

Requires: pip install aiohttp
"""
import asyncio
import logging

from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

logging.basicConfig(level=logging.INFO, format="[path_proxy] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Configuration — edit if your local ports ever change.
# ---------------------------------------------------------------------
LISTEN_PORT = 8080
ROUTES = {
    "/bridge": "http://127.0.0.1:8800",
}
# Request timeout. Ollama generation calls proxied THROUGH the bridge
# can run long (see your own quiz-generation timing investigation) —
# keep this generous so the proxy doesn't cut off a legitimate slow
# response. Should be >= storage_bridge.py's own upstream Ollama timeout
# and >= bridge_client.py's LLM_REQUEST_TIMEOUT, or this proxy will time
# out before the bridge does.
PROXY_TIMEOUT_SECONDS = 320


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    path = request.path  # e.g. "/bridge/health" or "/bridge/ollama/chat"

    matched_prefix = None
    target_base = None
    for prefix, base_url in ROUTES.items():
        if path == prefix or path.startswith(prefix + "/"):
            matched_prefix = prefix
            target_base = base_url
            break

    if target_base is None:
        return web.Response(status=404, text=f"No route configured for path: {path}")

    # Strip the prefix (e.g. "/bridge/health" -> "/health") before
    # forwarding, since the bridge doesn't know about the prefix.
    downstream_path = path[len(matched_prefix):] or "/"
    target_url = f"{target_base}{downstream_path}"
    if request.query_string:
        target_url += f"?{request.query_string}"

    body = await request.read()

    # Forward headers, but drop Host (the downstream service expects its
    # own Host) and Content-Length (recomputed automatically).
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    timeout = ClientTimeout(total=PROXY_TIMEOUT_SECONDS)
    async with ClientSession(timeout=timeout, connector=TCPConnector(ssl=False)) as session:
        try:
            async with session.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                data=body if body else None,
                allow_redirects=False,
            ) as resp:
                response_body = await resp.read()
                response_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")
                }
                return web.Response(
                    status=resp.status,
                    body=response_body,
                    headers=response_headers,
                )
        except asyncio.TimeoutError:
            log.warning(f"Timeout proxying {request.method} {target_url}")
            return web.Response(status=504, text="Upstream timeout")
        except Exception as e:
            log.warning(f"Error proxying {request.method} {target_url}: {type(e).__name__}: {e}")
            return web.Response(status=502, text=f"Bad gateway: {e}")


def build_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024 * 50)  # 50MB cap, for PDF uploads via /bridge
    app.router.add_route("*", "/{path_info:.*}", handle_proxy)
    return app


if __name__ == "__main__":
    log.info(f"Starting path-based proxy on port {LISTEN_PORT}")
    for prefix, target in ROUTES.items():
        log.info(f"  {prefix}/*  ->  {target}")
    web.run_app(build_app(), host="127.0.0.1", port=LISTEN_PORT)
