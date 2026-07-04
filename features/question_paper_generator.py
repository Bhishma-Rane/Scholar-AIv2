"""
features/question_paper_generator.py
=======================================
On-demand generation of a structured, section-based Question Paper for
the active chapter -- VSA/SA/LA/case-based/fill-in-the-blanks/assertion-
reason mixed together, matching the schema storage_bridge.py's
question_papers/paper_sections/paper_questions tables expect.

Design mirrors features/study_materials.py: get_llm(), a single prompt
asking for JSON back, then a brace-depth extraction of the JSON blob out
of whatever chatty wrapper text the model adds around it (Ollama models
are inconsistent about actually returning bare JSON even when told to).

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

COUNTS ARE NOW PER-TYPE (not a single total_marks_target). The caller
passes exactly how many of each question type they want -- e.g.
{"vsa": 10, "sa": 6, "fill_blank": 4, "assertion_reason": 4, "la": 6,
"case_based": 4} -- and the section plan, prompt, and total marks all
fall out of that directly. See _validate_question_counts for the
guardrails that keep a request from timing out the LLM call.

map_marking is deliberately never generated here -- the question-taking
UI (ui/tab_question_paper.py) doesn't support answering it yet, so
asking the model for one just guarantees a question nobody can answer.
"""
import json

from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.llm import get_llm, invoke_with_timeout
from core.vectorstore import get_chapter_text
from core.bridge_client import create_paper, add_paper_section, add_paper_question, BridgeRequestError

# The five types the in-app answer screen (ui/tab_question_paper.py) can
# actually render an input for. case_based is a container whose
# sub-questions must themselves be one of the SUB_QUESTION_TYPES below.
GENERATABLE_TYPES = ("mcq", "vsa", "sa", "la", "case_based", "fill_blank", "assertion_reason")
SUB_QUESTION_TYPES = ("mcq", "vsa", "sa", "fill_blank", "assertion_reason")

# Fixed marks-per-question and which section header each type falls
# under, following a CBSE-style layout:
#   Section A (1 mark each):  mcq, fill_blank, assertion_reason, vsa
#   Section B (2 marks):      sa
#   Section C (4 marks):      la_long   (bridge type "la", just a lower mark tier)
#   Section D (5 marks):      la_vlong  (bridge type "la", higher mark tier)
#   Section E (4 marks total, split 1+1+2 across 3 sub-questions): case_based
#
# "bridge_type" is the type string actually sent to storage_bridge.py --
# la_long/la_vlong are both plain "la" server-side, they only differ in
# marks and which section/count the UI exposes them under.
QUESTION_TYPE_INFO = {
    "mcq":               {"section": "Section A — MCQ, Fill-Ups, A-R & VSA", "marks": 1, "bridge_type": "mcq"},
    "fill_blank":        {"section": "Section A — MCQ, Fill-Ups, A-R & VSA", "marks": 1, "bridge_type": "fill_blank"},
    "assertion_reason":  {"section": "Section A — MCQ, Fill-Ups, A-R & VSA", "marks": 1, "bridge_type": "assertion_reason"},
    "vsa":               {"section": "Section A — MCQ, Fill-Ups, A-R & VSA", "marks": 1, "bridge_type": "vsa"},
    "sa":                {"section": "Section B — Short Answer",            "marks": 2, "bridge_type": "sa"},
    "la_long":           {"section": "Section C — Long Answer",             "marks": 4, "bridge_type": "la"},
    "la_vlong":          {"section": "Section D — Very Long Answer",        "marks": 5, "bridge_type": "la"},
    "case_based":        {"section": "Section E — Case-Based",              "marks": 4, "bridge_type": "case_based", "sub_marks": [1, 1, 2]},
}

# Stable section ordering (A -> B -> C -> D -> E) regardless of dict iteration order.
_SECTION_ORDER = list(dict.fromkeys(info["section"] for info in QUESTION_TYPE_INFO.values()))

