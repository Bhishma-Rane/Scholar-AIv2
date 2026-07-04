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

GENERATION IS RETRY-BASED PER ITEM (see _generate_item_questions).
Ollama models routinely ignore "produce EXACTLY N questions" and close
the JSON early once they feel "done," well before num_predict is
exhausted -- so a single call's output is a LOWER BOUND on what was
asked for, never treated as final. If a call comes back short, a
follow-up call asks only for the still-missing count (with the
already-generated questions listed so the model doesn't repeat itself),
up to MAX_ITEM_ATTEMPTS tries. If a call fails outright (timeout, bad
JSON, empty list), that's also just one attempt -- it gets retried like
any other shortfall rather than immediately giving up on the whole
question type.
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

# How many LLM calls we're willing to make for a SINGLE item (question
# type) before accepting whatever we've got. The first call asks for the
# full count; each subsequent call asks only for whatever's still
# missing. 3 gives the model two chances to make up a shortfall without
# letting one stubborn type blow up the total time budget for the paper
# (worst case: MAX_ITEM_ATTEMPTS calls * ITEM_TIMEOUT_SECONDS per item).
MAX_ITEM_ATTEMPTS = 3


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


def _build_item_prompt(
    chapter_text: str,
    chapter: str,
    language: str,
    item: dict,
    count_override: int = None,
    avoid_questions: list = None,
) -> str:
    """
    Builds a prompt for a SINGLE question type only, asking for
    `count_override` questions if given (used by retry/top-up calls that
    only need the still-missing remainder) or item["count"] otherwise.

    A section like "Section A" can mix 4 different types (mcq/fill_blank/
    A-R/vsa) totalling 20 questions -- asking for all of them in one call
    still blew past num_predict and got silently truncated to a single
    question (the model closes the JSON early to stay syntactically
    valid once it runs out of budget, so no error fires, you just get
    way fewer questions than asked for). Generating one type at a time
    keeps each response small enough to actually fit inside the "paper"
    model_type's token budget -- but even then, models frequently
    under-deliver on the exact count within a single call, which is why
    the caller (_generate_item_questions) treats this as retriable.

    `avoid_questions`, when given, lists short summaries of questions
    already generated for this item in an earlier attempt, so a
    follow-up call fills the gap with NEW questions instead of repeating
    ones we already have.
    """
    count = count_override if count_override is not None else item["count"]

    if item["type"] == "case_based":
        plan_line = (
            f'Produce EXACTLY {count} case-based question(s). Each one has a short passage '
            f'and EXACTLY 3 sub-questions worth {item["sub_marks"][0]}, {item["sub_marks"][1]}, and '
            f'{item["sub_marks"][2]} marks (in that order).'
        )
        shape = """{
  "questions": [
    {
      "type": "case_based",
      "marks": 0,
      "question_text": null,
      "extra": {"passage": "<a short paragraph the sub_questions are based on>"},
      "sub_questions": [
        {"type": "vsa" | "sa" | "mcq" | "fill_blank" | "assertion_reason", "marks": <exact mark from the sequence above>, "question_text": "<...>", "extra": <per rules below>}
      ]
    }
  ]
}"""
    else:
        plan_line = f'Produce EXACTLY {count} question(s) of type "{item["type"]}", {item["marks"]} marks each.'
        shape = f"""{{
  "questions": [
    {{
      "type": "{item['type']}",
      "marks": {item['marks']},
      "question_text": "<the question, OR null for fill_blank/assertion_reason>",
      "extra": <see rules below, or null>
    }}
  ]
}}"""

    avoid_block = ""
    if avoid_questions:
        listed = "\n".join(f"- {q}" for q in avoid_questions)
        avoid_block = (
            f"\n\nThese questions have ALREADY been used in this paper -- do NOT repeat them "
            f"or produce close variants of them. Write entirely new, different questions:\n{listed}"
        )

    return f"""You are writing questions for one part of a formal exam question paper for the chapter
"{chapter}", based ONLY on the text provided below. Write all question text, passages, and answer
choices in {language}.

{plan_line}

Return ONLY a JSON object, no other text, matching this exact shape:

{shape}

Rules for "extra" by type:
- vsa / sa / la: extra must be null.
- mcq: extra = {{"options": ["<option 1>", "<option 2>", "<option 3>", "<option 4>"], "correct_option": <integer index 0-3 of the correct option>}}.
  Always provide exactly 4 options.
- fill_blank: extra = {{"text_with_blanks": "<sentence with blanks marked as ___>", "blanks": ["<answer for each ___, in order>"]}}.
  The number of "___" markers MUST exactly equal the number of items in "blanks".
- assertion_reason: extra = {{"assertion": "<statement A>", "reason": "<statement R>", "correct_option": "A"|"B"|"C"|"D"}}.

IMPORTANT: the "questions" array must contain EXACTLY {count} item(s) -- not fewer, not more.{avoid_block}

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
    except json.JSONDecodeError as e:
        # Ollama models frequently produce near-valid JSON -- a missing
        # comma between two question objects, a trailing comma, an
        # unescaped quote inside a question string. Rather than let one
        # such slip abort the whole paper, try to repair it before
        # giving up.
        try:
            from json_repair import repair_json
        except ImportError:
            # json_repair isn't installed -- surface this as the same
            # ValueError the caller already knows how to catch and show
            # nicely, instead of an uncaught ImportError crashing the app.
            raise ValueError(f"{e} (and json_repair isn't installed to attempt a fix)") from e
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


def _validate_generated_question(question: dict, item: dict) -> bool:
    """
    Validates ONE question the LLM produced for `item` against the same
    rules add_paper_question will eventually enforce (plus a couple of
    shape checks specific to case_based). This runs INSIDE the retry loop
    -- see _generate_item_questions -- so a malformed question doesn't
    silently count toward "we got enough of this type" and short-circuit
    a retry that would have gotten a usable one instead.
    """
    qtype = question.get("type")

    if item["type"] == "case_based":
        if qtype != "case_based":
            return False
        if not _validate_question_extra_clientside("case_based", question.get("extra")):
            return False
        subs = question.get("sub_questions") or []
        if len(subs) != 3:
            return False
        for sub, expected_marks in zip(subs, item["sub_marks"]):
            sub_type = sub.get("type")
            sub_marks = sub.get("marks")
            sub_extra = sub.get("extra")
            sub_text = sub.get("question_text")
            if sub_type not in SUB_QUESTION_TYPES:
                return False
            if sub_marks != expected_marks:
                return False
            if not _validate_question_extra_clientside(sub_type, sub_extra):
                return False
            if sub_type in ("vsa", "sa", "mcq") and not sub_text:
                return False
        return True

    if qtype != item["type"]:
        return False
    marks = question.get("marks")
    if not isinstance(marks, (int, float)) or marks <= 0:
        return False
    if not _validate_question_extra_clientside(qtype, question.get("extra")):
        return False
    if qtype in ("mcq", "vsa", "sa", "la") and not question.get("question_text"):
        return False
    return True


def _question_summary(question: dict) -> str:
    """
    Short human-readable stand-in for a generated question, used only to
    (a) tell a retry call what's already been used so it doesn't repeat
    itself, and (b) de-duplicate across attempts if the model reproduces
    something close to a question it already gave us.
    """
    text = question.get("question_text")
    if text:
        return text.strip()[:120]
    extra = question.get("extra") or {}
    if extra.get("text_with_blanks"):
        return str(extra["text_with_blanks"]).strip()[:120]
    if extra.get("passage"):
        return str(extra["passage"]).strip()[:120]
    if extra.get("assertion"):
        return str(extra["assertion"]).strip()[:120]
    return "previous question"


def _generate_item_questions(llm, chapter_text: str, chapter: str, language: str, item: dict, timeout_seconds: int) -> tuple:
    """
    Generates item["count"] valid questions for a single question-type
    item, retrying with follow-up calls for whatever's still missing
    rather than accepting whatever the first call happened to return.

    Ollama models routinely under-deliver on "give me exactly N" --
    closing the JSON early once they feel "done," well before
    num_predict is actually exhausted -- so a single call's output is a
    LOWER BOUND, not the final answer. A call that fails outright
    (timeout, unparseable JSON, empty "questions" list) is treated the
    same way: as a shortfall of the full count, retried like any other,
    rather than immediately abandoning the whole question type.

    Each candidate question is validated with _validate_generated_question
    before it counts toward the target, so a malformed question doesn't
    fill a "slot" that a retry could have filled with something usable.

    Returns (valid_questions, attempts_used). valid_questions may still
    be shorter than item["count"] if MAX_ITEM_ATTEMPTS is exhausted --
    the caller is responsible for flagging that shortfall.
    """
    collected = []
    seen_summaries = set()
    attempts = 0

    while len(collected) < item["count"] and attempts < MAX_ITEM_ATTEMPTS:
        remaining = item["count"] - len(collected)
        avoid = [_question_summary(q) for q in collected] if collected else None
        prompt = _build_item_prompt(
            chapter_text, chapter, language, item,
            count_override=remaining, avoid_questions=avoid,
        )
        raw = invoke_with_timeout(llm, prompt, timeout_seconds=timeout_seconds)
        attempts += 1
        if raw is None:
            continue  # timed out -- try again rather than giving up on the type
        try:
            parsed = _extract_json(raw)
        except (ValueError, json.JSONDecodeError):
            continue  # bad JSON this round -- try again

        for q in parsed.get("questions") or []:
            if not _validate_generated_question(q, item):
                continue
            summary = _question_summary(q)
            if summary in seen_summaries:
                continue  # model repeated itself despite the avoid-list
            seen_summaries.add(summary)
            collected.append(q)
            if len(collected) >= item["count"]:
                break

    return collected, attempts


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

    Generation happens ONE QUESTION TYPE AT A TIME, and each type is
    generated via _generate_item_questions -- which retries with
    top-up calls (up to MAX_ITEM_ATTEMPTS) whenever a call returns
    fewer valid questions than requested, rather than silently accepting
    a short first response. A type that still falls short after all
    retries lands in "failed_sections" labelled with how many it
    actually got (e.g. "case_based (2/4)") instead of just vanishing
    from the paper with no visible signal.

    Raises ValueError if question_counts is invalid/empty/too large, the
    chapter text is missing, the LLM is unreachable, or literally every
    question type failed to generate. Raises BridgeRequestError/
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

    # "paper" model_type -- see core/llm.py -- is sized (num_predict) for
    # ONE TYPE's worth of questions per call. ITEM_TIMEOUT_SECONDS is per
    # call; with retries this means several small, fast calls per item
    # instead of one huge one that silently truncates.
    ITEM_TIMEOUT_SECONDS = 75
    llm = get_llm(model_type="paper", request_timeout=ITEM_TIMEOUT_SECONDS)
    if llm is None:
        raise ValueError("Could not connect to the LLM.")

    generated_sections = []  # [{"title", "instructions", "questions": [...]}]
    failed_item_labels = []
    for section in plan:
        section_questions = []
        for item in section["items"]:
            questions, _attempts = _generate_item_questions(
                llm, context_slice, chapter, lang, item, ITEM_TIMEOUT_SECONDS,
            )
            if not questions:
                failed_item_labels.append(item["label"])
                continue
            if len(questions) < item["count"]:
                # Got SOME, but fewer than asked for even after retries --
                # still usable, but flag it instead of pretending the
                # paper has exactly what was requested.
                failed_item_labels.append(f'{item["label"]} ({len(questions)}/{item["count"]})')
            section_questions.extend(questions)
        if section_questions:
            generated_sections.append({
                "title": section["title"],
                "instructions": "Answer all questions.",
                "questions": section_questions,
            })

    if not generated_sections:
        raise ValueError(
            "The model's response couldn't be turned into any valid questions "
            "(every question type failed or timed out). Try generating again, or with fewer questions."
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
        "failed_sections": failed_item_labels,
    }
