import streamlit as st
import requests
import platform
import socket

BRIDGE_URL = "https://scored-secrecy-rocking.ngrok-free.dev"  # or your VPS URL post-migration
BRIDGE_SECRET_HEADERS = {"x-bridge-secret": st.secrets.get("BRIDGE_SHARED_SECRET", "")}


def _device_fingerprint() -> str:
    """Best-effort human-readable device label, not used for security, just for the
    'logged out because of a login on <device>' messaging."""
    try:
        return f"{platform.system()} - {socket.gethostname()}"
    except Exception:
        return "unknown device"


def issue_new_session(username: str) -> str:
    """Call this immediately after successful password verification at login."""
    resp = requests.post(
        f"{BRIDGE_URL}/session/create",
        json={"username": username, "device_info": _device_fingerprint()},
        headers=BRIDGE_SECRET_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["session_token"]
    st.session_state["session_token"] = token
    st.session_state["username"] = username
    return token


def enforce_single_session():
    """Call this at the top of every page / on every rerun, after login-gating.
    Kicks the user out if a newer session has been issued elsewhere."""
    username = st.session_state.get("username")
    token = st.session_state.get("session_token")

    if not username or not token:
        return  # not logged in yet, nothing to enforce

    try:
        resp = requests.post(
            f"{BRIDGE_URL}/session/validate",
            json={"username": username, "session_token": token},
            headers=BRIDGE_SECRET_HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.RequestException:
        # Bridge unreachable — fail open rather than locking users out over a network blip.
        # If you'd rather fail closed, force logout here instead.
        return

    if not result.get("valid"):
        for key in ("session_token", "username", "authenticated"):
            st.session_state.pop(key, None)
        st.error("You've been logged out because this account was signed in on another device.")
        st.stop()


def logout(username: str, token: str):
    try:
        requests.post(
            f"{BRIDGE_URL}/session/logout",
            json={"username": username, "session_token": token},
            headers=BRIDGE_SECRET_HEADERS,
            timeout=8,
        )
    except requests.RequestException:
        pass
    for key in ("session_token", "username", "authenticated"):
        st.session_state.pop(key, None)
