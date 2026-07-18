"""
features/study_materials.py
=============================
AI-generated study aids: roadmaps, summaries, cheat sheets, concept maps,
formula sheets, a vocabulary builder, daily learning goals, and the
running "Mistake Notebook" profile that tracks a student's weak spots
across test attempts.
"""

import os
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_llm
from core.paths import get_chapter_paths, sanitize_filename
from core.vectorstore import get_chapter_text

# Plain-text material types: generated as a single LLM call, saved as .txt.
MATERIAL_PROMPTS = {
    "Study Roadmap": "Generate a step-by-step numbered learning roadmap. Break the chapter into logical phases.",
    "One-Page Summary": "Condense the core concepts and conclusions of this text into a readable, bulleted one-page summary.",
    "Exam Cheat Sheet": "Extract ONLY the most highly testable facts, dates, formulas, definitions, and key names.",
    "Key Formula Sheet": (
        "Extract every formula, equation, law, or critical numerical fact from this text. "
        "For each one, show: the formula/fact itself, what each symbol/term means, and one line on "
        "when/why it's used. If the text has no formulas, extract the most important hard facts instead "
        "(dates, definitions, classifications) in the same structured style."
    ),
    "Vocabulary Builder": (
        "Extract the key technical terms and vocabulary from this text. For EACH term provide, in this exact "
        "structure:\n### <Term> (<phonetic pronunciation in parentheses, e.g. (mai-TOH-sis)>)\n"
        "**Definition:** <clear, simple definition>\n**Example:** <one sentence using the term in context>\n\n"
        "Cover 8-15 of the most important terms."
    ),
}

# Material types that get parsed/structured rather than just saved as raw text.
CONCEPT_MAP_TYPE = "Concept Map"
DAILY_GOALS_TYPE = "Daily Learning Goals"

ALL_MATERIAL_TYPES = list(MATERIAL_PROMPTS.keys()) + [CONCEPT_MAP_TYPE, DAILY_GOALS_TYPE]


def generate_study_material(username: str, subject: str, chapter: str, material_type: str, language: str) -> str:
    """Generates a roadmap / summary / cheat sheet / formula sheet / vocabulary builder and saves it to disk."""
    paths = get_chapter_paths(username, subject, chapter)
    exact_text = get_chapter_text(username, subject, chapter)
    if not exact_text:
        return "Error: Chapter text not found for generation."

    context_slice = RecursiveCharacterTextSplitter(chunk_size=10000, chunk_overlap=0).split_text(exact_text)[0]

    try:
        sys_prompt = (
            f"{MATERIAL_PROMPTS.get(material_type, 'Analyze text.')}\n"
            f"CRITICAL: You MUST write the entire response in {language}."
        )

        # NOTE: BridgeChatLLM is NOT a LangChain Runnable -- it does not support
        # `prompt | llm | parser` piping (see core/llm.py's module docstring).
        # System prompt is passed straight to get_llm(), which BridgeChatLLM.invoke()
        # prepends as a system message automatically.
        llm = get_llm(username=username, system=sys_prompt)
        if llm is None:
            return "Error: could not initialize AI model (are you logged in?)."

        output = llm.invoke(f"Text:\n{context_slice}").content

        with open(os.path.join(paths["guides"], sanitize_filename(material_type) + ".txt"), "w", encoding="utf-8") as f:
            f.write(output)

        return output
    except Exception as e:
        return f"Generation failed: {str(e)}"


