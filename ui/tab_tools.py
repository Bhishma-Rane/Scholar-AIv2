"""
ui/tab_tools.py
================
NOTE: superseded by ui/tab_study.py (see that file's docstring) -- this
module is not imported or wired into the app anymore. Left in the repo
for reference; updated below only so it doesn't silently break if it's
ever revived, not because it's part of the live app.

The "Study Tools" tab: on-demand study material generation (roadmaps,
summaries, cheat sheets, formula sheets, vocabulary builder), the
Concept Map (rendered as labeled images, one per key concept, via web
image search), Daily Learning Goals (a simple checklist), and the AI
Mistake Notebook profile viewer.
"""
import streamlit as st

from core.content_store import load_text, delete as delete_content
from core.llm import search_images
from core.analytics_store import record_study_activity
from features.study_materials import (
    MATERIAL_PROMPTS,
    generate_study_material,
    generate_concept_map,
    generate_daily_goals,
    GUIDES_CATEGORY,
)


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


def render_tools_tab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.header("Pro Study Suite")

    gen_tab, concept_tab, goals_tab, mistakes_tab = st.tabs(
        ["📑 Generate Materials", "🗺️ Concept Map", "🎯 Daily Goals", "📓 Mistake Notebook"]
    )

    with gen_tab:
        _render_text_material_generator(username, active_subject, active_chapter, target_language)

    with concept_tab:
        _render_concept_map_generator(username, active_subject, active_chapter, target_language)

    with goals_tab:
        _render_daily_goals(username, active_subject, active_chapter, target_language)

    with mistakes_tab:
        if active_chapter != "Select Chapter" and active_subject != "Select Subject":
            profile_data = load_text(username, active_subject, active_chapter, GUIDES_CATEGORY, "Mistake_Notebook_Profile.txt")
            if profile_data and profile_data.strip():
                st.markdown(profile_data)
                if st.button("🗑️ Reset Profile"):
                    delete_content(username, active_subject, active_chapter, GUIDES_CATEGORY, "Mistake_Notebook_Profile.txt")
                    st.rerun()
            else:
                st.info("Profile is empty. Take a test to build it!")
        else:
            st.warning("Select an Active Chapter.")
            
