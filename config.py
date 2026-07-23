"""
config.py
=========
Central place for app-wide constants, Streamlit page configuration,
and global CSS.
"""
import os
import tempfile
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_DIR = os.path.join(tempfile.gettempdir(), "scholarai_users")
os.makedirs(USERS_DIR, exist_ok=True)

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

# LEGACY / NO LONGER READ BY core/llm.py -- kept only in case something
# else in the codebase still imports it (grep for OLLAMA_BASE_URL before
# deleting outright). This used to be the base_url ChatOllama connected
# to directly, which was the root cause of the tier/subscription bypass:
# it let the app reach Ollama through path_proxy.py's old "/ollama" route
# without ever touching storage_bridge.py's tier/subscription checks.
# core/llm.py's BridgeChatLLM now routes every LLM call through
# BRIDGE_BASE_URL + "/ollama/chat" instead (via bridge_client.ollama_chat()),
# so this value has no effect on LLM calls anymore. Safe to delete once
# you've confirmed no other module reads it directly.
OLLAMA_BASE_URL = st.secrets.get("OLLAMA_BASE_URL", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

BRIDGE_BASE_URL = st.secrets.get("BRIDGE_BASE_URL", os.environ.get("BRIDGE_BASE_URL", "")).rstrip("/")
BRIDGE_SHARED_SECRET = st.secrets.get("BRIDGE_SHARED_SECRET", os.environ.get("BRIDGE_SHARED_SECRET", ""))

# The one account (in-app, not the desktop admin_gui.py) that gets the
# "🔐 Admin" tab and can view/clear any student's chat history. Stored
# lowercase since ui/auth.py lowercases every username on login/signup
# before comparing/storing it. ui/auth.py's signup form also blocks new
# accounts from registering this exact username, so it can't be squatted.
ADMIN_USERNAME = "bhishma rane"

MIN_PASSWORD_LENGTH = 8

DEFAULT_THEME_COLOR = "#5a691d"  # olive green


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _darken(hex_color: str, factor: float = 0.8) -> str:
    """Returns a slightly darker shade, used for hover states."""
    r, g, b = _hex_to_rgb(hex_color)
    return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"


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
    ("theme_color", DEFAULT_THEME_COLOR),
]


def init_session_state():
    for key, default in SESSION_DEFAULTS:
        if key not in st.session_state:
            st.session_state[key] = default


def configure_page():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=APP_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_brand_header():
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


def build_global_css(accent_color: str = DEFAULT_THEME_COLOR) -> str:
    hover_color = _darken(accent_color)
    return f"""
<style>
.flashcard-container {{ perspective: 1000px; width: 100%; max-width: 600px; margin: 0 auto; height: 350px; }}
.flashcard {{ width: 100%; height: 100%; position: relative; transition: transform 0.6s; transform-style: preserve-3d; }}
.flashcard.flipped {{ transform: rotateY(180deg); }}
.flashcard-face {{ position: absolute; width: 100%; height: 100%; backface-visibility: hidden; display: flex; flex-direction: column; justify-content: center; align-items: center; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); padding: 30px; text-align: center; font-size: 24px; }}
.flashcard-front {{ background-color: #ffffff; border: 2px solid #e0e0e0; color: #333; }}
.flashcard-back {{ background-color: {accent_color}; color: white; transform: rotateY(180deg); }}
.tag-pill {{ background: #eee; border-radius: 15px; padding: 5px 15px; font-size: 12px; font-weight: bold; position: absolute; top: 15px; left: 15px; color: #555;}}

button[kind="primary"] {{ background-color: {accent_color} !important; border-color: {accent_color} !important; }}
button[kind="primary"]:hover {{ background-color: {hover_color} !important; border-color: {hover_color} !important; }}
.stTabs [aria-selected="true"] {{ color: {accent_color} !important; border-bottom-color: {accent_color} !important; }}
div[data-baseweb="slider"] > div > div {{ background: {accent_color} !important; }}
</style>
"""


def inject_css(accent_color: str = DEFAULT_THEME_COLOR):
    st.markdown(build_global_css(accent_color), unsafe_allow_html=True)
