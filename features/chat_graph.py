"""
features/chat_graph.py
========================
The conversational tutor's LangGraph state machine.

Flow:
    retrieve_verses --(route_by_intent)--> generate_standard_modes
                                        --> generate_dynamic_quiz_files
                                        --> handle_fallback

retrieve_verses parses the user's command (!mcq, !summary, "ver X ! quiz N", etc.),
fetches context either from the local vector store, a specific chapter's raw text,
or the internet, then routes to the right generation node.
"""
import re
import json
import math
from typing import TypedDict, List

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, START, END

from core.llm import get_llm, internet_search
from core.paths import get_chapter_paths
from core.vectorstore import get_vector_store, get_chapter_text
from features.mock_exams import extract_clean_json


class AgentState(TypedDict):
    question: str
    chat_history: List[BaseMessage]
    context: str
    relevance_score: float
    source_file: str
    command_mode: str
    quiz_count: int
    response: str
    active_chapter: str
    data_source: str
    language: str
    username: str
    subject: str


STANDARD_MODE_INSTRUCTIONS = {
    "mcq": "Generate ONE multiple-choice question.",
    "short": "Generate ONE short-answer question.",
    "long": "Generate ONE essay question.",
    "summary": "Provide a summary.",
    "explain": "Explain simply.",
    "translate": "Translate accurately.",
    "quizme": "Generate 3 quick questions.",
}

# --- Quiz chunking tuning knobs -------------------------------------------
# Chunk size in characters. ~2500 chars is roughly 600-700 tokens, which
# keeps per-chunk prompt-eval time low (a few seconds on M-series Macs)
# instead of the ~15-20s prompt-eval cost of stuffing in 15,000 chars at once.
QUIZ_CHUNK_SIZE = 2500

# Cap on questions requested from a single chunk in one LLM call, so a chunk
# with a high quota (e.g. target_count > num_chunks) doesn't balloon a
# single call's output size and generation time.
MAX_QUESTIONS_PER_CALL = 4

# Per-call timeout. Chunked prompts are far smaller than the old "whole
# chapter truncated to 15k chars" prompt, so generation per call should
# typically land in 10-25s. 60s gives comfortable headroom without letting
# a stuck call linger too long before being aborted (and retried).
QUIZ_CALL_TIMEOUT = 60


def retrieve_verses(state: AgentState):
    """Parses the command in the user's message and fetches the relevant context."""
    user_input = state["question"].strip()
    active_chap = state.get("active_chapter", "")
    d_source = state.get("data_source", "Local Docs")

    # Fix Issue #8 & #25: Safe, non-greedy regex.
    quiz_match = re.search(r"ver\s+(.+?)\s+!\s+quiz\s*(\d+)", user_input, re.IGNORECASE)

    if quiz_match:
        mode, target_chap, count = "dynamic_quiz", quiz_match.group(1).strip(), int(quiz_match.group(2))
    elif "!mcq" in user_input.lower():
        mode, count, target_chap = "mcq", 1, active_chap
    elif "!short" in user_input.lower():
        mode, count, target_chap = "short", 1, active_chap
    elif "!long" in user_input.lower():
        mode, count, target_chap = "long", 1, active_chap
    elif "!summary" in user_input.lower():
        mode, count, target_chap = "summary", 0, active_chap
    elif "!explain" in user_input.lower():
        mode, count, target_chap = "explain", 0, active_chap
    elif "!translate" in user_input.lower():
        mode, count, target_chap = "translate", 0, active_chap
    elif "!quizme" in user_input.lower():
        mode, count, target_chap = "quizme", 3, active_chap
    else:
        mode, count, target_chap = "standard", 0, active_chap

    if d_source == "Internet Search" and mode != "dynamic_quiz":
        try:
            return {
                "context": internet_search.invoke(user_input),
                "relevance_score": 1.0,
                "source_file": "Live Search",
                "command_mode": mode,
                "quiz_count": count,
            }
        except Exception:
            return {
                "context": "Internet search failed.",
                "relevance_score": 0.0,
                "source_file": "Error",
                "command_mode": mode,
                "quiz_count": count,
            }

    if mode == "dynamic_quiz" and target_chap not in ["Select Chapter", ""]:
        exact_text = get_chapter_text(state["username"], state["subject"], target_chap)
        if exact_text:
            return {
                "context": exact_text,
                "relevance_score": 1.0,
                "source_file": target_chap,
                "command_mode": mode,
                "quiz_count": count,
            }

    store = get_vector_store(state["username"], state["subject"])
    if not store:
        return {
            "context": "",
            "relevance_score": 0.0,
            "source_file": "Unknown",
            "command_mode": mode,
            "quiz_count": count,
        }

    # Fix Issue #22 & #23: Safe results check and top_k.
    results = store.similarity_search_with_relevance_scores(
        user_input,
        k=3,
        **({"filter": {"chapter": target_chap}} if target_chap not in ["Select Chapter", ""] else {}),
    )

    if not results:
        return {
            "context": "",
            "relevance_score": 0.0,
            "source_file": "Unknown",
            "command_mode": mode,
            "quiz_count": count,
        }

    return {
        "context": "\n\n---\n\n".join(d.page_content for d, _ in results),
        "relevance_score": results[0][1],
        "source_file": results[0][0].metadata.get("chapter", "Unknown"),
        "command_mode": mode,
        "quiz_count": count,
    }