# Fallback used only if the caller doesn't specify counts at all.
DEFAULT_QUESTION_COUNTS = {
    "mcq": 6, "fill_blank": 4, "assertion_reason": 4, "vsa": 6,
    "sa": 6, "la_long": 4, "la_vlong": 4, "case_based": 4,
}

# Guardrails. Ollama running locally on an M-series Mac has a real
# per-call time budget -- a request for, say, 200 LA questions in one
# shot is much more likely to time out or get truncated mid-JSON than
# to actually succeed, so we cap both the per-type count and the total
# question count rather than let the UI ask for anything.
MAX_COUNT_PER_TYPE = 25
MAX_TOTAL_QUESTIONS = 60


def estimate_total_marks(question_counts: dict) -> int:
    """
    Exact total marks a given question_counts dict would produce.
    case_based marks come from the fixed sub_marks split (1+1+2=4 per
    case), since each case is generated with that exact structure.
    """
    total = 0
    for qtype, count in (question_counts or {}).items():
        info = QUESTION_TYPE_INFO.get(qtype)
        if not info or not count:
            continue
        if qtype == "case_based":
            total += count * sum(info["sub_marks"])
        else:
            total += count * info["marks"]
    return total


def _validate_question_counts(question_counts: dict) -> dict:
    """
    Cleans and bounds the requested counts before anything is sent to
    the LLM. Raises ValueError for a request that's empty or too large
    to reasonably generate in one call; silently clamps individual
    per-type counts that exceed MAX_COUNT_PER_TYPE rather than
    rejecting the whole request over one oversized field.
    """
    if not question_counts:
        raise ValueError("No question counts provided.")

    cleaned = {}
    for qtype, count in question_counts.items():
        if qtype not in QUESTION_TYPE_INFO:
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        cleaned[qtype] = min(count, MAX_COUNT_PER_TYPE)

    if not cleaned:
        raise ValueError("At least one question type needs a count greater than 0.")

    total_questions = sum(cleaned.values())
    if total_questions > MAX_TOTAL_QUESTIONS:
        raise ValueError(
            f"That's {total_questions} questions total, which is more than this can reliably "
            f"generate in one go (max {MAX_TOTAL_QUESTIONS}). Try a smaller paper, or generate "
            f"a second paper for the rest."
        )

    return cleaned


def _build_section_plan(question_counts: dict) -> list:
    """
    Turns {"mcq": 6, "sa": 6, ...} into the section-plan shape the
    prompt builder and generator expect: one entry per section, each
    listing its own types (labelled with their bridge_type so the
    model/bridge see plain "la", not "la_long"/"la_vlong") with
    individual counts and marks. Types with a count of 0 (or omitted)
    are simply left out of the plan.
    """
    sections = {}
    for qtype, count in question_counts.items():
        info = QUESTION_TYPE_INFO[qtype]
        sec = sections.setdefault(info["section"], {"title": info["section"], "items": []})
        item = {
            "label": qtype,                      # what the UI/caller called it (la_long, la_vlong, etc.)
            "type": info["bridge_type"],          # what actually gets sent to the bridge (la, mcq, ...)
            "marks": info["marks"],
            "count": count,
        }
        if qtype == "case_based":
            item["sub_marks"] = info["sub_marks"]
        sec["items"].append(item)

    return [sections[title] for title in _SECTION_ORDER if title in sections]


