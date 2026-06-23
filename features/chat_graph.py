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


def generate_dynamic_quiz_files(state: AgentState):
    """
    Handles the 'ver <chapter> ! quiz <N>' command, writing a JSON quiz file
    to disk. Optimized for llama 3 on M5 with larger batches.
    """
    match = re.search(r"ver\s+(.+?)\s+!\s+quiz", state["question"], re.IGNORECASE)
    safe_verse = match.group(1).strip() if match else "Generated"

    paths = get_chapter_paths(state["username"], state["subject"], safe_verse)
    lang, context = state["language"], state["context"][:15000]
    target_count = state["quiz_count"]

    quiz_inst_template = """You are an exam generator. Generate EXACTLY {n} questions in {lang} about the
given context. Tag each question with a short "topic" name (a specific sub-concept
within the chapter, 1-4 words, e.g. "Mitochondria", "Krebs Cycle") so progress can be
tracked per topic.
FORMAT MUST BE EXACTLY THIS JSON, NOTHING ELSE — no markdown fences, no preamble:
[
  {{"q": "Question?", "options": ["A) opt1", "B) opt2", "C) opt3", "D) opt4"], "answer": "A", "explanation": "Reason", "type": "objective", "topic": "Specific Topic", "marks": 1}},
  {{"q": "Short answer?", "options": [], "answer": "Expected text", "explanation": "Rubric", "type": "subjective", "topic": "Specific Topic", "marks": 2}}
]"""

    quiz_llm = get_llm("quiz")
    if not quiz_llm:
        return {"response": "Generation failed: LLM engine offline."}

    all_questions = []
    seen_question_texts = set()
    attempts = 0
    max_attempts = target_count + 3

    try:
        while len(all_questions) < target_count and attempts < max_attempts:
            attempts += 1
            remaining = target_count - len(all_questions)
            
            # llama 3 on M5: batch size 15 for efficiency, smaller for final batch
            batch_request = min(remaining, 15)

            quiz_inst = quiz_inst_template.format(n=batch_request, lang=lang)
            raw_out = quiz_llm.invoke(f"{quiz_inst}\n\nContext:\n{context}").content

            try:
                json_str = extract_clean_json(raw_out)
                batch = json.loads(json_str)
            except Exception:
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
                all_questions.append(q)
                if len(all_questions) >= target_count:
                    break

        if not all_questions:
            return {"response": "JSON Formatting Failed. The LLM did not return any valid questions. Try again."}

        all_questions = all_questions[:target_count]
        shortfall_note = ""
        if len(all_questions) < target_count:
            shortfall_note = (
                f" (Requested {target_count}, generated {len(all_questions)} after retries — "
                f"try again or lower the count if this persists.)"
            )

        with open(f"{paths['mcq']}/{safe_verse}_Data.json", "w", encoding="utf-8") as f:
            json.dump(all_questions, f, indent=4)
        return {"response": f"Successfully generated {len(all_questions)} questions!{shortfall_note}"}
    except Exception as e:
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
