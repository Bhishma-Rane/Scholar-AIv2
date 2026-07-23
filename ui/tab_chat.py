"""
ui/tab_chat.py
===============
The "Tutor" tab: a chat interface backed by the LangGraph state machine.

'!diagram <topic>' always resolves to a real labeled image via free
DuckDuckGo image search (e.g. "!diagram the human heart",
"!diagram the water cycle", "!diagram headphones"). Mermaid-based
diagram generation was removed: it required an LLM classification step
(structural vs. visual) that was unreliable on small local models, and
even when classification worked, LLM-generated Mermaid syntax was prone
to parse errors. Going search-only trades away crisp process diagrams
(flowcharts/cycles/hierarchies) for universal coverage of any topic,
including plain physical objects that aren't anatomy (e.g. "headphones").

A "Socratic Tutor" toggle changes the chat's teaching style to guide with
questions instead of giving direct answers immediately.

CHANGED: chat history is now persisted per (student, subject, chapter) via
core.content_store (bridge-backed), not just kept in st.session_state for
the life of the browser session. This is what makes two things possible:
  1. A student's conversation survives a refresh/reconnect instead of
     vanishing, and correctly shows THAT chapter's own history when they
     switch subjects/chapters (previously chat_messages was one flat list
     that didn't care which chapter was active).
  2. The admin panel (ui/tab_admin.py, config.ADMIN_USERNAME) can load and
     clear any student's chat for a given subject/chapter, since it's the
     same content_store.load_chat_messages()/clear_chat_messages() calls
     just pointed at a different username.
Storage is scoped by username on every call (see core/content_store.py),
so a regular student can only ever load/save their OWN chat -- the app
never passes anything but the logged-in user's own username here.

Also adds CSS to pin the chat input to the bottom of the viewport, since
Streamlit's default docking can visually drift once a tab layout, sidebar,
and admin panel are all on the page at once.
"""
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage

from core.llm import search_images
from core.analytics_store import record_study_activity
from core import content_store
from features.chat_graph import vedic_graph
from features.socratic_tutor import wrap_socratic_instruction
from features.study_memory import build_memory_context