def generate_standard_modes(state: AgentState):
    """Handles all conversational / single-question commands plus plain chat."""
    mode, lang = state["command_mode"], state["language"]

    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"Respond strictly in {lang}.\n{STANDARD_MODE_INSTRUCTIONS.get(mode, 'Explain directly.')}\nContext:\n{{context}}",
            ),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{question}"),
        ]
    )
    answer = (prompt | llm | StrOutputParser()).invoke(
        {
            "context": state["context"],
            "chat_history": state["chat_history"],
            "question": state["question"],
        }
    )
    return {"response": answer + f"\n\n*Source: {state.get('source_file', 'Unknown')}*"}


def _split_into_chunks(text: str, chunk_size: int = QUIZ_CHUNK_SIZE) -> list:
    """
    Splits chapter text into roughly equal chunks of `chunk_size` characters.
    Splits on paragraph/newline boundaries where possible so a chunk doesn't
    cut off mid-sentence, which would give the LLM a worse, choppier context.
    """
    if not text:
        return []

    chunks = []
    pos = 0
    text_len = len(text)

    while pos < text_len:
        end = min(pos + chunk_size, text_len)
        if end < text_len:
            # try to break at the last paragraph or sentence boundary
            # within this window, so we don't slice mid-sentence.
            window = text[pos:end]
            break_point = max(window.rfind("\n\n"), window.rfind(". "))
            if break_point > chunk_size * 0.5:  # only use it if reasonably close to the end
                end = pos + break_point + 1
        chunk = text[pos:end].strip()
        if chunk:
            chunks.append(chunk)
        pos = end

    return chunks


