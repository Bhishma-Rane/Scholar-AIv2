"""
config.py
=========
Central place for app-wide constants, Streamlit page configuration,
and global CSS. Import this once from app.py before anything else
that touches the Streamlit UI.
"""
import os
import streamlit as st

# ---------------------------------------------------------------------
# 📁 BASE PATHS
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_DIR = os.path.join(BASE_DIR, "users")
os.makedirs(USERS_DIR, exist_ok=True)
# NOTE: USERS_DIR is still used for local scratch space (e.g. the ChromaDB
# that gets rebuilt fresh on every container start — see core/vectorstore.py).
# It is intentionally NOT used anymore for anything that must survive a
# restart: credentials, subjects, and uploaded PDFs all live on the
# storage bridge now (see core/bridge_client.py).

# ---------------------------------------------------------------------
# ⚙️ APP CONSTANTS
# ---------------------------------------------------------------------
APP_NAME = "ScholarAI"
APP_VENDOR = "by AuraStudios"
APP_TAGLINE = "Learn. Understand. Master."
APP_TITLE = f"{APP_NAME} {APP_VENDOR}"
APP_ICON = "🎓"

LANGUAGES = ["English", "Hindi", "Sanskrit"]
DATA_SOURCES = ["📚 Local Docs", "🌐 Internet Search"]
MATERIAL_TYPES = ["Study Roadmap", "One-Page Summary", "Exam Cheat Sheet", "Key Formula Sheet", "Vocabulary Builder"]

OLLAMA_MAIN_MODEL = "llama3"
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_BASE_URL = st.secrets.get("OLLAMA_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

# ---------------------------------------------------------------------
# 🌉 STORAGE BRIDGE (persistent storage on Bhishma's Windows laptop)
# ---------------------------------------------------------------------
# BRIDGE_BASE_URL: the ngrok static domain pointing at storage_bridge.py,
# e.g. "https://your-static-bridge-domain.ngrok-free.app" — NO trailing slash.
# BRIDGE_SHARED_SECRET: must exactly match the BRIDGE_SHARED_SECRET
# environment variable set on the Windows laptop before starting the bridge.
#
# Set both in Streamlit Cloud's app settings -> Secrets, e.g.:
#   BRIDGE_BASE_URL = "https://your-static-bridge-domain.ngrok-free.app"
#   BRIDGE_SHARED_SECRET = "the-same-long-random-string-as-the-laptop"
BRIDGE_BASE_URL = st.secrets.get("BRIDGE_BASE_URL", os.environ.get("BRIDGE_BASE_URL", "")).rstrip("/")
BRIDGE_SHARED_SECRET = st.secrets.get("BRIDGE_SHARED_SECRET", os.environ.get("BRIDGE_SHARED_SECRET", ""))

# Auth
MIN_PASSWORD_LENGTH = 8

# Session-state defaults: (key, default_value)
SESSION_DEFAULTS = [
    ("chat_messages", []),
    ("session_history", []),
    ("quiz_active", False),
    ("quiz_data", []),
    ("exam_submitted", False),
    ("fc_idx", 0),
    ("fc_flipped", False),
    ("logged_in_user", None),
    ("login_token", None),
    ("user_answers", {}),
    ("marked_review", {}),
    ("current_q", 0),
    ("evaluation_mode", "Practice"),
    ("ai_summary", ""),
    ("final_score", 0),
    ("grading_result", None),
    ("negative_marking_enabled", False),
    ("socratic_mode", False),
    ("daily_goals_list", []),
    ("daily_goals_done", []),
    ("dashboard_cache", None),
    ("tutorial_selected_pathway", None),
    ("tutorial_step_index", 0),
]


def init_session_state():
    """Fix Issue #1: Explicit, typed state initialization."""
    for key, default in SESSION_DEFAULTS:
        if key not in st.session_state:
            st.session_state[key] = default


def configure_page():
    """Sets Streamlit page config. Must be the first st.* call in app.py."""
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_brand_header():
    """Renders the ScholarAI logo/title/tagline. Called once near the top of app.py."""
    st.markdown(
        f"""
        <div style="display:flex; align-items:baseline; gap:12px; margin-bottom:0;">
            <span style="font-size:32px; font-weight:800;">{APP_ICON} {APP_NAME}</span>
            <span style="font-size:14px; color:#888;">{APP_VENDOR}</span>
        </div>
        <div style="font-size:14px; color:#666; font-style:italic; margin-top:-4px; margin-bottom:12px;">
            {APP_TAGLINE}
        </div>
        """,
        unsafe_allow_html=True,
    )


GLOBAL_CSS = """
<style>
.flashcard-container { perspective: 1000px; width: 100%; max-width: 600px; margin: 0 auto; height: 350px; }
.flashcard { width: 100%; height: 100%; position: relative; transition: transform 0.6s; transform-style: preserve-3d; }
.flashcard.flipped { transform: rotateY(180deg); }
.flashcard-face { position: absolute; width: 100%; height: 100%; backface-visibility: hidden; display: flex; flex-direction: column; justify-content: center; align-items: center; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); padding: 30px; text-align: center; font-size: 24px; }
.flashcard-front { background-color: #ffffff; border: 2px solid #e0e0e0; color: #333; }
.flashcard-back { background-color: #4CAF50; color: white; transform: rotateY(180deg); }
.tag-pill { background: #eee; border-radius: 15px; padding: 5px 15px; font-size: 12px; font-weight: bold; position: absolute; top: 15px; left: 15px; color: #555;}
</style>
"""


def inject_css():
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
