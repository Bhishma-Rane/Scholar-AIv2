"""
features/mock_exams.py
========================
Printable mock exam generation (question paper + answer key) and
AI-assisted grading of subjective answers.

BUGFIX (this revision):
- get_llm() now requires a `username` (used for the subscription/tier
  gate in the bridge) and returns None -- silently -- if it isn't
  passed. Every get_llm() call site in this file was calling it with
  NO username, so grading ALWAYS fell into the "LLM engine offline"
  fallback, regardless of whether Ollama/the bridge was actually up.
  Fixed by threading `username` through grade_subjective_answer(),
  grade_full_quiz(), generate_performance_summary(), and
  build_mock_exam_paper()'s quiz_llm = get_llm("quiz") call.
- grade_full_quiz() now accepts `username` and passes it down to
  grade_subjective_answer().
- Objective (MCQ) wrong-answer feedback used to be just the letter,
  e.g. "Correct answer: C", with no indication of what C actually
  was. It now looks up the matching option text and shows
  "Correct answer: C) <option text>" when available.
- generate_performance_summary() previously called llm.invoke(...)
  without checking whether get_llm() returned None first -- since
  get_llm() always returned None (see above), this was throwing
  AttributeError: 'NoneType' object has no attribute 'invoke' on
  every call, caught only by the generic except Exception at the
  bottom. Added an explicit None-check with a clean fallback message,
  same pattern as grade_subjective_answer().
"""

import re
import json
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_llm
from core.paths import get_chapter_paths
from core.vectorstore import get_chapter_text


def _format_correct_answer(question: dict) -> str:
    """
    Builds a human-readable "Correct answer: C) <option text>" string for
    an objective question, falling back to just the letter if the
    matching option can't be found (e.g. malformed question data).
    """
    correct_letter = str(question.get("answer", "")).strip().upper()
    if not correct_letter:
        return "Correct answer: (not set)"

    for opt in question.get("options", []):
        opt_str = str(opt).strip()
        # Options are typically authored as "C) some text" or "C. some text".
        opt_letter = opt_str.split(")")[0].split(".")[0].strip().upper()
        if opt_letter == correct_letter:
            return f"Correct answer: {opt_str}"

    # No matching option found -- fall back to just the letter rather
    # than silently showing nothing.
    return f"Correct answer: {correct_letter}"


def grade_objective_answer(question: dict, user_answer) -> dict:
    """
    Grades a single objective (MCQ) question. Pure string comparison — no
    LLM call needed since the correct option letter is already known.

    Returns {"is_correct": bool, "marks_earned": float, "marks_possible": float}.
    """
    marks_possible = question.get("marks", 1)
    correct_answer = str(question.get("answer", "")).strip().upper()
    given_answer = str(user_answer or "").strip().upper()
    # Accept either "A" or "A) opt1" style answers/options.
    given_letter = given_answer.split(")")[0].strip() if ")" in given_answer else given_answer
    is_correct = given_letter == correct_answer

    return {
        "is_correct": is_correct,
        "marks_earned": marks_possible if is_correct else 0,
        "marks_possible": marks_possible,
    }


