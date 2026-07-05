"""
core/onboarding_store.py
===========================
Tracks whether a given user has completed (or skipped) the first-run
tutorial, so it's shown automatically once per account rather than on
every login.

CHANGED: this flag used to live in a local JSON file under
get_user_paths(username)["root"] -- but that root is explicitly a
Streamlit Cloud container-local SCRATCH directory (see core/paths.py's
docstring), the same class of "wiped on container restart" problem
subjects/files hit before they moved to the bridge. The tutorial flag
now goes through the bridge instead, the same way theme_color does
(see core/bridge_client.py's get_theme_color/set_theme_color), so it
actually survives restarts/redeploys.
"""
from core import bridge_client
from core.bridge_client import BridgeUnavailableError


def has_completed_tutorial(username: str) -> bool:
    """
    Returns False (not just "unknown") if the bridge is unreachable --
    fails toward showing the tutorial again rather than silently
    hiding it, since accidentally re-showing a first-run dialog is a
    much smaller problem than an account that can never be marked done.
    """
    try:
        return bridge_client.get_tutorial_completed(username)
    except BridgeUnavailableError:
        return False


def mark_tutorial_complete(username: str) -> None:
    try:
        bridge_client.set_tutorial_completed(username, True)
    except BridgeUnavailableError:
        # Bridge is down -- the dialog will simply reappear next login
        # and the user can dismiss it again then. Not worth raising
        # into the UI for something this low-stakes.
        pass


def reset_tutorial(username: str) -> None:
    """Used by the sidebar 'Replay Tutorial' button."""
    try:
        bridge_client.set_tutorial_completed(username, False)
    except BridgeUnavailableError:
        pass
