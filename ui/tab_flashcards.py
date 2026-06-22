"""
ui/tab_flashcards.py
======================
The "Flashcards" tab: deck generation (and adding more cards to an
existing deck), a Leitner-box style flip-card study loop, and a
dedicated "Practice Forgotten" mode that filters to only box-1 cards
(the ones marked "Forgot") instead of mixing them back into the full deck.
"""
import os
import json
import html

import streamlit as st

from core.paths import get_chapter_paths
from core.analytics_store import record_study_activity
from features.flashcards import generate_flashcards


def _load_deck(fc_path: str) -> list:
    # Fix Issue #9: Graceful file read.
    try:
        with open(fc_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_deck(fc_path: str, fc_data: list):
    with open(fc_path, "w", encoding="utf-8") as f:
        json.dump(fc_data, f, indent=4)


def _render_card(card: dict, idx_in_deck: int, deck_len: int):
    st.markdown(
        f"<div style='text-align:center; color:#888; margin-bottom:10px;'>Card {idx_in_deck + 1} of {deck_len}</div>",
        unsafe_allow_html=True,
    )
    # Fix Issue #3 & #12: XSS Escape and CSS animation link.
    flip_class = "flipped" if st.session_state.fc_flipped else ""
    html_card = f"""
    <div class="flashcard-container">
        <div class="flashcard {flip_class}">
            <div class="flashcard-face flashcard-front">
                <div class="tag-pill">Box {card.get('box', 1)}</div>
                {html.escape(card['front'])}
            </div>
            <div class="flashcard-face flashcard-back">
                {html.escape(card['back'])}
            </div>
        </div>
    </div>
    """
    st.markdown(html_card, unsafe_allow_html=True)
    st.write("")


def _render_study_loop(
    username: str,
    fc_path: str,
    fc_data: list,
    study_deck: list,
    mastered_message: str,
    key_prefix: str,
):
    """Shared review loop used by both 'Study All' and 'Practice Forgotten' modes."""
    if not study_deck:
        st.success(mastered_message)
        return

    if st.session_state.fc_idx >= len(study_deck):
        st.session_state.fc_idx = 0
    card = study_deck[st.session_state.fc_idx]
    master_idx = fc_data.index(card)

    _render_card(card, st.session_state.fc_idx, len(study_deck))

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button(
                "❌ Forgot",
                use_container_width=True,
                key=f"{key_prefix}_forgot_btn",
        ):
            fc_data[master_idx]["box"] = 1
            _save_deck(fc_path, fc_data)
            record_study_activity(username)
            # Fix Issue #11: Advance index on forgot.
            st.session_state.fc_idx += 1
            st.session_state.fc_flipped = False
            st.rerun()
    with c2:
        if st.button(
                "🔄 Flip Card",
                type="primary",
                use_container_width=True,
                key=f"{key_prefix}_flip_btn",
        ):
            st.session_state.fc_flipped = not st.session_state.fc_flipped
            st.rerun()
    with c3:
        if st.button(
                "✅ Knew It",
                use_container_width=True,
                key=f"{key_prefix}_knew_it_btn",
        ):
            fc_data[master_idx]["box"] = card.get("box", 1) + 1
            _save_deck(fc_path, fc_data)
            record_study_activity(username)
            # Fix Issue #10: Advance index on correct.
            st.session_state.fc_idx += 1
            st.session_state.fc_flipped = False
            st.rerun()


def render_flashcards_tab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.header("🗂️ Intelligent Flashcards")

    if active_chapter == "Select Chapter" or active_subject == "Select Subject":
        st.warning("Select Subject and Chapter first.")
        return

    paths = get_chapter_paths(username, active_subject, active_chapter)
    fc_path = os.path.join(paths["flashcards"], "Flashcards.json")
    deck_exists = os.path.exists(fc_path)

    if not deck_exists:
        st.info("No deck yet for this chapter.")
        fc_count = st.number_input("How many to generate?", 5, 100, 20)
        if st.button("🚀 Generate Deck", type="primary"):
            with st.spinner("Building flashcards..."):
                res = generate_flashcards(username, active_subject, active_chapter, fc_count, target_language, append=False)
                st.success(res) if "Success" in res else st.error(res)
                if "Success" in res:
                    st.rerun()
        return

    fc_data = _load_deck(fc_path)
    if not fc_data:
        st.warning("This deck's file is empty or unreadable. Generate a new one below.")
        fc_count = st.number_input("How many to generate?", 5, 100, 20, key="regen_count")
        if st.button("🚀 Generate Deck", type="primary", key="regen_btn"):
            with st.spinner("Building flashcards..."):
                res = generate_flashcards(username, active_subject, active_chapter, fc_count, target_language, append=False)
                st.success(res) if "Success" in res else st.error(res)
                if "Success" in res:
                    st.rerun()
        return

    forgotten_deck = [c for c in fc_data if c.get("box", 1) == 1]
    learning_deck = [c for c in fc_data if c.get("box", 1) < 5]

    mode_tab_all, mode_tab_forgotten, mode_tab_manage = st.tabs(
        [f"📖 Study All ({len(learning_deck)})", f"🔁 Practice Forgotten ({len(forgotten_deck)})", "⚙️ Manage Deck"]
    )

    with mode_tab_all:
        _render_study_loop(
            username,
            fc_path,
            fc_data,
            learning_deck,
            "🎉 Deck Mastered! Every card is at Box 5.",
            "all",
        )
    with mode_tab_forgotten:
        _render_study_loop(
            username,
            fc_path,
            fc_data,
            forgotten_deck,
            "🎉 Nothing to review — no forgotten cards right now!",
            "forgotten",
        )

    with mode_tab_manage:
        st.write(f"Deck size: **{len(fc_data)} cards** ({len(forgotten_deck)} marked Forgot)")

        st.subheader("➕ Generate More Cards")
        more_count = st.number_input("How many additional cards?", 5, 100, 10, key="more_count")
        if st.button("Generate More", key="generate_more_btn"):
            with st.spinner("Generating additional flashcards..."):
                res = generate_flashcards(
                    username, active_subject, active_chapter, more_count, target_language, append=True
                )
                st.success(res) if "Success" in res else st.error(res)
                if "Success" in res:
                    st.rerun()

        st.markdown("---")
        st.subheader("🔄 Reset Deck Progress")
        st.caption("Resets every card back to Box 1 without deleting any cards.")
        if st.button("Reset All Progress"):
            for c in fc_data:
                c["box"] = 1
            _save_deck(fc_path, fc_data)
            st.session_state.fc_idx = 0
            st.success("Progress reset.")
            st.rerun()
