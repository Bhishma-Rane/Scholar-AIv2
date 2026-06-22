# 🎓 ScholarAI — by AuraStudios
**Learn. Understand. Master.**

An AI study companion: per-subject document ingestion (RAG via Chroma), an LLM tutor with
slash-commands and a Socratic mode, real graded quizzes (with negative marking and AI-graded
short answers), flashcards, mock exams, structural and visual diagrams, and a full analytics
dashboard that tells you what you've mastered and what needs work. Powered by Ollama (local
LLM) + LangGraph.

## Project Structure

```
app.py                       # Streamlit entry point — run this
config.py                    # Branding, page config, CSS, session-state defaults
requirements.txt

core/                        # Low-level, reusable infrastructure
├── paths.py                   # Per-user/subject/chapter folder layout
├── llm.py                      # Lazy Ollama LLM loader, internet search, image search
├── vectorstore.py               # Chroma vector store build/load, raw chapter text
├── pdf_export.py                 # Plain text -> PDF rendering
├── credentials.py                 # Salted password hashing & storage
├── mermaid_render.py                # Renders Mermaid.js diagrams inline (HTML component)
├── diagram_router.py                 # Classifies a diagram topic: structural vs. visual
├── analytics_store.py                 # The data layer: quiz attempts, mastery, streaks, readiness
└── onboarding_store.py                 # Tracks per-account tutorial completion

features/                    # Business logic / AI features (no Streamlit imports)
├── study_materials.py         # Roadmaps, summaries, cheat sheets, formula sheets,
│                                 vocabulary builder, concept maps, daily goals, mistake profile
├── flashcards.py                # Batch flashcard generation, append-mode, dedup
├── mock_exams.py                 # Printable mock exams + full quiz grading engine
│                                   (objective + AI-graded subjective + negative marking)
├── chat_graph.py                   # LangGraph state machine for the tutor chat
├── diagrams.py                       # Mermaid diagram generation (multi-type, not just flowcharts)
├── socratic_tutor.py                   # Prompt-wrapping for guided, question-based teaching
├── study_memory.py                      # Surfaces weak topics into the chat's context
└── dashboard_ai.py                        # Narrates real analytics data into a progress report

ui/                          # Streamlit rendering only — one file per tab
├── auth.py                     # Login / Sign-up gate
├── sidebar.py                    # Timer, subject/chapter workspace, settings, Replay Tutorial
├── tutorial_content.py             # Pure data: pathway steps + feature reference (no st.* calls)
├── tab_tutorial.py                   # First-run tutorial: welcome, guided pathways, reference
├── tab_dashboard.py                    # AI Dashboard
├── tab_chat.py                           # Tutor (chat + diagrams + Socratic toggle)
├── tab_tools.py                            # Study Tools (materials, concept map, daily goals)
├── tab_flashcards.py                         # Flashcards (study / practice forgotten / manage)
├── tab_batch_gen.py                            # Batch Gen (bulk quiz / mock exam generation)
├── tab_assessment.py                             # Assessment (real grading + results screen)
├── tab_progress.py                                 # Progress (heatmap, streaks, analytics, readiness)
└── tab_viewer.py                                     # Viewer (browse/export generated files)

users/                        # Runtime data (created automatically, gitignored)
└── <username>/
    ├── source_materials/<subject>/      # Uploaded PDFs/TXTs
    ├── chroma_db/                         # Vector store
    ├── analytics.json                       # Quiz history, mastery, streaks (per user)
    ├── onboarding.json                        # Whether this account has completed the tutorial
    └── study/<subject>/<chapter>/
        ├── interactive_quizzes/
        ├── mock_exams/
        ├── study_guides/
        └── flashcards/
```

## Feature Notes

### First-run Tutorial (`ui/tab_tutorial.py` + `ui/tutorial_content.py` + `core/onboarding_store.py`)
Every brand-new account is shown a full-screen tutorial automatically on first login — before
the sidebar or any tabs appear, so there's no clutter to learn around. It opens with a 4-step
overview of the core loop, then offers **three goal-based guided pathways** to pick from:
- *"I want to upload material and start studying"*
- *"I want to test myself"*
- *"I want to see my progress"*

Each pathway is a short numbered walkthrough that names the exact real tab/sidebar section for
each step. There's also a "show me everything" expandable reference covering all 8 tabs, for
anyone who'd rather skip the guided flow. Completion is tracked per-account
(`users/<username>/onboarding.json`) so it never reappears uninvited — but it's always
reachable again via the **❓ Tutorial** tab or the **Replay Tutorial** button in the sidebar.

