"""
ui/tab_question_paper.py
===========================
The "Question Paper" tab: generate a question paper on demand for the
active chapter, choose Practice or Test mode (Test mode adds a
customizable timer), answer questions one at a time (VSA, SA, LA,
case-based, fill-in-the-blanks, assertion-reason -- map-marking comes
in a later update), and see a graded results screen on submit.

Structured the same way as ui/tab_assessment.py: a setup screen, a
question-by-question screen, and a results screen, switched between via
session_state flags -- so this feels like the same app, not a bolted-on
second system.

NOTE: map_marking questions are skipped in the question list for now
(filtered out in _flatten_questions) -- they'll be added once the
click-on-image widget is built. If a paper contains ONLY a map_marking
question in some section, that section will simply show no questions,
which is expected for now.

NOTE on ownership: there's no publish/draft step. Every paper belongs
to the username that generated it and shows up in their own list the
moment it's created -- nothing to approve, nothing shared between
students by default.
"""
import threading

import streamlit as st

from features.question_paper_generator import (
    generate_question_paper,
    estimate_total_marks,
    MAX_COUNT_PER_TYPE,
    MAX_TOTAL_QUESTIONS,
)

from core.bridge_client import (
    list_papers,
    get_paper,
    start_paper_attempt,
    submit_paper_attempt,
    BridgeUnavailableError,
    BridgeRequestError,
)

TIMER_PRESETS = {
    "15 minutes": 15 * 60,
    "30 minutes": 30 * 60,
    "45 minutes": 45 * 60,
    "60 minutes": 60 * 60,
    "Custom": None,
}


def _flatten_questions(paper: dict) -> list:
    """
    Turns the nested sections->questions->[sub_questions] structure into
    a flat ordered list the question-screen can step through one at a
    time, while keeping track of which section each question belongs to
    (for the section-header display) and skipping map_marking questions
    for now (see module docstring).

    Each item: {"section_title": str, "section_instructions": str|None,
                "question": dict, "passage": str|None}
    `passage` is set only for case_based sub-questions, so the question
    screen can show the passage above the sub-question.
    """
    flat = []
    for section in paper.get("sections", []):
        for question in section.get("questions", []):
            if question["type"] == "case_based":
                passage = (question.get("extra") or {}).get("passage")
                for sub in question.get("sub_questions", []):
                    if sub["type"] == "map_marking":
                        continue
                    flat.append({
                        "section_title": section["title"],
                        "section_instructions": section.get("instructions"),
                        "question": sub,
                        "passage": passage,
                    })
            elif question["type"] == "map_marking":
                continue
            else:
                flat.append({
                    "section_title": section["title"],
                    "section_instructions": section.get("instructions"),
                    "question": question,
                    "passage": None,
                })
    return flat


