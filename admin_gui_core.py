"""
admin_gui.py
==============
ScholarAI Admin Panel — a desktop GUI replacing the terminal commands
you've been running by hand (toggle_access.py, manage_tier.py,
reset_password.py, view_feedback.py, and starting/stopping the bridge
itself). Built with customtkinter (a modern-styled skin over tkinter --
ships with Python, no extra system dependencies, unlike PyQt).

RUN THIS FROM THE SAME FOLDER AS storage_bridge.py:
    python admin_gui.py

WHAT IT DOES:
  - Dashboard tab: start/stop Ollama, the bridge, the path proxy, and
    ngrok (replicates startscholar_all/stopscholar_all/scholar_status
    from your ~/.zshrc, but as buttons with live status instead of
    typed commands)
  - Students tab: see all users, their tier/subscription status,
    activate/deactivate access, change tier, issue password resets
  - Feedback tab: view bug reports/suggestions/ratings, mark resolved
  - Question Papers tab: see published/draft papers at a glance

DESIGN NOTE: this talks to the bridge over HTTP (same as Streamlit
does), using the LOCAL address (http://127.0.0.1:8800) since it's
running on the same machine as the bridge -- no ngrok/proxy needed for
the GUI's own API calls, only for Streamlit Cloud's remote calls.
"""
import os
import sys
import subprocess
import signal
import threading
import time
from pathlib import Path
from datetime import datetime

import customtkinter as ctk
import requests

# ---------------------------------------------------------------------
# Configuration -- edit these if your setup differs
# ---------------------------------------------------------------------
BRIDGE_DIR = Path(__file__).resolve().parent  # assumes this file sits next to storage_bridge.py
BRIDGE_LOCAL_URL = "http://127.0.0.1:8800"
BRIDGE_PORT = 8800

LOG_DIR = Path.home() / "scholarai-logs"
PID_DIR = Path.home() / ".scholarai-pids"
LOG_DIR.mkdir(exist_ok=True)
PID_DIR.mkdir(exist_ok=True)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def get_bridge_secret() -> str:
    """
    Reads BRIDGE_SHARED_SECRET the same way storage_bridge.py does --
    from the environment. If you normally `export` it via your shell
    profile before running things, launch this GUI from a terminal
    where that's already set (e.g. right after `source ~/.zshrc`).
    """
    return os.environ.get("BRIDGE_SHARED_SECRET", "")


