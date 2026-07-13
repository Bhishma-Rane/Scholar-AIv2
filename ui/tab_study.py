"""
ui/tab_study.py
==================
The "Study" tab: everything for working WITH a chapter's material --
generating study aids, drilling flashcards, reviewing the AI mistake
notebook, and viewing the source document. Combines what used to be
three separate tabs (Study Tools, Flashcards, Viewer) into one, since
they're all "things you do with the chapter you're currently studying"
rather than separate concerns.

Sub-tabs:
  - Generate: study material generation, concept map, daily goals
    (unchanged from the old tab_tools.py, just nested one level deeper)
  - Flashcards: unchanged from the old tab_flashcards.py
  - Mistake Notebook: unchanged, moved from tab_tools.py's 4th sub-tab
  - Viewer: unchanged from the old tab_viewer.py

This file ASSUMES render_flashcards_tab and render_viewer_tab already
exist in ui/tab_flashcards.py and ui/tab_viewer.py respectively -- it
imports and calls them as-is, just nested under a sub-tab instead of a
top-level tab. No changes needed to those two files.
"""
import os

import streamlit as st

from core.paths import get_chapter_paths
from core.llm import search_images
from core.analytics_store import record_study_activity
from features.study_materials import (
    MATERIAL_PROMPTS,
    generate_study_material,
    generate_concept_map,
    generate_daily_goals,
)
from ui.tab_flashcards import render_flashcards_tab
from ui.tab_viewer import render_viewer_tab


def _render_text_material_generator(username: str, active_subject: str, active_chapter: str, target_language: str):
    mat_type = st.selectbox("Material Type:", list(MATERIAL_PROMPTS.keys()))
    if st.button("Generate Document", type="primary"):
        if active_chapter == "Select Chapter":
            st.error("Select a chapter first!")
        else:
            with st.spinner(f"Compiling {mat_type}..."):
                result = generate_study_material(username, active_subject, active_chapter, mat_type, target_language)
                record_study_activity(username)
                st.markdown(f"### {active_chapter} - {mat_type}")
                st.markdown(result)


def _render_concept_map_generator(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.caption(
        "Shows a labeled image for each of this chapter's key concepts. "
        "(Note: these are independent images, not a diagram of how the concepts relate to each other.)"
    )
    if st.button("🗺️ Generate Concept Map", type="primary"):
        if active_chapter == "Select Chapter":
            st.error("Select a chapter first!")
        else:
            with st.spinner("Identifying key concepts..."):
                result = generate_concept_map(username, active_subject, active_chapter, target_language)
                record_study_activity(username)
            if result["success"]:
                st.session_state["concept_map_concepts"] = result["concepts"]
            else:
                st.error(f"Couldn't generate a concept map: {result['error']}")
                st.session_state.pop("concept_map_concepts", None)

    concepts = st.session_state.get("concept_map_concepts")
    if concepts:
        for concept in concepts:
            st.markdown(f"#### {concept}")
            with st.spinner(f"Searching for an image of '{concept}'..."):
                images = search_images(f"{concept} labeled diagram educational", max_results=4)
            if images:
                cols = st.columns(len(images))
                for col, img in zip(cols, images):
                    with col:
                        st.image(img["image_url"], caption=img.get("title", ""), use_container_width=True)
            else:
                st.info(f"No image found for '{concept}'.")


def _render_daily_goals(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.caption("A few small, achievable objectives for today's study session on this chapter.")
    num_goals = st.slider("How many goals?", 2, 5, 3)
    if st.button("🎯 Generate Today's Goals", type="primary"):
        if active_chapter == "Select Chapter":
            st.error("Select a chapter first!")
        else:
            with st.spinner("Setting today's goals..."):
                goals = generate_daily_goals(username, active_subject, active_chapter, target_language, num_goals)
                record_study_activity(username)
                st.session_state["daily_goals_list"] = goals
                st.session_state["daily_goals_done"] = [False] * len(goals)

    if st.session_state.get("daily_goals_list"):
        st.markdown("#### Today's Goals")
        for i, goal in enumerate(st.session_state["daily_goals_list"]):
            done = st.checkbox(goal, value=st.session_state["daily_goals_done"][i], key=f"goal_{i}")
            st.session_state["daily_goals_done"][i] = done
        completed = sum(st.session_state["daily_goals_done"])
        total = len(st.session_state["daily_goals_list"])
        st.progress(completed / total if total else 0, text=f"{completed}/{total} goals completed")
        if completed == total and total > 0:
            st.success("🎉 All goals completed for today!")


def _render_manage_materials(username: str, active_subject: str, active_chapter: str):
    if active_chapter == "Select Chapter" or active_subject == "Select Subject":
        return
    paths = get_chapter_paths(username, active_subject, active_chapter)
    guides_dir = paths["guides"]
    existing = sorted(
        f for f in os.listdir(guides_dir)
        if f.endswith(".txt") and f != "Mistake_Notebook_Profile.txt"
    ) if os.path.isdir(guides_dir) else []

    if not existing:
        return

    with st.expander("🗑️ Manage Generated Materials", expanded=False):
        for fname in existing:
            label = fname.rsplit(".", 1)[0].replace("_", " ")
            col1, col2 = st.columns([4, 1])
            col1.write(label)
            if col2.button("Delete", key=f"del_material_{fname}"):
                os.remove(os.path.join(guides_dir, fname))
                st.rerun()


def _render_generate_subtab(username: str, active_subject: str, active_chapter: str, target_language: str):
    gen_mode = st.radio(
        "What would you like to generate?",
        ["📑 Study Material", "🗺️ Concept Map", "🎯 Daily Goals"],
        horizontal=True,
        key="study_generate_mode",
    )
    st.markdown("---")
    if gen_mode == "📑 Study Material":
        _render_text_material_generator(username, active_subject, active_chapter, target_language)
    elif gen_mode == "🗺️ Concept Map":
        _render_concept_map_generator(username, active_subject, active_chapter, target_language)
    else:
        _render_daily_goals(username, active_subject, active_chapter, target_language)

    st.markdown("---")
    _render_manage_materials(username, active_subject, active_chapter)


def _render_mistake_notebook(username: str, active_subject: str, active_chapter: str):
    if active_chapter != "Select Chapter" and active_subject != "Select Subject":
        paths = get_chapter_paths(username, active_subject, active_chapter)
        profile_file = os.path.join(paths["guides"], "Mistake_Notebook_Profile.txt")
        if os.path.exists(profile_file):
            with open(profile_file, "r", encoding="utf-8", errors="replace") as f:
                profile_data = f.read()
            if profile_data.strip():
                st.markdown(profile_data)
                if st.button("🗑️ Reset Profile"):
                    open(profile_file, "w").close()
                    st.rerun()
            else:
                st.info("Profile is empty. Take a test to build it!")
        else:
            st.info("Profile is empty. Take a test to build it!")
    else:
        st.warning("Select an Active Chapter.")


def render_study_tab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.header("📚 Study")

    gen_subtab, flash_subtab, mistakes_subtab, viewer_subtab = st.tabs(
        ["✨ Generate", "🗂️ Flashcards", "📓 Mistake Notebook", "📄 Viewer"]
    )

    with gen_subtab:
        _render_generate_subtab(username, active_subject, active_chapter, target_language)

    with flash_subtab:
        render_flashcards_tab(username, active_subject, active_chapter, target_language)

    with mistakes_subtab:
        _render_mistake_notebook(username, active_subject, active_chapter)

    with viewer_subtab:
        render_viewer_tab(username, active_subject, active_chapter)
