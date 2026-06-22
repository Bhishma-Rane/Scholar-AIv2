"""
features/socratic_tutor.py
=============================
Wraps a student's question with an instruction that changes the tutor's
teaching style: instead of answering directly, it guides the student
toward the answer with questions, hints, and small steps — classic
Socratic method. This is a lightweight prompt-wrapping layer, not a new
LLM call, so it works with the existing chat_graph pipeline at zero
extra cost.
"""

SOCRATIC_INSTRUCTION = (
    "[Teaching mode: Socratic. Do NOT give the final answer directly. Instead, "
    "respond with a guiding question, a small hint, or a simpler related question "
    "that helps the student work toward the answer themselves. Only reveal the "
    "direct answer if the student explicitly says they're stuck or asks for it "
    "directly after at least one guiding exchange. Keep each response short — "
    "one guiding question or hint at a time, not a lecture.] "
)


def wrap_socratic_instruction(question: str) -> str:
    """
    Prepends the Socratic teaching instruction to the user's question before
    it's sent to the LLM. The original, unwrapped question should still be
    what gets stored in chat history (so history stays clean for context
    purposes) — only the text sent to the model for this turn is wrapped.
    """
    return f"{SOCRATIC_INSTRUCTION}\n\nStudent's question: {question}"