# ---------------------------------------------------------------------
# Process management -- mirrors startscholar_all/stopscholar_all/scholar_status
# ---------------------------------------------------------------------
class ProcessManager:
    """
    Starts/stops Ollama, the bridge, the path proxy, and ngrok as
    subprocesses, tracking PIDs the same way the shell functions do
    (one .pid file per service in ~/.scholarai-pids/), so the GUI and
    your terminal commands stay compatible with each other -- you can
    mix using the GUI and using startscholar_all/stopscholar_all and
    neither will get confused about what's running.
    """

    SERVICES = ["ollama", "bridge", "proxy", "ngrok"]

    def __init__(self, bridge_dir: Path, log_dir: Path, pid_dir: Path, ngrok_domain: str = ""):
        self.bridge_dir = bridge_dir
        self.log_dir = log_dir
        self.pid_dir = pid_dir
        self.ngrok_domain = ngrok_domain

    def _pid_file(self, name: str) -> Path:
        return self.pid_dir / f"{name}.pid"

    def _read_pid(self, name: str):
        pf = self._pid_file(name)
        if not pf.exists():
            return None
        try:
            return int(pf.read_text().strip())
        except (ValueError, OSError):
            return None

    def is_running(self, name: str) -> bool:
        """
        Checks via `pgrep`-equivalent logic: prefer matching by process
        pattern (more reliable than trusting a possibly-stale pidfile
        across reboots), falling back to the pidfile if pattern
        matching isn't applicable.
        """
        patterns = {
            "ollama": ["pgrep", "-x", "ollama"],
            "bridge": ["pgrep", "-f", "uvicorn storage_bridge"],
            "proxy": ["pgrep", "-f", "path_proxy.py"],
            "ngrok": ["pgrep", "-x", "ngrok"],
        }
        try:
            result = subprocess.run(patterns[name], capture_output=True, text=True, timeout=5)
            return bool(result.stdout.strip())
        except (subprocess.SubprocessError, OSError, FileNotFoundError):
            # pgrep itself unavailable (shouldn't happen on macOS) -- fall
            # back to "do we have a pidfile" as a weaker signal.
            return self._read_pid(name) is not None

    def start(self, name: str) -> str:
        """Returns a human-readable status message."""
        if self.is_running(name):
            return f"{name} is already running."

        log_file = open(self.log_dir / f"{name}.log", "a")

        try:
            if name == "ollama":
                proc = subprocess.Popen(
                    ["ollama", "serve"], stdout=log_file, stderr=subprocess.STDOUT,
                )
            elif name == "bridge":
                if not (self.bridge_dir / "storage_bridge.py").exists():
                    return f"storage_bridge.py not found in {self.bridge_dir} -- check the path."
                proc = subprocess.Popen(
                    ["uvicorn", "storage_bridge:app", "--host", "0.0.0.0", "--port", str(BRIDGE_PORT)],
                    cwd=str(self.bridge_dir), stdout=log_file, stderr=subprocess.STDOUT,
                )
            elif name == "proxy":
                if not (self.bridge_dir / "path_proxy.py").exists():
                    return f"path_proxy.py not found in {self.bridge_dir} -- check the path."
                proc = subprocess.Popen(
                    [sys.executable, "path_proxy.py"],
                    cwd=str(self.bridge_dir), stdout=log_file, stderr=subprocess.STDOUT,
                )
            elif name == "ngrok":
                if not self.ngrok_domain:
                    return "No ngrok domain configured -- set it in Settings first."
                proc = subprocess.Popen(
                    ["ngrok", "http", "8080", f"--domain={self.ngrok_domain}"],
                    stdout=log_file, stderr=subprocess.STDOUT,
                )
            else:
                return f"Unknown service: {name}"

            self._pid_file(name).write_text(str(proc.pid))
            return f"Started {name} (pid {proc.pid})."
        except FileNotFoundError as e:
            return f"Couldn't start {name}: command not found ({e}). Is it installed and on your PATH?"
        except OSError as e:
            return f"Couldn't start {name}: {e}"

    def stop(self, name: str) -> str:
        pid = self._read_pid(name)
        if pid is None:
            return f"No pidfile for {name} -- nothing to stop (or it wasn't started by this tool)."
        try:
            os.kill(pid, signal.SIGTERM)
            self._pid_file(name).unlink(missing_ok=True)
            return f"Stopped {name} (pid {pid})."
        except ProcessLookupError:
            self._pid_file(name).unlink(missing_ok=True)
            return f"{name} (pid {pid}) was not actually running -- pidfile cleared."
        except OSError as e:
            return f"Couldn't stop {name}: {e}"

    def start_all(self) -> list:
        return [self.start(name) for name in self.SERVICES]

    def stop_all(self) -> list:
        return [self.stop(name) for name in self.SERVICES]

    def status_all(self) -> dict:
        return {name: self.is_running(name) for name in self.SERVICES}


# ---------------------------------------------------------------------
# Bridge API client -- thin wrapper, mirrors core/bridge_client.py's
# style but lives standalone here since the GUI isn't part of the
# Streamlit app.
# ---------------------------------------------------------------------
def db_connect(db_path):
    """
    Shared SQLite connection helper for every place the Admin GUI reads/
    writes the bridge's database directly. Uses the same timeout +
    WAL-mode settings as storage_bridge.py's _get_conn() -- without
    these, a GUI read colliding with a bridge write could raise
    "database is locked" under the default ~5s timeout with no WAL.
    """
    import sqlite3
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class BridgeAPI:
    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def _headers(self):
        return {"x-bridge-secret": self.secret}

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_feedback(self) -> list:
        r = requests.get(f"{self.base_url}/feedback/list", headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()["feedback"]

    def resolve_feedback(self, feedback_id: int):
        r = requests.post(
            f"{self.base_url}/feedback/resolve", headers=self._headers(),
            data={"feedback_id": feedback_id}, timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def list_papers(self, published_only: bool = False) -> list:
        r = requests.get(
            f"{self.base_url}/papers/list", headers=self._headers(),
            params={"published_only": published_only}, timeout=10,
        )
        r.raise_for_status()
        return r.json()["papers"]
