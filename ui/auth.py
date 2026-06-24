"""
ui/auth.py
==========
Username + password login gate, with a separate sign-up flow for new
accounts. Stops script execution via st.stop() until a user is
authenticated, so app.py can safely assume a logged-in user for
everything that runs after calling require_login().

AUTO-LOGIN: on successful login, a long-lived token (issued by the
storage bridge — see core/credentials.py / core/bridge_client.py) is
stored in the browser's URL query params. On every page load, BEFORE
showing the login form, we check for that token and silently
re-authenticate if it's valid — so a page reload or reopening the tab
doesn't force a fresh login. Logging out clears both the token's
server-side record (so it can't be reused) and the URL param.

Failed login attempts are rate-limited per browser session (not
per-username globally) to slow down naive brute-force guessing without
needing any external infrastructure.
"""
import time

import streamlit as st

from config import MIN_PASSWORD_LENGTH
from core.credentials import (
    create_account,
    user_exists,
    verify_password,
    issue_login_token,
    verify_login_token,
    revoke_login_token,
    BridgeUnavailableError,
)

MAX_ATTEMPTS_BEFORE_COOLDOWN = 5
COOLDOWN_SECONDS = 30
TOKEN_QUERY_PARAM = "auth_token"


def _init_auth_state():
    if "login_attempts" not in st.session_state:
        st.session_state.login_attempts = 0
    if "login_locked_until" not in st.session_state:
        st.session_state.login_locked_until = 0.0


def _is_locked_out() -> float:
    """Returns remaining lockout seconds (0 if not locked out)."""
    remaining = st.session_state.login_locked_until - time.time()
    return max(0.0, remaining)


def _record_failed_attempt():
    st.session_state.login_attempts += 1
    if st.session_state.login_attempts >= MAX_ATTEMPTS_BEFORE_COOLDOWN:
        st.session_state.login_locked_until = time.time() + COOLDOWN_SECONDS
        st.session_state.login_attempts = 0


def _record_successful_login():
    st.session_state.login_attempts = 0
    st.session_state.login_locked_until = 0.0


def _try_auto_login_from_url():
    """
    Runs once per page load, before the login form is shown. If a valid
    auth token is present in the URL, logs the user in silently without
    requiring the password form. Safe to call even when already logged
    in (it's a no-op in that case).
    """
    if st.session_state.get("logged_in_user"):
        return  # already logged in this session, nothing to do

    token = st.query_params.get(TOKEN_QUERY_PARAM)
    if not token:
        return

    try:
        username = verify_login_token(token)
    except BridgeUnavailableError:
        # Bridge is down — fail open to the normal login form rather than
        # blocking the user with a confusing error on every page load.
        return

    if username:
        st.session_state.logged_in_user = username
        st.session_state.login_token = token


def _render_login_form():
    st.subheader("Login")
    username_input = st.text_input("Username:", key="login_username")
    password_input = st.text_input("Password:", type="password", key="login_password")
    stay_logged_in = st.checkbox("Keep me logged in on this device", value=True, key="login_stay")

    if st.button("Login", type="primary", use_container_width=True):
        lockout_remaining = _is_locked_out()
        if lockout_remaining > 0:
            st.error(f"Too many failed attempts. Try again in {int(lockout_remaining)}s.")
            return

        clean_username = username_input.strip().lower()
        if not clean_username or not password_input:
            st.error("Please enter both a username and password.")
            return

        try:
            valid = verify_password(clean_username, password_input)
        except BridgeUnavailableError:
            st.error(
                "Can't reach the account server right now (the storage bridge "
                "may be offline). Please try again in a moment."
            )
            return

        if valid:
            _record_successful_login()
            st.session_state.logged_in_user = clean_username

            if stay_logged_in:
                try:
                    token = issue_login_token(clean_username, password_input)
                    if token:
                        st.session_state.login_token = token
                        st.query_params[TOKEN_QUERY_PARAM] = token
                except BridgeUnavailableError:
                    # Login itself still succeeded for this session — just
                    # without auto-login persistence. Not worth blocking on.
                    pass

            st.rerun()
        else:
            _record_failed_attempt()
            st.error("Incorrect username or password.")


def _render_signup_form():
    st.subheader("Create a New Profile")
    new_username = st.text_input("Choose a username:", key="signup_username")
    new_password = st.text_input("Choose a password:", type="password", key="signup_password")
    confirm_password = st.text_input("Confirm password:", type="password", key="signup_confirm")

    if st.button("Create Account", type="primary", use_container_width=True):
        clean_username = new_username.strip().lower()

        if not clean_username:
            st.error("Please choose a username.")
            return

        try:
            already_taken = user_exists(clean_username)
        except BridgeUnavailableError:
            st.error(
                "Can't reach the account server right now (the storage bridge "
                "may be offline). Please try again in a moment."
            )
            return

        if already_taken:
            st.error("That username is already taken. Try logging in instead.")
            return
        if len(new_password) < MIN_PASSWORD_LENGTH:
            st.error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if new_password != confirm_password:
            st.error("Passwords don't match.")
            return

        try:
            create_account(clean_username, new_password)
            token = issue_login_token(clean_username, new_password)
        except BridgeUnavailableError:
            st.error(
                "Can't reach the account server right now (the storage bridge "
                "may be offline). Please try again in a moment."
            )
            return

        st.session_state.logged_in_user = clean_username
        if token:
            st.session_state.login_token = token
            st.query_params[TOKEN_QUERY_PARAM] = token

        st.success("Account created!")
        st.rerun()


def logout():
    """Call this from the sidebar's logout button instead of manually
    clearing session_state, so the server-side token is revoked too —
    otherwise the URL token would still silently log the user back in
    on the very next page load."""
    token = st.session_state.get("login_token")
    if token:
        try:
            revoke_login_token(token)
        except BridgeUnavailableError:
            pass  # bridge down — token will just expire on its own eventually

    if TOKEN_QUERY_PARAM in st.query_params:
        del st.query_params[TOKEN_QUERY_PARAM]

    for key in list(st.session_state.keys()):
        del st.session_state[key]


def require_login() -> str:
    """
    Renders the login/signup screen if no user is logged in yet, and
    halts the script. Returns the logged-in username once available.
    """
    _init_auth_state()
    _try_auto_login_from_url()

    if not st.session_state.logged_in_user:
        st.markdown(
            "<div style='text-align:center; font-size:36px; font-weight:800;'>🎓 ScholarAI</div>"
            "<div style='text-align:center; color:#888; margin-bottom:4px;'>by AuraStudios</div>"
            "<div style='text-align:center; font-style:italic; color:#666; margin-bottom:20px;'>"
            "Learn. Understand. Master.</div>",
            unsafe_allow_html=True,
        )
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            with st.container(border=True):
                login_tab, signup_tab = st.tabs(["Login", "Sign Up"])
                with login_tab:
                    _render_login_form()
                with signup_tab:
                    _render_signup_form()
        st.stop()

    return st.session_state.logged_in_user
