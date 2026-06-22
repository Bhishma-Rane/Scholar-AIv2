"""
ui/auth.py
==========
Username + password login gate, with a separate sign-up flow for new
accounts. Stops script execution via st.stop() until a user is
authenticated, so app.py can safely assume a logged-in user for
everything that runs after calling require_login().

Failed login attempts are rate-limited per browser session (not
per-username globally) to slow down naive brute-force guessing without
needing any external infrastructure.
"""
import time

import streamlit as st

from config import MIN_PASSWORD_LENGTH
from core.credentials import create_account, user_exists, verify_password

MAX_ATTEMPTS_BEFORE_COOLDOWN = 5
COOLDOWN_SECONDS = 30


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


def _render_login_form():
    st.subheader("Login")
    username_input = st.text_input("Username:", key="login_username")
    password_input = st.text_input("Password:", type="password", key="login_password")

    if st.button("Login", type="primary", use_container_width=True):
        lockout_remaining = _is_locked_out()
        if lockout_remaining > 0:
            st.error(f"Too many failed attempts. Try again in {int(lockout_remaining)}s.")
            return

        clean_username = username_input.strip().lower()
        if not clean_username or not password_input:
            st.error("Please enter both a username and password.")
            return

        if verify_password(clean_username, password_input):
            _record_successful_login()
            st.session_state.logged_in_user = clean_username
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
        if user_exists(clean_username):
            st.error("That username is already taken. Try logging in instead.")
            return
        if len(new_password) < MIN_PASSWORD_LENGTH:
            st.error(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if new_password != confirm_password:
            st.error("Passwords don't match.")
            return

        create_account(clean_username, new_password)
        st.session_state.logged_in_user = clean_username
        st.success("Account created!")
        st.rerun()


def require_login() -> str:
    """
    Renders the login/signup screen if no user is logged in yet, and
    halts the script. Returns the logged-in username once available.
    """
    _init_auth_state()

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
