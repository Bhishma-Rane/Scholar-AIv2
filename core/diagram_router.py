"""
core/diagram_router.py
========================
Decides whether a requested "diagram" topic should be rendered as a
structural Mermaid diagram (flowchart/mindmap/etc — for processes, cycles,
systems, hierarchies) or fetched as a real labeled image via web image
search (for anatomical/realistic subjects like "the human heart" that
Mermaid fundamentally cannot draw).

Classification is a two-step process:
  1. A fast keyword pre-check catches obvious VISUAL topics (anatomy,
     real-world physical objects/structures) without any model call.
     Small local models (llama3.2) are unreliable on single-word
     classification tasks -- in testing, "the human heart" was
     misclassified as STRUCTURAL despite being explicitly listed as a
     VISUAL example in the prompt. A keyword check guarantees correctness
     for the common, unambiguous cases.
  2. If no keyword matches, fall back to the LLM classification call,
     which still generalizes to topics the keyword list didn't anticipate
     (e.g. "leaf cross section", "engine internals").
"""
import re

from core.llm import get_llm

# Topics matching these patterns are almost always VISUAL (anatomy, real
# physical structures/objects) regardless of phrasing ("the human heart",
# "human heart anatomy", "heart diagram" all match "heart"). Matched as
# whole words against the lowercased topic so e.g. "heartfelt" won't
# false-positive ("heart" requires a word boundary).
_VISUAL_KEYWORDS = (
    # Human/animal anatomy
    "heart", "brain", "lung", "kidney", "liver", "skeleton", "skull",
    "spine", "muscle", "nerve", "neuron", "cell", "eye", "ear", "stomach",
    "intestine", "artery", "vein", "bone", "joint", "tooth", "teeth",
    "embryo", "fetus", "organ",
    # Plant/biological structures
    "flower", "leaf", "root system", "seed", "pollen", "chromosome",
    "dna", "rna", "bacteria", "virus", "fungus", "fungi",
    # Real-world physical objects/machinery (appearance, not process)
    "engine", "circuit board", "motherboard", "anatomy", "cross section",
    "cross-section", "internal structure", "layout", "pcb",
)
_VISUAL_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in _VISUAL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

ROUTING_PROMPT = """Classify the following diagram request into exactly one category:

"STRUCTURAL" — the request is about a process, cycle, workflow, hierarchy,
relationship between concepts, sequence of steps, or system architecture.
These are best shown as boxes/arrows/text (e.g. "the water cycle",
"how DNS resolution works", "stages of mitosis", "exam grading workflow").

"VISUAL" — the request is about the physical/anatomical/realistic
appearance of a real-world object or structure, where an actual labeled
image or illustration is needed rather than boxes and arrows (e.g. "the
human heart", "a plant cell under a microscope", "skeleton of a frog",
"parts of a flower", "an ESP32 board layout").

Topic: "{topic}"

Respond with ONLY one word: STRUCTURAL or VISUAL."""


def classify_diagram_topic(topic: str) -> str:
    """
    Returns "structural" or "visual" (lowercase).

    Checks a keyword list first for fast, reliable classification of
    obvious anatomical/physical-object topics. Only falls back to the LLM
    call for topics the keyword list doesn't recognize. Defaults to
    "structural" on any classification failure, since that path is free
    (no network image search) and still produces a usable result for
    most topics.
    """
    if _VISUAL_PATTERN.search(topic):
        return "visual"

    llm = get_llm()
    if not llm:
        return "structural"

    try:
        raw = llm.invoke(ROUTING_PROMPT.format(topic=topic)).content.strip().upper()
        if "VISUAL" in raw:
            return "visual"
        return "structural"
    except Exception:
        return "structural"