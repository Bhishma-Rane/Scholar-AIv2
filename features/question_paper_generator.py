"""
features/question_paper_generator.py
=======================================
On-demand generation of a structured, section-based Question Paper for
the active chapter -- VSA/SA/LA/case-based/fill-in-the-blanks/assertion-
reason mixed together, matching the schema storage_bridge.py's
question_papers/paper_sections/paper_questions tables expect.

Design mirrors features/study_materials.py: get_llm(), a single prompt
asking for JSON back, then a regex pull of the JSON blob out of
whatever chatty wrapper text the model adds around it (Ollama models are
inconsistent about actually returning bare JSON even when told to).

Unlike study_materials.py's one-shot text generators, a question paper
has real structure the bridge validates server-side (see
_validate_question_extra in storage_bridge.py) -- a fill_blank question
where the blank count doesn't match extra.blanks, for instance, gets a
400 back. Rather than let one bad question 400 out and abort a paper
that's otherwise fine, this module validates each question the SAME way
the bridge will before sending it, and just drops questions that don't
pass rather than failing the whole paper. A paper with a few fewer
questions than requested is fine; a paper that errors out halfway
through being built and leaves a half-empty shell in the DB is not.

map_marking is deliberately never generated here -- the question-taking
UI (ui/tab_question_paper.py) doesn't support answering it yet, so
asking the model for one just guarantees a question nobody can answer.
"""
import json
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_llm
from core.vectorstore import get_chapter_text
from core.bridge_client import create_paper, add_paper_section, add_paper_question, BridgeRequestError

# The five types the in-app answer screen (ui/tab_question_paper.py) can
# actually render an input for. case_based is a container whose
# sub-questions must themselves be one of the SUB_QUESTION_TYPES below.
GENERATABLE_TYPES = ("vsa", "sa", "la", "case_based", "fill_blank", "assertion_reason")
SUB_QUESTION_TYPES = ("vsa", "sa", "la", "fill_blank", "assertion_reason")

# Fixed marks-per-question and a target share of total_marks_target for
# each section; question COUNT is derived from those two so the paper
# scales to whatever total the user asked for instead of always coming
# back with the same fixed 31-mark paper.
SECTION_MARKS_SHARE = [
    {"title": "Section A — Very Short Answer", "types": ["vsa"], "marks": 1, "share": 0.20},
    {"title": "Section B — Short Answer", "types": ["sa", "fill_blank", "assertion_reason"], "marks": 3, "share": 0.35},
    {"title": "Section C — Long Answer", "types": ["la"], "marks": 5, "share": 0.30},
    {"title": "Section D — Case-Based", "types": ["case_based"], "marks": 4, "share": 0.15},
]


def _build_section_plan(total_marks_target: int) -> list:
    plan = []
    for sec in SECTION_MARKS_SHARE:
        section_marks = max(sec["marks"], round(total_marks_target * sec["share"]))
        count = max(1, round(section_marks / sec["marks"]))
        plan.append({"title": sec["title"], "types": sec["types"], "marks": sec["marks"], "count": count})
    return plan


def _validate_question_extra_clientside(qtype: str, extra: dict) -> bool:
    """
    Mirrors storage_bridge.py's _validate_question_extra -- checked here
    first so a malformed question from the LLM gets silently dropped
    instead of taking down the whole /papers/add_question call with a
    400 partway through building the paper.
    """
    extra = extra or {}

    if qtype in ("vsa", "sa", "la"):
        return True

    if qtype == "case_based":
        return isinstance(extra.get("passage"), str) and bool(extra["passage"].strip())

    if qtype == "fill_blank":
        text = extra.get("text_with_blanks")
        blanks = extra.get("blanks")
        if not text or not isinstance(text, str):
            return False
        if not blanks or not isinstance(blanks, list) or not all(isinstance(b, str) for b in blanks):
            return False
        return text.count("___") == len(blanks)

    if qtype == "assertion_reason":
        for field in ("assertion", "reason", "correct_option"):
            if not extra.get(field):
                return False
        return extra["correct_option"] in ("A", "B", "C", "D")

    return False  # map_marking or anything unrecognized -- never accepted here


