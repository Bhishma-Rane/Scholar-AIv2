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
    reset_password,
    BridgeUnavailableError,
    BridgeRequestError,
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
    if st.session_state.get("logged_in_user"):
        return

    token = st.query_params.get(TOKEN_QUERY_PARAM)
    if not token:
        return

    try:
        username = verify_login_token(token)
    except BridgeUnavailableError:
        return

    if username:
        st.session_state.logged_in_user = username
        st.session_state.login_token = token


SESSION_CHECK_INTERVAL_SECONDS = 20


def _verify_active_session():
    """Detects if a newer login (e.g. on another device) has superseded this
    session's token, and force-logs-out this session if so. Runs on every
    rerun but throttled so it doesn't hammer the bridge on every widget click."""
    token = st.session_state.get("login_token")
    if not token:
        return

    now = time.time()
    last_check = st.session_state.get("_last_session_check", 0.0)
    if now - last_check < SESSION_CHECK_INTERVAL_SECONDS:
        return
    st.session_state["_last_session_check"] = now

    try:
        active_username = verify_login_token(token)
    except BridgeUnavailableError:
        return  # fail open on a bridge blip, don't kick someone out for that

    if active_username != st.session_state.get("logged_in_user"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        if TOKEN_QUERY_PARAM in st.query_params:
            del st.query_params[TOKEN_QUERY_PARAM]
        st.error("You've been logged out because this account was signed in elsewhere.")
        st.stop()


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

            # Always issue a token, even for "don't stay logged in" sessions --
            # this is what lets us detect a kickout on THIS device when someone
            # else logs in elsewhere (see _verify_active_session below). Only
            # persist it to the URL if the user opted into staying logged in.
            try:
                token = issue_login_token(clean_username, password_input)
                if token:
                    st.session_state.login_token = token
                    if stay_logged_in:
                        st.query_params[TOKEN_QUERY_PARAM] = token
            except BridgeUnavailableError:
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


def _render_forgot_password_form():
    st.subheader("Reset Password")

    if st.session_state.get("reset_password_done"):
        st.success("Password reset! You can log in with your new password now.")
        if st.button("Back to reset form", key="reset_back_to_form"):
            st.session_state["reset_password_done"] = False
            st.rerun()
        return

    st.caption(
        "Locked out? Ask Bhishma for a reset code (in person or on "
        "WhatsApp), then enter it below along with your new password."
    )

    reset_username = st.text_input("Username:", key="reset_username")
    reset_token = st.text_input("Reset code (from Bhishma):", key="reset_token")
    reset_new_password = st.text_input("New password:", type="password", key="reset_new_password")
    reset_confirm_password = st.text_input("Confirm new password:", type="password", key="reset_confirm_password")

    if st.button("Reset Password", type="primary", use_container_width=True, key="reset_submit"):
        clean_username = reset_username.strip().lower()
        clean_token = reset_token.strip()

        if not clean_username or not clean_token or not reset_new_password:
            st.error("Please fill in all fields.")
            return
        if reset_new_password != reset_confirm_password:
            st.error("Passwords don't match.")
            return
        if len(reset_new_password) < MIN_PASSWORD_LENGTH:
            st.error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return

        try:
            reset_password(clean_username, clean_token, reset_new_password)
        except BridgeRequestError as e:
            st.error(e.detail)
            if "expired" in e.detail.lower() or "used" in e.detail.lower() or "invalid" in e.detail.lower():
                st.info("Ask Bhishma for a new reset code.")
            return
        except BridgeUnavailableError:
            st.error(
                "Can't reach the account server right now (the storage bridge "
                "may be offline). Please try again in a moment."
            )
            return

        # Don't write to st.session_state["reset_username"] etc. here --
        # those keys are bound to the text_input widgets above, and
        # Streamlit forbids setting a widget-bound key after the widget
        # has already been instantiated in this run (raises
        # StreamlitAPIException). Use a rerun + separate flag instead,
        # same pattern _render_login_form() already uses on success --
        # the rerun naturally clears the form since this branch (done=True)
        # renders instead of the inputs.
        st.session_state["reset_password_done"] = True
        st.rerun()


def logout():
    token = st.session_state.get("login_token")
    if token:
        try:
            revoke_login_token(token)
        except BridgeUnavailableError:
            pass

    if TOKEN_QUERY_PARAM in st.query_params:
        del st.query_params[TOKEN_QUERY_PARAM]

    for key in list(st.session_state.keys()):
        del st.session_state[key]


def require_login() -> str:
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
                login_tab, signup_tab, forgot_tab = st.tabs(["Login", "Sign Up", "Forgot password?"])
                with login_tab:
                    _render_login_form()
                with signup_tab:
                    _render_signup_form()
                with forgot_tab:
                    _render_forgot_password_form()
        st.stop()

    _verify_active_session()
    return st.session_state.logged_in_user