def _render_generate_form(username: str, active_subject: str, active_chapter: str, target_language: str):
    """Lets the user generate a fresh question paper for the active chapter
    on demand -- no admin step in between, it's usable the moment it's built."""
    with st.expander("➕ Generate a new question paper", expanded=False):
        if active_chapter == "Select Chapter":
            st.warning("Select a chapter first to generate a paper from it.")
            return

        title = st.text_input(
            "Paper title:",
            value=f"{active_chapter} — Question Paper",
            key="qp_gen_title",
        )

        st.markdown("**Choose how many of each question type:**")
        col1, col2, col3 = st.columns(3)
        with col1:
            n_mcq = st.number_input("MCQ (1 mark)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=6, step=1, key="qp_gen_n_mcq")
            n_fill = st.number_input("Fill in the Blanks (1 mark)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=4, step=1, key="qp_gen_n_fill")
            n_ar = st.number_input("Assertion-Reason (1 mark)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=4, step=1, key="qp_gen_n_ar")
        with col2:
            n_vsa = st.number_input("Very Short Answer (1 mark)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=6, step=1, key="qp_gen_n_vsa")
            n_sa = st.number_input("Short Answer (2 marks)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=6, step=1, key="qp_gen_n_sa")
        with col3:
            n_la_long = st.number_input("Long Answer (4 marks)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=4, step=1, key="qp_gen_n_la_long")
            n_la_vlong = st.number_input("Very Long Answer (5 marks)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=4, step=1, key="qp_gen_n_la_vlong")
            n_case = st.number_input("Case-Based (4 marks: 1+1+2)", min_value=0, max_value=MAX_COUNT_PER_TYPE, value=4, step=1, key="qp_gen_n_case")

        question_counts = {
            "mcq": n_mcq, "fill_blank": n_fill, "assertion_reason": n_ar,
            "vsa": n_vsa, "sa": n_sa, "la_long": n_la_long, "la_vlong": n_la_vlong,
            "case_based": n_case,
        }
        total_questions = sum(question_counts.values())
        est_marks = estimate_total_marks(question_counts)
        st.caption(f"≈ {total_questions} question(s), ≈ {est_marks} total marks")

        if total_questions > MAX_TOTAL_QUESTIONS:
            st.warning(
                f"That's {total_questions} questions total -- more than {MAX_TOTAL_QUESTIONS} "
                f"can reliably be generated in one go. Reduce a count above, or generate a second "
                f"paper for the rest."
            )

        gen_state = st.session_state.setdefault("qp_gen_state", {"thread": None, "progress": {}, "lock": threading.Lock()})

        if st.button("🚀 Generate Paper", type="primary", key="qp_gen_button", disabled=gen_state["thread"] is not None):
            if total_questions == 0:
                st.error("Pick at least one question of some type before generating.")
                return

            gen_state["progress"] = {}
            gen_state["result"] = None
            gen_state["error"] = None

            def _progress_callback(event):
                # Called from a worker thread inside generate_question_paper --
                # only ever touches the shared dict under its own lock, never
                # calls into Streamlit directly (that's not thread-safe).
                with gen_state["lock"]:
                    gen_state["progress"][event["label"]] = event

            def _run():
                try:
                    gen_state["result"] = generate_question_paper(
                        username=username,
                        subject=active_subject,
                        chapter=active_chapter,
                        title=title,
                        question_counts=question_counts,
                        lang=target_language,
                        progress_callback=_progress_callback,
                    )
                except (ValueError, BridgeRequestError, BridgeUnavailableError) as e:
                    gen_state["error"] = e
                finally:
                    gen_state["thread"] = None

            thread = threading.Thread(target=_run, daemon=True)
            gen_state["thread"] = thread
            thread.start()
            st.rerun()

        if gen_state["thread"] is not None or gen_state.get("result") or gen_state.get("error"):
            _render_generation_progress(gen_state)


@st.fragment(run_every=1)
def _render_generation_progress(gen_state: dict):
    """
    Polls the background generation thread's shared progress dict once a
    second, INSIDE an st.fragment -- so only this small chunk of the page
    reruns on each tick, not the whole script (and, critically, not the
    main st.tabs() call in app.py/tab_practice.py). The old version called
    generate_question_paper() directly inside a blocking st.spinner, which
    held the ENTIRE script thread hostage for up to a couple of minutes
    (MAX_ITEM_ATTEMPTS x ITEM_TIMEOUT_SECONDS across retried items) with no
    fragment boundary to contain it -- a long enough uninterrupted block,
    especially over the ngrok tunnel, for the browser's Streamlit
    WebSocket session to hiccup mid-request. On reconnect after a hiccup
    like that, the frontend can lose track of which stTabs panel should be
    hidden and fall back to rendering every tab's content stacked in one
    long scrollable column. Keeping the wait inside a fragment means the
    rest of the page (including the tabs) never sits in that frozen state
    to begin with.
    """
    with gen_state["lock"]:
        progress_snapshot = dict(gen_state["progress"])

    if gen_state["thread"] is not None:
        if progress_snapshot:
            for label, event in progress_snapshot.items():
                st.progress(
                    min(1.0, event["collected"] / max(1, event["target"])),
                    text=f"{label}: {event['collected']}/{event['target']}",
                )
        else:
            st.caption("Starting generation…")
        return

    error = gen_state.get("error")
    if error is not None:
        if isinstance(error, ValueError):
            st.error(f"Couldn't generate this paper: {error}")
        elif isinstance(error, BridgeRequestError):
            st.error(f"Couldn't generate this paper: {error.detail}")
        else:
            st.error("Can't reach the account server right now. Please try again in a moment.")
        gen_state["error"] = None
        return

    result = gen_state.get("result")
    if result:
        st.success(f"Generated \"{result['title']}\"! Pick it from the list below to start.")
        if result["questions_skipped"]:
            st.caption(f"({result['questions_added']} questions added, {result['questions_skipped']} skipped for not matching the expected format.)")
        gen_state["result"] = None
        st.rerun()

def _render_setup_screen(username: str, active_subject: str, active_chapter: str, target_language: str):
    _render_generate_form(username, active_subject, active_chapter, target_language)

    try:
        papers = list_papers(username=username)
    except BridgeRequestError as e:
        st.error(f"Couldn't load question papers: {e.detail}")
        return
    except BridgeUnavailableError:
        st.error("Can't reach the account server right now. Please try again in a moment.")
        return

    if not papers:
        st.info("You haven't generated any question papers yet -- use \"Generate a new question paper\" above to create one.")
        return

    _render_paper_picker(username, papers)


@st.fragment
def _render_paper_picker(username: str, papers: list):
    """
    Selecting a paper, switching Practice/Test mode, or changing the timer
    preset are all plain widget interactions -- by default each one causes
    a FULL script rerun, which tears down and remounts BOTH levels of
    nested st.tabs() (the main app tabs in app.py, and the Practice & Exams
    sub-tabs in tab_practice.py). Streamlit briefly shows every tab panel's
    content stacked together while that remount is in flight -- a confirmed
    upstream quirk (see streamlit/streamlit community reports on st.tabs
    "glitch where all hidden tab content is shown at once during rerun"),
    made more visible here by the double nesting and by ngrok's added
    round-trip latency on every rerun.

    Wrapping this whole picker in @st.fragment means picking a paper only
    reruns THIS fragment -- the outer tabs never get torn down, so there's
    nothing to remount and no flash. Only "Start", which actually needs to
    swap the whole tab's content over to the question screen, does a real
    (and rare, deliberate) full-app st.rerun().
    """
    paper_labels = [f"{p['title']} ({p.get('subject') or 'General'}) — {p['total_marks']} marks" for p in papers]
    selected_idx = st.selectbox(
        "Choose one of your question papers:",
        range(len(papers)),
        format_func=lambda i: paper_labels[i],
        key="qp_selected_paper_idx",
    )
    selected_paper_summary = papers[selected_idx]

    mode = st.radio(
        "Mode:",
        ["Practice", "Test"],
        horizontal=True,
        key="qp_mode_choice",
        help="Practice: no timer, answer at your own pace. "
             "Test: timed, matches exam conditions.",
    )

    timer_seconds = None
    if mode == "Test":
        preset = st.selectbox("Time limit:", list(TIMER_PRESETS.keys()), key="qp_timer_preset")
        if preset == "Custom":
            custom_minutes = st.number_input(
                "Custom time limit (minutes):", min_value=1, max_value=240, value=30, key="qp_timer_custom"
            )
            timer_seconds = int(custom_minutes * 60)
        else:
            timer_seconds = TIMER_PRESETS[preset]

    if st.button("▶ Start", type="primary"):
        try:
            paper = get_paper(selected_paper_summary["id"])
            flat_questions = _flatten_questions(paper)

            if not flat_questions:
                st.warning("This paper has no answerable questions yet (map-marking questions aren't supported in this view yet).")
                return

            attempt = start_paper_attempt(
                username=username,
                paper_id=paper["id"],
                mode=mode.lower(),
                timer_seconds=timer_seconds,
            )
        except BridgeRequestError as e:
            st.error(f"Couldn't start this paper: {e.detail}")
            return
        except BridgeUnavailableError:
            st.error("Can't reach the account server right now. Please try again in a moment.")
            return

        st.session_state.update({
            "qp_active": True,
            "qp_paper": paper,
            "qp_flat_questions": flat_questions,
            "qp_attempt_id": attempt["attempt_id"],
            "qp_mode": mode.lower(),
            "qp_timer_seconds": attempt.get("timer_seconds"),
            "qp_started_at": attempt["started_at"],
            "qp_current_idx": 0,
            "qp_answers": {},
            "qp_submitted": False,
            "qp_result": None,
        })
        st.rerun()  # deliberate full-app rerun -- this transition really does
                    # need to swap the tab's whole content over to the question
                    # screen, so remounting the tabs here is expected and fine.


def _render_answer_input(question: dict, idx: int):
    """Renders the right input widget for this question's type, and
    stores the answer into st.session_state.qp_answers[question['id']]
    in the shape submit_paper_attempt() expects."""
    qtype = question["type"]
    qid = question["id"]
    existing = st.session_state.qp_answers.get(qid, {})

    if qtype in ("vsa", "sa", "la"):
        height = 80 if qtype == "vsa" else (150 if qtype == "sa" else 250)
        answer = st.text_area("Your answer:", value=existing.get("answer_text", ""), height=height, key=f"qp_input_{idx}")
        st.session_state.qp_answers[qid] = {"question_id": qid, "answer_text": answer}

    elif qtype == "fill_blank":
        extra = question.get("extra") or {}
        text_with_blanks = extra.get("text_with_blanks", "")
        blanks_needed = text_with_blanks.count("___")
        st.markdown(f"*{text_with_blanks}*")
        existing_blanks = existing.get("answer_blanks") or [""] * blanks_needed
        new_blanks = []
        for b in range(blanks_needed):
            val = st.text_input(
                f"Blank {b + 1}:",
                value=existing_blanks[b] if b < len(existing_blanks) else "",
                key=f"qp_input_{idx}_blank_{b}",
            )
            new_blanks.append(val)
        st.session_state.qp_answers[qid] = {"question_id": qid, "answer_blanks": new_blanks}

    elif qtype == "assertion_reason":
        extra = question.get("extra") or {}
        st.markdown(f"**Assertion (A):** {extra.get('assertion', '')}")
        st.markdown(f"**Reason (R):** {extra.get('reason', '')}")
        options = {
            "A": "Both A and R are true, and R is the correct explanation of A.",
            "B": "Both A and R are true, but R is NOT the correct explanation of A.",
            "C": "A is true, but R is false.",
            "D": "A is false, but R is true.",
        }
        current = existing.get("answer_option")
        choice = st.radio(
            "Choose the correct option:",
            list(options.keys()),
            index=list(options.keys()).index(current) if current in options else None,
            format_func=lambda k: f"{k}. {options[k]}",
            key=f"qp_input_{idx}_ar",
        )
        st.session_state.qp_answers[qid] = {"question_id": qid, "answer_option": str(choice)}
        
    elif qtype == "mcq":
        extra = question.get("extra") or {}
        options = extra.get("options", [])
        current = existing.get("answer_option")
        choice = st.radio(
            "Choose the correct option:",
            range(len(options)),
            index=current if isinstance(current, int) else None,
            format_func=lambda i: options[i],
            key=f"qp_input_{idx}_mcq",
        )
        st.session_state.qp_answers[qid] = {"question_id": qid, "answer_option": choice}

    else:
        st.warning(f"This question type ({qtype}) isn't supported in this view yet.")


def _render_question_screen():
    flat_questions = st.session_state.qp_flat_questions
    idx = st.session_state.qp_current_idx
    item = flat_questions[idx]
    question = item["question"]

    st.progress((idx + 1) / len(flat_questions), text=f"Question {idx + 1} of {len(flat_questions)}")

    # Show the section header whenever we've just entered a new section
    # (i.e. the previous question, if any, belonged to a different section).
    entering_new_section = idx == 0 or flat_questions[idx - 1]["section_title"] != item["section_title"]
    if entering_new_section:
        st.subheader(f"📑 {item['section_title']}")
        if item["section_instructions"]:
            st.caption(item["section_instructions"])

    if item["passage"]:
        with st.container(border=True):
            st.markdown(f"**Passage:** {item['passage']}")

    marks_value = question['marks']
    marks_display = int(marks_value) if marks_value == int(marks_value) else marks_value
    marks_label = f" ({marks_display} mark{'s' if marks_value != 1 else ''})"
    if question.get("question_text"):
        st.markdown(f"**Q{idx + 1}.** {question['question_text']}{marks_label}")
    else:
        st.markdown(f"**Q{idx + 1}.**{marks_label}")

    _render_answer_input(question, idx)

    c1, c2, c3 = st.columns(3)
    if c1.button("⬅️ Previous", use_container_width=True) and idx > 0:
        st.session_state.qp_current_idx -= 1
        st.rerun()
    if c2.button("Next ➡️", use_container_width=True) and idx < len(flat_questions) - 1:
        st.session_state.qp_current_idx += 1
        st.rerun()
    if c3.button("✅ Submit Paper", type="primary", use_container_width=True):
        st.session_state.qp_submitted = True
        st.rerun()


def _render_results_screen(username: str, target_language: str):
    if st.session_state.qp_result is None:
        with st.spinner("Grading your answers..."):
            answers_list = list(st.session_state.qp_answers.values())
            try:
                result = submit_paper_attempt(
                    attempt_id=st.session_state.qp_attempt_id,
                    username=username,
                    answers=answers_list,
                    lang=target_language,
                )
                st.session_state.qp_result = result
            except BridgeRequestError as e:
                st.error(f"Couldn't submit: {e.detail}")
                return
            except BridgeUnavailableError:
                st.error("Can't reach the account server right now. Please try again in a moment.")
                return

    result = st.session_state.qp_result
    pct = (result["total_score"] / result["max_score"] * 100) if result["max_score"] else 0

    st.success("Paper Graded!")
    if result.get("auto_submitted"):
        st.warning("This attempt ran past the time limit and was auto-submitted.")

    col1, col2 = st.columns(2)
    col1.metric("Score", f"{result['total_score']} / {result['max_score']}")
    col2.metric("Percentage", f"{pct:.1f}%")

    st.markdown("---")
    st.subheader("Question-by-Question Breakdown")

    flat_questions = st.session_state.qp_flat_questions
    questions_by_id = {item["question"]["id"]: item["question"] for item in flat_questions}

    for r in result["per_question"]:
        question = questions_by_id.get(r["question_id"], {})
        icon = "✅" if r["marks_earned"] == r["marks_possible"] else ("⚠️" if r["marks_earned"] > 0 else "❌")
        label = question.get("question_text") or f"({r['type']} question)"
        with st.expander(f"{icon} {label} — {r['marks_earned']}/{r['marks_possible']} marks"):
            answer = st.session_state.qp_answers.get(r["question_id"], {})
            shown_answer = (
                answer.get("answer_text")
                or (", ".join(answer.get("answer_blanks", [])) if answer.get("answer_blanks") else None)
                or answer.get("answer_option")
                or "*Skipped*"
            )
            st.markdown(f"**Your answer:** {shown_answer}")
            if r.get("feedback"):
                st.markdown(f"**Feedback:** {r['feedback']}")

    if st.button("Back to Question Papers"):
        for key in list(st.session_state.keys()):
            if key.startswith("qp_"):
                del st.session_state[key]
        st.rerun()


def render_question_paper_tab(username: str, active_subject: str = None, active_chapter: str = None, target_language: str = "English"):
    st.header("📝 Question Paper")

    if "qp_active" not in st.session_state:
        st.session_state.qp_active = False
    if "qp_submitted" not in st.session_state:
        st.session_state.qp_submitted = False

    if not st.session_state.qp_active:
        _render_setup_screen(username, active_subject, active_chapter, target_language)
    elif not st.session_state.qp_submitted:
        _render_question_screen()
    else:
        _render_results_screen(username, target_language)
        
