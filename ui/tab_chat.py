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
"""
import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage

from core.llm import search_images
from core.analytics_store import record_study_activity
from features.chat_graph import vedic_graph
from features.socratic_tutor import wrap_socratic_instruction
from features.study_memory import build_memory_context


def _render_message(msg: dict):
    """Renders a single stored chat message, including images if it has any."""
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("image_results"):
            cols = st.columns(len(msg["image_results"]))
            for col, img in zip(cols, msg["image_results"]):
                with col:
                    st.image(img["image_url"], caption=img.get("title", ""), use_container_width=True)


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