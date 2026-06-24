def generate_dynamic_quiz_files(state: AgentState):
    """
    Handles the 'ver <chapter> ! quiz <N>' command, writing a JSON quiz file
    to disk. Optimized for llama 3 on M5 with TIMEOUT PROTECTION.
    """
    import json
    from core.llm import invoke_with_timeout
    
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
            batch_request = min(remaining, 15)

            quiz_inst = quiz_inst_template.format(n=batch_request, lang=lang)
            
            # DEBUG: Log the attempt
            print(f"[ScholarAI] Quiz gen attempt {attempts}/{max_attempts}, batch size {batch_request}")
            
            try:
                # CRITICAL FIX: Use timeout wrapper — 30s per batch for llama3
                raw_out = invoke_with_timeout(
                    quiz_llm,
                    f"{quiz_inst}\n\nContext:\n{context}",
                    timeout_seconds=30
                )
                
                if raw_out is None:
                    print(f"[ScholarAI] Timeout on attempt {attempts}. Skipping batch.")
                    continue
                
                print(f"[ScholarAI] Got response: {len(raw_out)} chars")
                
            except Exception as e:
                print(f"[ScholarAI] LLM error on attempt {attempts}: {type(e).__name__}: {str(e)}")
                continue

            try:
                json_str = extract_clean_json(raw_out)
                batch = json.loads(json_str)
                print(f"[ScholarAI] Parsed {len(batch) if isinstance(batch, list) else '?'} questions from JSON")
                
            except json.JSONDecodeError as e:
                print(f"[ScholarAI] JSON parse failed: {str(e)}")
                print(f"[ScholarAI] Raw output preview: {raw_out[:300]}...")
                continue
            except Exception as e:
                print(f"[ScholarAI] extract_clean_json failed: {type(e).__name__}: {str(e)}")
                continue

            if not isinstance(batch, list):
                print(f"[ScholarAI] Batch is not a list, got {type(batch)}")
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
            print(f"[ScholarAI] After {attempts} attempts, got 0 valid questions")
            return {"response": "JSON Formatting Failed. The LLM did not return any valid questions. Try again."}

        all_questions = all_questions[:target_count]
        shortfall_note = ""
        if len(all_questions) < target_count:
            shortfall_note = (
                f" (Requested {target_count}, generated {len(all_questions)} after {attempts} retries — "
                f"try again or lower the count if this persists.)"
            )

        with open(f"{paths['mcq']}/{safe_verse}_Data.json", "w", encoding="utf-8") as f:
            json.dump(all_questions, f, indent=4)
        
        print(f"[ScholarAI] ✓ Saved {len(all_questions)} questions to {safe_verse}_Data.json")
        return {"response": f"Successfully generated {len(all_questions)} questions!{shortfall_note}"}
        
    except Exception as e:
        print(f"[ScholarAI] Fatal error in generate_dynamic_quiz_files: {str(e)}")
        return {"response": f"JSON Formatting Failed. Try again. Error: {str(e)}"}