def _build_prompt(chapter_text: str, chapter: str, language: str, section_plan: list) -> str:
    plan_lines = [
        f'- "{sec["title"]}": {sec["count"]} question(s), {sec["marks"]} marks each, type one of {sec["types"]}'
        for sec in section_plan
    ]
    plan_text = "\n".join(plan_lines)

    return f"""You are writing a formal, section-based exam question paper for the chapter "{chapter}",
based ONLY on the text provided below. Write all question text, passages, and answer choices in {language}.

Produce EXACTLY this section plan:
{plan_text}

Return ONLY a JSON object, no other text, matching this exact shape:

{{
  "sections": [
    {{
      "title": "<section title, matching the plan above exactly>",
      "instructions": "<one short line of instructions for this section, e.g. 'Answer all questions.'>",
      "questions": [
        {{
          "type": "vsa" | "sa" | "la" | "fill_blank" | "assertion_reason" | "case_based",
          "marks": <number, matching the plan above>,
          "question_text": "<the question, OR null for fill_blank/assertion_reason/case_based>",
          "extra": <see rules below, or null>,
          "sub_questions": [ <ONLY for case_based -- 2 or 3 questions of type vsa/sa/fill_blank/assertion_reason,
                               each shaped exactly like a normal question object above, WITHOUT nested
                               sub_questions of their own> ]
        }}
      ]
    }}
  ]
}}

Rules for "extra" by type:
- vsa / sa / la: extra must be null.
- fill_blank: extra = {{"text_with_blanks": "<sentence with blanks marked as ___>", "blanks": ["<answer for each ___, in order>"]}}.
  The number of "___" markers MUST exactly equal the number of items in "blanks".
- assertion_reason: extra = {{"assertion": "<statement A>", "reason": "<statement R>", "correct_option": "A"|"B"|"C"|"D"}}.
- case_based: extra = {{"passage": "<a short paragraph the sub_questions are based on>"}}, question_text = null,
  marks = 0 (the real marks live on each sub_question), and sub_questions must be present and non-empty.

Chapter text:
{chapter_text}"""