### Diagrams: structural vs. visual
`!diagram <topic>` in the Tutor tab automatically classifies the topic:
- **Structural** topics (cycles, processes, hierarchies, system architecture) get an
  LLM-generated **Mermaid diagram**. The diagram *type* (flowchart, sequence, mindmap,
  class, state) is chosen in a forced, separate step before generation — this is what fixes
  the "everything comes out as a flowchart" problem; asking a small local model to choose
  *and* generate in one shot biases it toward the most common pattern regardless of fit.
- **Visual/anatomical** topics (e.g. "the human heart", "a plant cell") **cannot** be
  represented by Mermaid — there's no diagram syntax that draws realistic shapes. These are
  routed to a free DuckDuckGo image search instead, pulling a real labeled image.
  **This requires normal internet access** (DuckDuckGo isn't reachable from network-restricted
  sandboxes — verify this works from wherever you actually deploy).

The same multi-type Mermaid generation also powers the **Concept Map** in Study Tools.

### Quiz grading engine (`features/mock_exams.py`)
- Objective (MCQ) questions are graded by exact match — no LLM call needed.
- Subjective (short-answer) questions are graded by the LLM comparing the student's answer
  to the official key, **with partial credit** (e.g. 1.5/2 marks for a partially correct answer).
- **Negative marking** is an optional toggle in the Assessment tab. When on, a wrong
  *attempted* objective answer deducts 25% of that question's marks. Skipped questions and
  subjective answers are never penalized.
- Dynamic quiz generation (`!quiz N` and Batch Gen) now generates in small batches and keeps
  retrying until it hits the exact requested count, since local LLMs reliably undershoot
  "generate exactly N" in a single call for larger N. Duplicate questions across batches are
  filtered out.

### Flashcards (`ui/tab_flashcards.py`)
- **Study All**: the full deck minus mastered (Box 5) cards.
- **Practice Forgotten**: a dedicated queue of only Box-1 ("Forgot") cards.
- **Manage Deck**: generate more cards (appended, deduplicated against the existing deck)
  or reset all progress back to Box 1.

### AI Dashboard (`ui/tab_dashboard.py` + `features/dashboard_ai.py`)
The LLM's job here is **narration, not analysis** — the actual numbers (mastered topics,
weak topics, streak, readiness score) are computed entirely by `core/analytics_store.py`
from real quiz history, and the LLM is only asked to explain that data in plain language.
This keeps the dashboard honest: it can't report progress that doesn't exist.

### Progress tab (`ui/tab_progress.py`)
- **Knowledge Heatmap**: green/amber/red bars per topic by accuracy.
- **Weak Topic Detection**: topics under 60% accuracy (with 2+ attempts) flagged for revision.
- **Study Streaks**: current + longest daily streak.
- **Learning Analytics**: total study time, quiz count, accuracy trend chart.
- **Predicted Exam Readiness Score**: a transparent heuristic (60% recent accuracy, 25% topic
  coverage, 15% consistency) — explicitly not a black-box ML prediction, and it says so.

### Socratic Tutor & Study Chat Memory
- Toggle "Socratic Tutor Mode" in the Tutor tab to make the chat guide with questions instead
  of answering directly (`features/socratic_tutor.py` — a prompt wrapper, no extra LLM cost).
- The tutor automatically pulls in the student's known weak topics (`features/study_memory.py`)
  as background context, so it can be more careful/clear around historically tricky material.

## Why this layout?

- **`core/` has no feature-specific logic.** Change the embedding model, swap image search
  providers, or adjust the readiness-score formula — you touch one file.
- **`features/` has zero Streamlit imports.** Every function takes plain arguments and returns
  plain data. You can unit-test quiz grading or diagram generation without ever starting Streamlit.
- **`ui/` has almost zero business logic.** Each tab file calls into `features/`/`core/` and
  renders the result. Adding a 9th tab means one new file and one import in `app.py`.
- **`app.py` stays tiny.** A table of contents, not an implementation.

## Running

```bash
pip install -r requirements.txt
# Make sure Ollama is running locally with the required models pulled:
#   ollama pull llama3
#   ollama pull nomic-embed-text
streamlit run app.py
```

## Known limitations / honest caveats

- **Image search for visual diagrams needs real internet access.** It was built and unit-tested
  against a mocked search client, but live DuckDuckGo connectivity could not be verified inside
  the sandboxed build environment (network egress there is restricted to package registries).
  Test it on your actual machine before relying on it.
- **Readiness score and topic mastery are heuristics**, not validated predictive models — they're
  transparent and explainable, not "AI-certified," and the UI says so.
- **The credentials store is file-based JSON**, appropriate for personal or small-group use
  (dozens to ~100 users), not built for high-concurrency multi-server production deployments.
- **Negative marking, partial credit, and quiz batching parameters** (25% deduction, 5-question
  batches, etc.) are reasonable defaults defined as constants near the top of their respective
  files — easy to tune if you want different exam conventions.
