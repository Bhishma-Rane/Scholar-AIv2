"""
features/flashcards.py
========================
Spaced-repetition style flashcard deck generation, batched in chunks
of 5 to keep individual LLM calls fast and reliable.

CHANGE LOG (this revision):
  - Fixed generate_flashcards() calling get_llm("quiz") with no
    username, even though `username` is a parameter of this very
    function. Under get_llm()'s current contract (refuses to return an
    LLM without a username), this meant every flashcard generation call
    was failing outright with "Failed: LLM Engine offline." regardless
    of tier.
  - Now passes feature="flashcards" explicitly, since flashcards and
    quiz-question generation share model_type="quiz" (same num_predict
    budget) but are gated as separate tiers in storage_bridge.py's
    FEATURE_MIN_TIER ("flashcards" vs "quiz_generation").
"""
import os
import re
import json

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_llm
from core.paths import get_chapter_paths
from core.vectorstore import get_chapter_text

BATCH_SIZE = 5


def generate_flashcards(username: str, subject: str, chapter: str, count: int, language: str, append: bool = False) -> str:
    """
    Generates `count` flashcards for a chapter in batches, saving the
    final deck as Flashcards.json. Returns "Success" or a "Failed: ..." message.

    If `append` is True and a deck already exists, the new cards are added
    to the existing deck (new cards start at box 1) instead of overwriting it.
    Duplicate fronts (case-insensitive) are skipped so re-generating doesn't
    pad the deck with near-identical cards.
    """
    paths = get_chapter_paths(username, subject, chapter)
    fc_path = os.path.join(paths["flashcards"], "Flashcards.json")

    existing_cards = []
    existing_fronts = set()
    if append and os.path.exists(fc_path):
        try:
            with open(fc_path, "r", encoding="utf-8") as f:
                existing_cards = json.load(f)
            existing_fronts = {c.get("front", "").strip().lower() for c in existing_cards}
        except Exception:
            existing_cards = []

    exact_text = get_chapter_text(username, subject, chapter)
    if not exact_text:
        return "Failed: Source text not found."

    slices = RecursiveCharacterTextSplitter(chunk_size=8000, chunk_overlap=500).split_text(exact_text)

    # Fix Issue #4: Modulo by zero.
    if not slices:
        return "Failed: Could not parse text chunks."

    new_flashcards = []
    attempts = 0
    max_attempts = (count // BATCH_SIZE) + 10

    # Was get_llm("quiz") with no username -- username is right here in
    # scope, it just wasn't being passed. feature="flashcards" gates this
    # against FEATURE_MIN_TIER["flashcards"] (gold+) instead of the
    # "quiz_generation" gate that model_type="quiz" would default to.
    quiz_llm = get_llm("quiz", username=username, feature="flashcards")
    if not quiz_llm:
        return "Failed: LLM Engine offline."

    try:
        while len(new_flashcards) < count and attempts < max_attempts:
            attempts += 1
            target_count = min(BATCH_SIZE, count - len(new_flashcards))
            context_slice = slices[attempts % len(slices)]

            quiz_inst = f"""Generate EXACTLY {target_count} high-yield flashcards from the text in {language}.
CRITICAL: Return ONLY a valid JSON array of objects. NO Markdown.
Format MUST be:
[
  {{"front": "Question or Term", "back": "Answer or Definition", "box": 1}}
]"""
            raw_out = quiz_llm.invoke(f"{quiz_inst}\n\nContext:\n{context_slice}").content

            try:
                # Fix Issue #6: Safe regex extraction.
                match = re.search(r"\[.*\]", raw_out, re.DOTALL)
                if match:
                    fc_batch = json.loads(match.group())
                    for card in fc_batch:
                        front_key = card.get("front", "").strip().lower()
                        if front_key and front_key not in existing_fronts:
                            existing_fronts.add(front_key)
                            new_flashcards.append(card)
            except Exception:
                continue

        # Fix Issue #5: Empty deck handling.
        if not new_flashcards:
            return "Failed: LLM failed to generate valid new flashcards (they may all have been duplicates)."

        new_flashcards = new_flashcards[:count]
        combined_deck = existing_cards + new_flashcards

        with open(fc_path, "w", encoding="utf-8") as f:
            json.dump(combined_deck, f, indent=4)

        return f"Success: added {len(new_flashcards)} new cards (deck now has {len(combined_deck)} total)."

    except Exception as e:
        return f"Failed: {str(e)}"
        