def _find_json_object(raw: str) -> str:
    """
    Finds the outermost {...} block by brace-depth counting rather than a
    greedy regex. A greedy `\{.*\}` grabs from the FIRST `{` to the LAST
    `}` in the whole response -- if the model appends any chatty text
    with its own stray braces after the real JSON, that regex silently
    stitches unrelated text into the "JSON" it hands to json.loads(),
    which is what produces confusing errors like "Expecting ',' delimiter"
    deep in the middle of an otherwise-fine object.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("Model did not return a recognizable JSON object.")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]

    raise ValueError("Model's JSON object was truncated (no matching closing brace).")


def _extract_json(raw: str) -> dict:
    blob = _find_json_object(raw)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Ollama models frequently produce near-valid JSON -- a missing
        # comma between two question objects, a trailing comma, an
        # unescaped quote inside a question string. Rather than let one
        # such slip abort the whole paper, try to repair it before
        # giving up.
        try:
            from json_repair import repair_json
        except ImportError:
            raise  # re-raise the original JSONDecodeError if repair isn't installed
        repaired = repair_json(blob)
        return json.loads(repaired)


def generate_question_paper(
    username: str,
    subject: str,
    chapter: str,
    title: str,
    total_marks_target: int = 40,
    lang: str = "English",
    section_plan: list = None,
) -> dict:
    """
    Generates a question paper for `chapter` and writes it into the bridge
    DB via create_paper/add_paper_section/add_paper_question, ready to be
    listed and taken immediately -- no publish step.

    Returns {"paper_id": int, "title": str, "subject": str|None,
             "questions_added": int, "questions_skipped": int}.

    Raises ValueError if the chapter text is missing, the LLM is
    unreachable, or its response couldn't be turned into any valid
    questions at all. Raises BridgeRequestError/BridgeUnavailableError
    only for the initial paper-shell creation call -- there's no point
    generating content if we can't even open the shell. Once the shell
    exists, individual bad questions are skipped rather than raised,
    since one malformed question from the LLM shouldn't blow away an
    otherwise-good paper that's already partially built.
    """
    plan = section_plan or _build_section_plan(total_marks_target)

    chapter_text = get_chapter_text(username, subject, chapter)
    if not chapter_text:
        raise ValueError(f"Chapter text not found for {chapter}.")

    context_slice = RecursiveCharacterTextSplitter(chunk_size=12000, chunk_overlap=0).split_text(chapter_text)[0]

    prompt = _build_prompt(context_slice, chapter, lang, plan)

    llm = get_llm()
    if llm is None:
        raise ValueError("Could not connect to the LLM.")

    raw = llm.invoke(prompt).content
    try:
        parsed = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Model returned an unusable response: {e}")

    sections = parsed.get("sections") or []
    if not sections:
        raise ValueError("Model returned no sections.")

    # Paper shell first -- if THIS call fails, nothing partial gets left behind.
    created = create_paper(username=username, title=title, subject=subject)
    paper_id = created["paper_id"]

    questions_added = 0
    questions_skipped = 0

    for sec_idx, section in enumerate(sections):
        sec_title = str(section.get("title") or f"Section {sec_idx + 1}").strip()
        sec_instructions = section.get("instructions")

        try:
            sec_result = add_paper_section(
                paper_id=paper_id, title=sec_title, instructions=sec_instructions, order_index=sec_idx,
            )
        except BridgeRequestError:
            # Shouldn't happen for a section, but skip rather than abort
            # if it does -- the rest of the paper is still fine.
            continue
        section_id = sec_result["section_id"]

        for q_idx, question in enumerate(section.get("questions") or []):
            qtype = question.get("type")
            marks = question.get("marks")
            extra = question.get("extra")
            question_text = question.get("question_text")

            if qtype not in GENERATABLE_TYPES:
                questions_skipped += 1
                continue

            if qtype == "case_based":
                subs = question.get("sub_questions") or []
                if not _validate_question_extra_clientside("case_based", extra) or not subs:
                    questions_skipped += 1
                    continue
                try:
                    parent_result = add_paper_question(
                        section_id=section_id, type="case_based", marks=0,
                        order_index=q_idx, question_text=None, extra=extra,
                    )
                except BridgeRequestError:
                    questions_skipped += 1
                    continue
                parent_id = parent_result["question_id"]
                questions_added += 1

                for sub_idx, sub in enumerate(subs):
                    sub_type = sub.get("type")
                    sub_marks = sub.get("marks")
                    sub_extra = sub.get("extra")
                    sub_text = sub.get("question_text")

                    if sub_type not in SUB_QUESTION_TYPES:
                        questions_skipped += 1
                        continue
                    if not isinstance(sub_marks, (int, float)) or sub_marks <= 0:
                        questions_skipped += 1
                        continue
                    if not _validate_question_extra_clientside(sub_type, sub_extra):
                        questions_skipped += 1
                        continue

                    try:
                        add_paper_question(
                            section_id=section_id, type=sub_type, marks=sub_marks,
                            order_index=sub_idx, question_text=sub_text,
                            parent_question_id=parent_id, extra=sub_extra,
                        )
                        questions_added += 1
                    except BridgeRequestError:
                        questions_skipped += 1
                continue

            # Non-case_based, ordinary question.
            if not isinstance(marks, (int, float)) or marks <= 0:
                questions_skipped += 1
                continue
            if not _validate_question_extra_clientside(qtype, extra):
                questions_skipped += 1
                continue
            if qtype in ("vsa", "sa", "la") and not question_text:
                questions_skipped += 1
                continue

            try:
                add_paper_question(
                    section_id=section_id, type=qtype, marks=marks,
                    order_index=q_idx, question_text=question_text, extra=extra,
                )
                questions_added += 1
            except BridgeRequestError:
                questions_skipped += 1

    if questions_added == 0:
        raise ValueError(
            "The model's response couldn't be turned into any valid questions. Try generating again."
        )

    return {
        "paper_id": paper_id,
        "title": created["title"],
        "subject": created.get("subject"),
        "questions_added": questions_added,
        "questions_skipped": questions_skipped,
    }
