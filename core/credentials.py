from core.bridge_client import (
    create_account as _bridge_create_account,
    user_exists as _bridge_user_exists,
    verify_password as _bridge_verify_password,
    issue_login_token as _bridge_issue_login_token,
    verify_login_token as _bridge_verify_login_token,
    revoke_login_token as _bridge_revoke_login_token,
    reset_password as _bridge_reset_password,
    BridgeUnavailableError,
    BridgeRequestError,
    AccountDisabledError,
)

__all__ = [
    "create_account", "user_exists", "verify_password", "issue_login_token",
    "verify_login_token", "revoke_login_token", "reset_password",
    "BridgeUnavailableError", "BridgeRequestError", "AccountDisabledError",
]


def user_exists(username: str) -> bool:
    return _bridge_user_exists(username)


def create_account(username: str, password: str) -> bool:
    return _bridge_create_account(username, password)


def verify_password(username: str, password: str) -> bool:
    return _bridge_verify_password(username, password)


def issue_login_token(username: str, password: str):
    return _bridge_issue_login_token(username, password)


def verify_login_token(token: str):
    return _bridge_verify_login_token(token)


def revoke_login_token(token: str) -> None:
    _bridge_revoke_login_token(token)


def reset_password(username: str, token: str, new_password: str) -> None:
    """
    Applies an admin-issued password reset token (see reset_password.py,
    which Bhishma runs to generate the token a student is given). Raises
    BridgeRequestError with a human-readable .detail if the token is
    invalid, expired, or already used, or if new_password is too short.
    """
    _bridge_reset_password(username, token, new_password)