def grade_subjective_answer(question: dict, user_answer: str, lang: str = "English", username: str = None) -> dict:
    """
    Grades a single short/long-answer question by asking the LLM to compare
    the student's answer against the official answer key, allowing partial
    credit (the LLM returns a fraction of the available marks, not just
    right/wrong).

    Returns {"is_correct": bool, "marks_earned": float, "marks_possible": float, "feedback": str}.

    "is_correct" is True only for full marks; partial-credit answers are
    marked correct=False but still earn partial marks, so callers can
    distinguish "fully correct" from "partially correct" if needed.
    """
    marks_possible = question.get("marks", 2)
    user_answer = (user_answer or "").strip()

    if not user_answer:
        return {
            "is_correct": False,
            "marks_earned": 0,
            "marks_possible": marks_possible,
            "feedback": "No answer provided.",
        }

    grading_prompt = f"""You are a strict but fair exam grader. Respond in {lang}.

Question: {question.get('q', '')}
Official Answer Key: {question.get('answer', '')}
Student's Answer: {user_answer}
Maximum Marks: {marks_possible}

TASK: Compare the student's answer to the official answer key conceptually
(not word-for-word). Award partial credit proportional to how much of the
key concept the student captured. A vague or partially-correct answer should
get partial marks, not zero. A fully blank or completely wrong answer gets 0.

RETURN STRICTLY AS JSON, NOTHING ELSE:
{{"marks_earned": <number between 0 and {marks_possible}, can be a decimal>, "feedback": "<one sentence>"}}
"""

    try:
        llm = get_llm(username=username)
        if not llm:
            return {
                "is_correct": False,
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
        # Clamp to valid range in case the LLM ignores the bound.
        marks_earned = max(0.0, min(marks_earned, marks_possible))

        return {
            "is_correct": marks_earned >= marks_possible,
            "marks_earned": marks_earned,
            "marks_possible": marks_possible,
            "feedback": parsed.get("feedback", ""),
        }
    except Exception as e:
        return {
            "is_correct": False,
            "marks_earned": 0,
            "marks_possible": marks_possible,
            "feedback": f"Grading error: {str(e)}",
        }


def grade_full_quiz(quiz_data: list, user_answers: dict, negative_marking: bool, lang: str = "English", username: str = None) -> dict:
    """
    Grades an entire quiz attempt question-by-question.

    quiz_data: the list of question dicts (as loaded from the quiz JSON file).
    user_answers: {question_index (int): answer (str or None)}.
    negative_marking: if True, an incorrect *objective* answer deducts marks
      (standard competitive-exam convention: deduct a fraction, here 25%,
      of that question's marks). Negative marking is never applied to
      subjective answers or to skipped/blank questions, only to answers
      that were actually attempted and wrong.
    username: REQUIRED to actually reach the LLM for subjective grading --
      threaded down to grade_subjective_answer() -> get_llm(). Without it,
      subjective grading always falls back to "LLM engine offline".

    Returns:
    {
        "total_score": float, "max_score": float,
        "per_question": [ {index, is_correct, marks_earned, marks_possible, feedback}, ... ],
        "topic_breakdown": {topic: {"correct": float, "total": float}}
    }
    """
    NEGATIVE_MARK_FRACTION = 0.25
    per_question = []
    total_score = 0.0
    max_score = 0.0
    topic_breakdown = {}

    for idx, question in enumerate(quiz_data):
        user_answer = user_answers.get(idx)
        marks_possible = question.get("marks", 1)
        max_score += marks_possible

        if question.get("type") == "objective":
            result = grade_objective_answer(question, user_answer)
            attempted = user_answer is not None and str(user_answer).strip() != ""
            if negative_marking and attempted and not result["is_correct"]:
                result["marks_earned"] = -round(marks_possible * NEGATIVE_MARK_FRACTION, 2)
            result["feedback"] = "" if result["is_correct"] else _format_correct_answer(question)
        else:
            result = grade_subjective_answer(question, user_answer, lang, username=username)

        result["index"] = idx
        per_question.append(result)
        total_score += result["marks_earned"]

        topic = question.get("topic", "General")
        topic_stats = topic_breakdown.setdefault(topic, {"correct": 0.0, "total": 0.0})
        topic_stats["total"] += marks_possible
        topic_stats["correct"] += max(result["marks_earned"], 0)  # don't let negative marks pull mastery below 0

    return {
        "total_score": round(total_score, 2),
        "max_score": round(max_score, 2),
        "per_question": per_question,
        "topic_breakdown": topic_breakdown,
    }


def extract_clean_json(text: str) -> str:
    """Pulls a JSON array of objects out of raw LLM text, falling back to the raw text."""
    match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
    if match:
        return match.group(0)
    return text


def build_mock_exam_paper(username: str, subject: str, chapter: str, config: dict):
    """
    Builds a custom printable mock exam (question paper + master answer key)
    based on the requested section order and per-section question counts.
    Writes both files to the chapter's "mock" folder.
    """
    paths = get_chapter_paths(username, subject, chapter)
    exact_text = get_chapter_text(username, subject, chapter)
    if not exact_text:
        raise FileNotFoundError("Chapter text not found.")

    context_slice = RecursiveCharacterTextSplitter(chunk_size=12000, chunk_overlap=0).split_text(exact_text)[0]

    q_paper = f"CUSTOM MOCK EXAM: {chapter.upper()}\nTotal Marks: {config['total_marks']}\n\n"
    a_paper = f"MASTER ANSWER KEY: {chapter.upper()}\n\n"
    strict_rule = "\nCRITICAL RULE: Output ONLY the requested questions. NO conversational text."

    # BUGFIX: was get_llm("quiz") with no username -- always returned None.
    quiz_llm = get_llm("quiz", username=username)

    for section in config["order"]:
        if section == "MCQs" and config["mcq_count"] > 0:
            q_paper += "=== SECTION: MULTIPLE CHOICE ===\n\n"
            a_paper += "=== SECTION: MULTIPLE CHOICE ===\n\n"

            q_out = (
                ChatPromptTemplate.from_messages(
                    [
                        ("system", f"Generate EXACTLY {config['mcq_count']} Multiple Choice Questions." + strict_rule),
                        ("human", f"Context:\n{context_slice}"),
                    ]
                )
                | quiz_llm
                | StrOutputParser()
            ).invoke({})
            q_paper += q_out + "\n\n"

            a_paper += (
                ChatPromptTemplate.from_messages(
                    [
                        ("system", "Provide only the answer key list."),
                        ("human", f"Questions:\n{q_out}\nContext:\n{context_slice}"),
                    ]
                )
                | quiz_llm
                | StrOutputParser()
            ).invoke({}) + "\n\n"

        elif section == "Short Answer" and config["short_count"] > 0:
            q_paper += "=== SECTION: SHORT ANSWER ===\n\n"
            a_paper += "=== SECTION: SHORT ANSWER ===\n\n"

            q_out = (
                ChatPromptTemplate.from_messages(
                    [
                        ("system", f"Generate EXACTLY {config['short_count']} Short Answer Questions." + strict_rule),
                        ("human", f"Context:\n{context_slice}"),
                    ]
                )
                | quiz_llm
                | StrOutputParser()
            ).invoke({})
            q_paper += q_out + "\n\n"

            a_paper += (
                ChatPromptTemplate.from_messages(
                    [
                        ("system", "Provide short answers."),
                        ("human", f"Questions:\n{q_out}\nContext:\n{context_slice}"),
                    ]
                )
                | quiz_llm
                | StrOutputParser()
            ).invoke({}) + "\n\n"

    with open(f"{paths['mock']}/{chapter}_MockExam_Questions.txt", "w", encoding="utf-8") as f:
        f.write(q_paper)
    with open(f"{paths['mock']}/{chapter}_MockExam_Answers.txt", "w", encoding="utf-8") as f:
        f.write(a_paper)


def generate_performance_summary(wrong_answers: List[dict], subjective_answers: List[dict], lang: str, username: str = None) -> dict:
    """
    Sends subjective answers to the LLM for strict grading + brief feedback.
    Returns {"subjective_score": int, "ai_feedback": str}.
    """
    analysis_prompt = f"You are an elite academic grader. Analyze the exam performance in {lang}.\n\n"
    if subjective_answers:
        for item in subjective_answers:
            analysis_prompt += f"Q: {item['q']}\nStudent Wrote: {item['written']}\nOfficial Key: {item['actual']}\n\n"

    analysis_prompt += """TASK:
1. Grade each subjective answer strictly (1 point if conceptually correct, 0 if wrong/blank).
2. Provide brief feedback on the current test attempt.
RETURN YOUR OUTPUT STRICTLY AS JSON: {"subjective_score": <int>, "ai_feedback": "<string>"}
"""
    try:
        # BUGFIX: was get_llm() with no username -- always returned None,
        # and the old code called llm.invoke(...) without checking for
        # None first, which raised AttributeError on every call.
        llm = get_llm(username=username)
        if not llm:
            return {"subjective_score": 0, "ai_feedback": "Grading unavailable: LLM engine offline."}

        res = llm.invoke(analysis_prompt).content
        # Fix Issue #7: Safe regex.
        match = re.search(r"\{.*\}", res, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"subjective_score": 0, "ai_feedback": "Unable to parse AI grading format."}
    except Exception as e:
        return {"subjective_score": 0, "ai_feedback": f"Error grading subjective questions: {str(e)}"}