def _inject_pinned_input_css():
    """Forces the chat input to stay fixed to the bottom of the viewport,
    like a modern chat app, instead of relying on Streamlit's default
    (which can drift depending on layout/sidebar state). Also pads the
    bottom of the page so the last message isn't hidden behind it."""
    st.markdown(
        """
        <style>
        div[data-testid="stChatInput"] {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            z-index: 999;
            background-color: var(--background-color, #ffffff);
            padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom, 0px)) 1rem;
            box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.06);
        }
        /* Match the input's left offset to the sidebar so it doesn't run under it */
        @media (min-width: 768px) {
            section[data-testid="stSidebar"][aria-expanded="true"] ~ div div[data-testid="stChatInput"] {
                left: var(--sidebar-width, 21rem);
            }
        }
        /* Keep the last chat bubble from being hidden behind the fixed input */
        div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stChatMessage"]:last-of-type) {
            padding-bottom: 6rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_message(msg: dict):
    """Renders a single stored chat message, including images if it has any."""
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("image_results"):
            cols = st.columns(len(msg["image_results"]))
            for col, img in zip(cols, msg["image_results"]):
                with col:
                    st.image(img["image_url"], caption=img.get("title", ""), use_container_width=True)


def _sync_chat_scope(username: str, active_subject: str, active_chapter: str):
    """Loads this student's persisted chat for the current (subject, chapter)
    into session state, but only when the scope actually changed -- so we're
    not re-fetching from the bridge on every widget interaction/rerun. Falls
    back to an empty, unsaved scratch list while no real subject/chapter is
    selected yet."""
    if active_subject == "Select Subject" or active_chapter == "Select Chapter":
        st.session_state.chat_messages = []
        st.session_state["_chat_scope"] = None
        return

    scope_key = (username, active_subject, active_chapter)
    if st.session_state.get("_chat_scope") != scope_key:
        st.session_state.chat_messages = content_store.load_chat_messages(
            username, active_subject, active_chapter
        )
        st.session_state["_chat_scope"] = scope_key


def _persist_chat(username: str, active_subject: str, active_chapter: str):
    """Saves the current in-memory chat_messages back to storage. No-op while
    no real subject/chapter is selected (nothing to key the save under)."""
    if active_subject == "Select Subject" or active_chapter == "Select Chapter":
        return
    content_store.save_chat_messages(username, active_subject, active_chapter, st.session_state.chat_messages)


def _handle_diagram_command(prompt: str, active_chapter: str):
    """
    Resolves the '!diagram <topic>' command to a labeled image search,
    renders the result, and appends it to chat history (including the
    raw image data needed to replay it identically on rerun).
    """
    topic = prompt.replace("!diagram", "", 1).strip()
    if not topic:
        msg = "Tell me what to diagram — e.g. `!diagram the water cycle` or `!diagram the human heart`."
        st.markdown(msg)
        st.session_state.chat_messages.append({"role": "assistant", "content": msg})
        return

    with st.spinner("Searching for a labeled image..."):
        images = search_images(f"{topic} labeled diagram educational", max_results=4)

    if images:
        caption = f"Here's a labeled diagram for: {topic}"
        st.markdown(caption)
        cols = st.columns(len(images))
        for col, img in zip(cols, images):
            with col:
                st.image(img["image_url"], caption=img.get("title", ""), use_container_width=True)
        st.session_state.chat_messages.append(
            {"role": "assistant", "content": caption, "image_results": images}
        )
    else:
        error_text = (
            f"Couldn't find a labeled image for '{topic}' right now (image search may be unavailable). "
            "Try rephrasing, or check your network connection."
        )
        st.markdown(error_text)
        st.session_state.chat_messages.append({"role": "assistant", "content": error_text})


def render_chat_tab(username: str, active_subject: str, active_chapter: str, data_source: str, target_language: str):
    st.header("Interactive Content Tutor")
    _inject_pinned_input_css()
    _sync_chat_scope(username, active_subject, active_chapter)

    if active_subject == "Select Subject" or active_chapter == "Select Chapter":
        st.info("👆 Pick a subject and active chapter in the Workspace tab to start chatting.")

    socratic_mode = st.toggle(
        "🧠 Socratic Tutor Mode",
        value=st.session_state.get("socratic_mode", False),
        help="When on, the tutor guides you toward the answer with questions instead of giving it directly.",
    )
    st.session_state.socratic_mode = socratic_mode

    for msg in st.session_state.chat_messages:
        _render_message(msg)

    prompt = st.chat_input("Ask about your material... (try !diagram <topic>)")
    if not prompt:
        return

    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        if prompt.strip().startswith("!diagram"):
            _handle_diagram_command(prompt, active_chapter)
            record_study_activity(username)
        else:
            with st.spinner("Analyzing..."):
                try:
                    effective_question = wrap_socratic_instruction(prompt) if socratic_mode else prompt
                    memory_context = build_memory_context(username, subject=active_subject)
                    if memory_context:
                        effective_question = f"{memory_context}\n\n{effective_question}"
                    state_output = vedic_graph.invoke(
                        {
                            "question": effective_question,
                            "chat_history": st.session_state.session_history,
                            "context": "",
                            "relevance_score": 0.0,
                            "source_file": "Unknown",
                            "command_mode": "standard",
                            "quiz_count": 0,
                            "response": "",
                            "active_chapter": active_chapter,
                            "data_source": data_source.replace("📚 ", "").replace("🌐 ", ""),
                            "language": target_language,
                            "username": username,
                            "subject": active_subject,
                        }
                    )
                    response_text = state_output.get("response", "Error.")

                    # Fix Issue #33: Truncate history to prevent memory bloat.
                    # NOTE: the original (un-wrapped) prompt is stored in history,
                    # not the Socratic-wrapped version, so history stays clean.
                    st.session_state.session_history.extend(
                        [HumanMessage(content=prompt), AIMessage(content=response_text)]
                    )
                    if len(st.session_state.session_history) > 10:
                        st.session_state.session_history = st.session_state.session_history[-10:]

                    record_study_activity(username)

                except Exception as e:
                    response_text = f"System Error: {str(e)}"

                st.markdown(response_text)
                st.session_state.chat_messages.append({"role": "assistant", "content": response_text})

    # Persist once per turn (covers both the !diagram branch and the normal
    # LLM branch above), so the next load for this (subject, chapter) picks
    # up right where this conversation left off -- and so the admin panel
    # sees it too.
    _persist_chat(username, active_subject, active_chapter)