def _plan_question_distribution(num_chunks: int, target_count: int) -> list:
    """
    Decides how many questions to request from each chunk.

    - If target_count >= num_chunks: every chunk contributes at least one
      question (base = target_count // num_chunks), with the remainder
      spread across the first few chunks.
    - If target_count < num_chunks: rather than only sampling the FIRST
      `target_count` chunks (which would just recreate the old "only reads
      the start of the chapter" problem at a smaller scale), we sample
      chunk indices evenly spaced across the whole chapter so a small
      number of questions still covers the chapter's beginning, middle,
      and end.

    Returns a list of length num_chunks, where dist[i] = number of
    questions to request from chunk i (0 means "skip this chunk").
    """
    if num_chunks <= 0 or target_count <= 0:
        return [0] * max(num_chunks, 0)

    if target_count >= num_chunks:
        base = target_count // num_chunks
        remainder = target_count % num_chunks
        return [base + (1 if i < remainder else 0) for i in range(num_chunks)]

    # Sparse case: evenly spaced sample of chunk indices across the chapter.
    if target_count == 1:
        chosen_indices = [num_chunks // 2]
    else:
        step = (num_chunks - 1) / (target_count - 1)
        chosen_indices = sorted(set(round(i * step) for i in range(target_count)))
        # Rounding can collapse two targets onto the same index for small
        # chapters/odd ratios — top up by picking the largest unused gaps.
        while len(chosen_indices) < target_count:
            remaining = [i for i in range(num_chunks) if i not in chosen_indices]
            if not remaining:
                break
            chosen_indices.append(remaining[len(chosen_indices) % len(remaining)])
            chosen_indices = sorted(set(chosen_indices))

    dist = [0] * num_chunks
    for idx in chosen_indices[:target_count]:
        dist[idx] += 1

    # If duplicate collapsing left us short, stack extras onto already-
    # chosen chunks rather than leaving the target count unmet.
    shortfall = target_count - sum(dist)
    i = 0
    chosen_list = [idx for idx in chosen_indices if idx < num_chunks] or [0]
    while shortfall > 0:
        dist[chosen_list[i % len(chosen_list)]] += 1
        shortfall -= 1
        i += 1

    return dist


QUIZ_INSTRUCTION_TEMPLATE = """You are an exam generator. Generate EXACTLY {n} questions in {lang} about the
given context only. Tag each question with a short "topic" name (a specific sub-concept
within the passage, 1-4 words, e.g. "Mitochondria", "Krebs Cycle") so progress can be
tracked per topic.
FORMAT MUST BE EXACTLY THIS JSON, NOTHING ELSE — no markdown fences, no preamble:
[
  {{"q": "Question?", "options": ["A) opt1", "B) opt2", "C) opt3", "D) opt4"], "answer": "A", "explanation": "Reason", "type": "objective", "topic": "Specific Topic", "marks": 1}},
  {{"q": "Short answer?", "options": [], "answer": "Expected text", "explanation": "Rubric", "type": "subjective", "topic": "Specific Topic", "marks": 2}}
]"""


def _generate_questions_for_chunk(quiz_llm, chunk_text: str, n: int, lang: str, safe_verse: str,
                                   seen_question_texts: set, max_retries: int = 2) -> list:
    """
    Requests `n` questions grounded in a single chunk of text. Retries up to
    `max_retries` times on timeout/parse failure. Returns a list of valid,
    deduplicated question dicts (length <= n).
    """
    from core.llm import invoke_with_timeout

    collected = []
    attempts = 0
    # Cap how many we ask for in one call — keeps each call's output (and
    # therefore generation time) small and predictable.
    request_n = min(n, MAX_QUESTIONS_PER_CALL)

    while len(collected) < n and attempts < max_retries + 1:
        attempts += 1
        still_needed = min(n - len(collected), MAX_QUESTIONS_PER_CALL)
        quiz_inst = QUIZ_INSTRUCTION_TEMPLATE.format(n=still_needed, lang=lang)
        full_prompt = f"{quiz_inst}\n\nContext:\n{chunk_text}"

        try:
            raw_out = invoke_with_timeout(quiz_llm, full_prompt, timeout_seconds=QUIZ_CALL_TIMEOUT)
            if raw_out is None:
                print(f"[ScholarAI] Chunk call timed out (attempt {attempts}/{max_retries + 1})")
                continue
        except Exception as e:
            print(f"[ScholarAI] Chunk call error: {type(e).__name__}: {e}")
            continue

        try:
            json_str = extract_clean_json(raw_out)
            batch = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"[ScholarAI] Chunk JSON parse failed: {e} | preview: {raw_out[:200]}...")
            continue
        except Exception as e:
            print(f"[ScholarAI] extract_clean_json failed: {type(e).__name__}: {e}")
            continue

        if not isinstance(batch, list):
            continue

        for q in batch:
            if not all(k in q for k in ["q", "answer", "type"]):
                continue
            if q["q"] in seen_question_texts:
                continue
            q.setdefault("topic", safe_verse)
            q.setdefault("marks", 1)
            seen_question_texts.add(q["q"])
            collected.append(q)
            if len(collected) >= n:
                break

    return collected