def generate_concept_map(username: str, subject: str, chapter: str, language: str) -> dict:
    """
    Extracts the chapter's key concepts as a flat list, for the UI to look
    up as individual labeled images (via web image search) -- one image
    per concept -- rather than a single generated diagram.

    NOTE: this used to generate a Mermaid flowchart/mindmap showing how
    concepts relate to each other, which was more informative (it showed
    actual relationships, not just a list) and didn't depend on image
    search finding anything relevant. That approach was dropped in favor
    of a universal image-search pipeline for all "diagram" requests
    app-wide. The tradeoff: this view can no longer show how concepts
    relate to one another, only what each one looks like/is illustrated
    as individually -- and image search results for an abstract concept
    name (e.g. "supply and demand") are far less reliable than for a
    concrete physical object (e.g. "the human heart").

    Returns {"success": bool, "concepts": [str, ...]} or
    {"success": False, "error": str}.
    """
    paths = get_chapter_paths(username, subject, chapter)
    exact_text = get_chapter_text(username, subject, chapter)
    if not exact_text:
        return {"success": False, "error": "Chapter text not found."}

    context_slice = RecursiveCharacterTextSplitter(chunk_size=10000, chunk_overlap=0).split_text(exact_text)[0]

    prompt = f"""Extract the 6-15 most important KEY CONCEPTS from this chapter text, in {language}.
Each concept should be a short noun phrase (2-5 words) suitable as a search term
(e.g. "Krebs cycle", "supply and demand", "Newton's second law").

Return ONLY a JSON array of strings, nothing else: ["concept 1", "concept 2", ...]

Chapter text:
{context_slice}"""

    try:
        llm = get_llm(username=username)
        if llm is None:
            return {"success": False, "error": "Could not initialize AI model (are you logged in?)."}

        raw = llm.invoke(prompt).content
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return {"success": False, "error": "Model did not return a recognizable concept list."}

        import json
        concepts = [str(c).strip() for c in json.loads(match.group()) if str(c).strip()]
        if not concepts:
            return {"success": False, "error": "Model returned an empty concept list."}

        with open(os.path.join(paths["guides"], "Concept_Map.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(concepts))

        return {"success": True, "concepts": concepts}
    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_daily_goals(username: str, subject: str, chapter: str, language: str, num_goals: int = 3) -> list:
    """
    Generates a short list of small, achievable objectives for today's
    study session on this chapter (e.g. "Review the 3 stages of the Krebs
    cycle", "Attempt 5 practice MCQs"). Returns a list of goal strings
    (not saved to disk -- these are meant to be fresh each session).
    """
    exact_text = get_chapter_text(username, subject, chapter)
    if not exact_text:
        return [f"Error: chapter text not found for {chapter}."]

    context_slice = RecursiveCharacterTextSplitter(chunk_size=6000, chunk_overlap=0).split_text(exact_text)[0]

    prompt = f"""Generate EXACTLY {num_goals} small, specific, achievable study goals for TODAY's session on
this chapter, in {language}. Each goal should be completable in 10-20 minutes and phrased as an
action (e.g. "Summarize the causes of X in your own words", "Take a 5-question quiz on Y").

Return ONLY a JSON array of strings, nothing else: ["goal 1", "goal 2", "goal 3"]

Chapter text:
{context_slice}"""

    try:
        llm = get_llm(username=username)
        if llm is None:
            return [f"Review the key concepts in {chapter}.", "Take a short practice quiz.", "Make 5 flashcards."]

        raw = llm.invoke(prompt).content
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return [f"Review the key concepts in {chapter}.", "Take a short practice quiz.", "Make 5 flashcards."]

        import json
        goals = json.loads(match.group())
        return [str(g) for g in goals][:num_goals]
    except Exception:
        return [f"Review the key concepts in {chapter}.", "Take a short practice quiz.", "Make 5 flashcards."]


def update_mistake_profile(
    username: str, subject: str, chapter: str, wrong_obj: list, subj_answers: list, score: int, total_qs: int, lang: str
):
    """
    Rewrites the chapter's cumulative Mistake Notebook profile after a new
    test attempt, merging new mistakes/strengths into the existing record.
    """
    paths = get_chapter_paths(username, subject, chapter)
    profile_path = os.path.join(paths["guides"], "Mistake_Notebook_Profile.txt")

    existing_profile = ""
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8", errors="replace") as f:
            existing_profile = f.read()

    prompt = f"""You are an elite AI Study Tracker.
PREVIOUS profile: {existing_profile if existing_profile else 'First test taken.'}
LATEST TEST DATA (Score: {score}/{total_qs}): Mistakes: {wrong_obj} | Subjective: {subj_answers}
TASK: Rewrite their overarching Mistake Notebook Profile in {lang}. Categorize their knowledge.
Use EXACTLY this Markdown format:
### 🌟 Mastered Concepts (Perfected)
### 📈 Currently Learning (Improving)
### ⚠️ Needs Focus (Core Weaknesses)
### 📝 Error Log from Latest Test
"""

    try:
        llm = get_llm(username=username)
        if llm is None:
            return "Error updating profile: could not initialize AI model."

        res = llm.invoke(prompt).content
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(res)

        return res
    except Exception:
        return "Error updating profile."
