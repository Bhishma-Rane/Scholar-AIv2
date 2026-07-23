"""
grading_engine.py — AI-graded written answers (VSA/SA/LA/case-based subs)
==============================================================================
Mirrors features/mock_exams.py's grade_subjective_answer() exactly: same
prompt shape, same JSON-extraction regex, same clamping, same graceful
fallback if the LLM is offline or returns something unparseable. The only
difference is the marks-possible default (VSA/SA/LA carry their own
per-question marks set at authoring time, so there's no made-up default
like the quiz engine's `question.get("marks", 2)` -- if a question paper
question is missing marks, that's an authoring bug we want surfaced, not
silently defaulted).

VSA/SA/LA and case-based sub-questions all use this same function --
there's no behavioral difference between them for grading purposes, only
the constraints students see when answering (a short box vs. a long box vs.
a passage above the question) differ, which is a UI concern (layer 3),
not a grading concern.

BUGFIX (this revision):
- core/llm.py's get_llm() requires a `username` (used for the bridge's
  subscription/tier gate) and returns None -- silently, no exception --
  if it isn't passed. This function was calling get_llm() with no
  arguments at all, so grading ALWAYS fell into the "Grading unavailable:
  LLM engine offline." fallback, regardless of whether the LLM backend
  was actually reachable. Fixed by adding a `username` parameter here and
  threading it through to get_llm().

  IMPORTANT: every call site of grade_written_answer() (e.g.
  grade_paper_attempt()) now needs to pass `username=...` through as
  well, the same way every get_llm() call site had to be updated per
  core/llm.py's own change log. Search your codebase for
  `grade_written_answer(` and update each call site.
"""

import re
import json

from core.llm import get_llm


def grade_written_answer(question: dict, user_answer: str, lang: str = "English", username: str = None) -> dict:
    """
    question: a paper_questions row dict, with question_text and marks set.
      For case_based sub-questions, the caller should pass the PARENT's
      passage as part of the prompt context (see grade_paper_attempt,
      which threads this through) so the LLM grades with full context,
      not just the sub-question in isolation.
    user_answer: the student's free-text answer.
    username: REQUIRED to actually reach the LLM -- see BUGFIX note above.
      Without it, get_llm() refuses to hand back an LLM and this function
      always returns the "LLM engine offline" fallback.

    Returns {"marks_earned": float, "marks_possible": float, "feedback": str},
    matching grade_subjective_answer()'s exact shape so paper grading can
    be assembled the same way quiz grading is.

    NOTE: question papers don't carry a separate "official answer key"
    field the way auto-generated quizzes do (question["answer"] in
    mock_exams.py) -- you author papers with just a question + marks, no
    answer key. So the grading prompt asks the LLM to grade on subject-
    matter correctness directly, rather than comparing against a key. This
    is a deliberate scope choice: adding an optional answer-key field to
    paper_questions later (for stricter grading) is possible without
    breaking this function -- it would just add a few more lines if/when
    you want it, this function will accept an optional `answer_key`
    kwarg already wired through grade_paper_attempt for that future case.
    """
    marks_possible = question.get("marks")
    if marks_possible is None:
        return {
            "marks_earned": 0,
            "marks_possible": 0,
            "feedback": "Question configuration error: no marks set for this question.",
        }

    user_answer = (user_answer or "").strip()
    if not user_answer:
        return {
            "marks_earned": 0,
            "marks_possible": marks_possible,
            "feedback": "No answer provided.",
        }

    context_block = ""
    passage = question.get("_passage_context")  # set by grade_paper_attempt for case_based subs
    if passage:
        context_block = f"Passage (for context):\n{passage}\n\n"

    answer_key = question.get("_answer_key")  # optional, not part of current authoring flow
    key_block = f"Reference answer key: {answer_key}\n" if answer_key else ""

    grading_prompt = f"""You are a strict but fair exam grader. Respond in {lang}.

{context_block}Question: {question.get('question_text', '')}
{key_block}Student's Answer: {user_answer}
Maximum Marks: {marks_possible}

TASK: Evaluate the student's answer for subject-matter correctness and
completeness relative to the question asked{" and the passage above" if passage else ""}.
Award partial credit proportional to how much of a complete, correct
answer the student captured. A vague or partially-correct answer should
get partial marks, not zero. A fully blank or completely wrong answer gets 0.

RETURN STRICTLY AS JSON, NOTHING ELSE:
{{"marks_earned": <number between 0 and {marks_possible}, can be a decimal>, "feedback": "<one sentence>"}}
"""

    try:
        llm = get_llm(username=username)
        if not llm:
            return {
                "marks_earned": 0,
                "marks_possible": marks_possible,
                "feedback": "Grading unavailable: LLM engine offline.",
            }

        raw = llm.invoke(grading_prompt).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in grading response.")

        parsed = json.loads(match.group())
        marks_earned = float(parsed.get("marks_earned", 0))
        marks_earned = max(0.0, min(marks_earned, marks_possible))

        return {
            "marks_earned": marks_earned,
            "marks_possible": marks_possible,
            "feedback": parsed.get("feedback", ""),
        }
    except Exception as e:
        return {
            "marks_earned": 0,
            "marks_possible": marks_possible,
            "feedback": f"Grading error: {str(e)}",
        }