def _build_section_prompt(chapter_text: str, chapter: str, language: str, section: dict) -> str:
    """
    Builds a prompt for a SINGLE section only (see generate_question_paper,
    which now makes one LLM call per section rather than one call for the
    whole paper). Splitting the request this way keeps each individual
    Ollama call's response small enough to finish comfortably within the
    "paper" model_type's timeout/num_predict budget (core/llm.py) --
    a full 8-type paper generated in ONE call regularly exceeded even a
    60s httpx timeout once mcq/fill_blank/A-R/vsa/sa/la_long/la_vlong/
    case_based were all requested together.
    """
    plan_lines = []
    for item in section["items"]:
        if item["type"] == "case_based":
            marks_note = f'a passage with exactly 3 sub-questions worth {item["sub_marks"][0]}, {item["sub_marks"][1]}, and {item["sub_marks"][2]} marks (in that order)'
        else:
            marks_note = f'{item["marks"]} marks each'
        plan_lines.append(f'- {item["count"]} question(s) of type "{item["type"]}", {marks_note}')
    plan_text = "\n".join(plan_lines)

    return f"""You are writing ONE SECTION of a formal, section-based exam question paper for the
chapter "{chapter}", based ONLY on the text provided below. Write all question text, passages,
and answer choices in {language}.

This section is titled "{section["title"]}". Produce EXACTLY this plan for it:
{plan_text}

Return ONLY a JSON object, no other text, matching this exact shape:

{{
  "title": "{section["title"]}",
  "instructions": "<one short line of instructions for this section, e.g. 'Answer all questions.'>",
  "questions": [
    {{
      "type": "mcq" | "vsa" | "sa" | "la" | "fill_blank" | "assertion_reason" | "case_based",
      "marks": <number, matching the plan above -- 0 for case_based>,
      "question_text": "<the question, OR null for fill_blank/assertion_reason/case_based>",
      "extra": <see rules below, or null>,
      "sub_questions": [ <ONLY for case_based -- EXACTLY 3 questions of type vsa/sa/mcq/fill_blank/assertion_reason,
                           each shaped exactly like a normal question object above, WITHOUT nested
                           sub_questions of their own, with "marks" set to the exact sequence given in the
                           plan above (e.g. 1, then 1, then 2)> ]
    }}
  ]
}}

Rules for "extra" by type:
- vsa / sa / la: extra must be null.
- mcq: extra = {{"options": ["<option 1>", "<option 2>", "<option 3>", "<option 4>"], "correct_option": <integer index 0-3 of the correct option>}}.
  Always provide exactly 4 options.
- fill_blank: extra = {{"text_with_blanks": "<sentence with blanks marked as ___>", "blanks": ["<answer for each ___, in order>"]}}.
  The number of "___" markers MUST exactly equal the number of items in "blanks".
- assertion_reason: extra = {{"assertion": "<statement A>", "reason": "<statement R>", "correct_option": "A"|"B"|"C"|"D"}}.
- case_based: extra = {{"passage": "<a short paragraph the sub_questions are based on>"}}, question_text = null,
  marks = 0 (the real marks live on each sub_question), and sub_questions must be present with exactly 3 entries.

IMPORTANT: match the requested COUNT of each question type exactly. Do not add extra questions of a
type that wasn't requested, and do not omit a type that was requested with a count greater than 0.

Chapter text:
{chapter_text}"""