def generate_dynamic_quiz_files(state: AgentState):
    """
    Handles the 'ver <chapter> ! quiz <N>' command, writing a JSON quiz file
    to disk.

    CHUNKED GENERATION: instead of truncating the chapter to the first
    15,000 characters and sending it as one giant prompt (slow, and biased
    toward only the start of the chapter), the full chapter text is split
    into ~2500-char chunks. Questions are distributed across chunks —
    spread evenly across the whole chapter when there are fewer questions
    than chunks — and one smaller, faster LLM call is made per chunk-quota.
    This gives both better topic coverage AND lower latency per call, since
    each prompt is now a few hundred tokens of context instead of ~4000+.
    """
    match = re.search(r"ver\s+(.+?)\s+!\s+quiz", state["question"], re.IGNORECASE)
    safe_verse = match.group(1).strip() if match else "Generated"

    paths = get_chapter_paths(state["username"], state["subject"], safe_verse)
    lang = state["language"]
    full_context = state["context"] or ""
    target_count = state["quiz_count"]

    quiz_llm = get_llm("quiz")
    if not quiz_llm:
        return {"response": "Generation failed: LLM engine offline."}

    chunks = _split_into_chunks(full_context, QUIZ_CHUNK_SIZE)
    if not chunks:
        return {"response": "Generation failed: no chapter content available to generate questions from."}

    distribution = _plan_question_distribution(len(chunks), target_count)
    used_chunks = sum(1 for d in distribution if d > 0)
    print(f"[ScholarAI] Quiz gen: {len(chunks)} chunks total, sampling {used_chunks} of them "
          f"for {target_count} questions (chapter='{safe_verse}')")

    all_questions = []
    seen_question_texts = set()

    try:
        for idx, n_for_chunk in enumerate(distribution):
            if n_for_chunk <= 0:
                continue
            if len(all_questions) >= target_count:
                break

            chunk_text = chunks[idx]
            still_needed_overall = target_count - len(all_questions)
            n_request = min(n_for_chunk, still_needed_overall)

            print(f"[ScholarAI] Chunk {idx + 1}/{len(chunks)}: requesting {n_request} question(s)")

            chunk_questions = _generate_questions_for_chunk(
                quiz_llm, chunk_text, n_request, lang, safe_verse, seen_question_texts
            )
            all_questions.extend(chunk_questions)
            print(f"[ScholarAI] Chunk {idx + 1} yielded {len(chunk_questions)}/{n_request} question(s) "
                  f"— total so far: {len(all_questions)}/{target_count}")

        # If some chunks under-delivered (timeouts/parse failures), make a
        # final pass over any remaining un-sampled chunks to try to fill
        # the shortfall, rather than giving up early.
        if len(all_questions) < target_count:
            unused_chunk_indices = [i for i, d in enumerate(distribution) if d == 0]
            for idx in unused_chunk_indices:
                if len(all_questions) >= target_count:
                    break
                still_needed = target_count - len(all_questions)
                n_request = min(still_needed, MAX_QUESTIONS_PER_CALL)
                print(f"[ScholarAI] Shortfall fill — trying chunk {idx + 1}/{len(chunks)} for {n_request} question(s)")
                chunk_questions = _generate_questions_for_chunk(
                    quiz_llm, chunks[idx], n_request, lang, safe_verse, seen_question_texts
                )
                all_questions.extend(chunk_questions)

        if not all_questions:
            print("[ScholarAI] Got 0 valid questions across all sampled chunks")
            return {"response": "JSON Formatting Failed. The LLM did not return any valid questions. Try again."}

        all_questions = all_questions[:target_count]
        shortfall_note = ""
        if len(all_questions) < target_count:
            shortfall_note = (
                f" (Requested {target_count}, generated {len(all_questions)} — "
                f"try again or lower the count if this persists.)"
            )

        with open(f"{paths['mcq']}/{safe_verse}_Data.json", "w", encoding="utf-8") as f:
            json.dump(all_questions, f, indent=4)

        print(f"[ScholarAI] \u2713 Saved {len(all_questions)} questions to {safe_verse}_Data.json")
        return {"response": f"Successfully generated {len(all_questions)} questions!{shortfall_note}"}

    except Exception as e:
        print(f"[ScholarAI] Fatal error in generate_dynamic_quiz_files: {str(e)}")
        return {"response": f"JSON Formatting Failed. Try again. Error: {str(e)}"}


def handle_fallback(state: AgentState):
    return {
        "response": "I'm not confident enough in the retrieved material. Please clarify or check the loaded documents."
    }


def route_by_intent(state: AgentState):
    """Fix Issue #24: Adjusted relevance heuristic."""
    if state["relevance_score"] < 0.2 and state["data_source"] != "Internet Search":
        return "not_relevant"
    if state["command_mode"] == "dynamic_quiz":
        return "trigger_dynamic_quiz"
    return "ready_to_generate"


def _build_graph():
    builder = StateGraph(AgentState)
    for node, func in [
        ("retrieve_verses", retrieve_verses),
        ("generate_standard_modes", generate_standard_modes),
        ("generate_dynamic_quiz_files", generate_dynamic_quiz_files),
        ("handle_fallback", handle_fallback),
    ]:
        builder.add_node(node, func)

    builder.add_edge(START, "retrieve_verses")
    builder.add_conditional_edges(
        "retrieve_verses",
        route_by_intent,
        {
            "not_relevant": "handle_fallback",
            "trigger_dynamic_quiz": "generate_dynamic_quiz_files",
            "ready_to_generate": "generate_standard_modes",
        },
    )
    for node in ["generate_standard_modes", "generate_dynamic_quiz_files", "handle_fallback"]:
        builder.add_edge(node, END)

    return builder.compile()


# Compiled graph, ready to .invoke({...}) — imported by the chat & batch-gen UI tabs.
vedic_graph = _build_graph()