def _find_json_object(raw: str) -> str:
    """
    Finds the outermost {...} block by brace-depth counting rather than a
    greedy regex. A greedy `\\{.*\\}` grabs from the FIRST `{` to the LAST
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

    if qtype == "mcq":
        options = extra.get("options")
        correct = extra.get("correct_option")
        if not options or not isinstance(options, list) or len(options) < 2:
            return False
        if not all(isinstance(o, str) and o.strip() for o in options):
            return False
        return isinstance(correct, int) and 0 <= correct < len(options)

    return False  # map_marking or anything unrecognized -- never accepted here


def generate_question_paper(
    username: str,
    subject: str,
    chapter: str,
    title: str,
    question_counts: dict = None,
    lang: str = "English",
) -> dict:
    """
    Generates a question paper for `chapter` and writes it into the bridge
    DB via create_paper/add_paper_section/add_paper_question, ready to be
    listed and taken immediately -- no publish step.

    `question_counts` is a dict of {question_type: count}, e.g.
    {"vsa": 10, "sa": 6, "fill_blank": 4, "assertion_reason": 4,
     "la": 6, "case_based": 4}. Omitted or falsy counts mean "don't
    include this type." Falls back to DEFAULT_QUESTION_COUNTS if None.

    Returns {"paper_id": int, "title": str, "subject": str|None,
             "questions_added": int, "questions_skipped": int,
             "estimated_total_marks": int, "failed_sections": list[str]}.

    Generation happens ONE SECTION AT A TIME (one LLM call per section,
    e.g. a separate call each for the MCQ/VSA section, the Long Answer
    section, the Case-Based section, etc.) rather than a single call for
    the whole paper -- this keeps each individual Ollama call's response
    small enough to reliably finish inside its timeout even when the
    paper as a whole has many question types and a large total count.
    A section whose LLM call times out or returns unusable JSON is
    skipped (its title lands in "failed_sections") rather than aborting
    the whole paper -- the student still gets everything that DID
    generate successfully.

    Raises ValueError if question_counts is invalid/empty/too large, the
    chapter text is missing, the LLM is unreachable, or literally every
    section failed to generate. Raises BridgeRequestError/
    BridgeUnavailableError only for the initial paper-shell creation
    call -- there's no point generating content if we can't even open
    the shell. Once the shell exists, individual bad questions are
    skipped rather than raised, since one malformed question from the
    LLM shouldn't blow away an otherwise-good paper that's already
    partially built.
    """
    cleaned_counts = _validate_question_counts(question_counts or DEFAULT_QUESTION_COUNTS)
    plan = _build_section_plan(cleaned_counts)

    chapter_text = get_chapter_text(username, subject, chapter)
    if not chapter_text:
        raise ValueError(f"Chapter text not found for {chapter}.")

    context_slice = RecursiveCharacterTextSplitter(chunk_size=12000, chunk_overlap=0).split_text(chapter_text)[0]

    # "paper" model_type -- see core/llm.py -- is sized (num_predict) and
    # timed for ONE SECTION's worth of questions per call, not the whole
    # paper. SECTION_TIMEOUT_SECONDS is generous per-call since we're now
    # making several smaller calls instead of one huge one; still bounded
    # so a genuinely stuck Ollama call doesn't hang the whole request.
    SECTION_TIMEOUT_SECONDS = 90
    llm = get_llm(model_type="paper", request_timeout=SECTION_TIMEOUT_SECONDS)
    if llm is None:
        raise ValueError("Could not connect to the LLM.")

    # Generate each section's questions with its own LLM call. A section
    # that times out or returns unusable JSON is skipped entirely rather
    # than aborting the whole paper -- e.g. if the Case-Based section
    # fails, the student still gets a paper with everything else in it.
    generated_sections = []
    failed_section_titles = []
    for section in plan:
        section_prompt = _build_section_prompt(context_slice, chapter, lang, section)
        raw = invoke_with_timeout(llm, section_prompt, timeout_seconds=SECTION_TIMEOUT_SECONDS)
        if raw is None:
            failed_section_titles.append(section["title"])
            continue
        try:
            parsed_section = _extract_json(raw)
        except (ValueError, json.JSONDecodeError):
            failed_section_titles.append(section["title"])
            continue
        if not parsed_section.get("questions"):
            failed_section_titles.append(section["title"])
            continue
        generated_sections.append(parsed_section)

    if not generated_sections:
        raise ValueError(
            "The model's response couldn't be turned into any valid questions "
            "(every section failed or timed out). Try generating again, or with fewer questions."
        )

    # Paper shell first -- if THIS call fails, nothing partial gets left behind.
    created = create_paper(username=username, title=title, subject=subject)
    paper_id = created["paper_id"]

    questions_added = 0
    questions_skipped = 0

    for sec_idx, section in enumerate(generated_sections):
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
            if qtype in ("mcq", "vsa", "sa", "la") and not question_text:
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
        "estimated_total_marks": estimate_total_marks(cleaned_counts),
        "failed_sections": failed_section_titles,
    }
