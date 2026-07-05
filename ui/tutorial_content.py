"""
ui/tutorial_content.py
=========================
Pure content for the onboarding tutorial — no Streamlit calls here, just
data structures. Kept separate from ui/tab_tutorial.py (the renderer) so
the actual wording/steps can be edited without touching rendering logic.

Each step now carries a `target`: a match spec telling the spotlight
overlay (ui/tutorial_overlay.py) exactly which real element to highlight,
by its actual visible label/text -- NOT a guessed CSS selector or
sibling index, since those break silently the moment Streamlit's
internal markup shifts. Match types:
  - {"type": "css", "selector": "..."}          -- unique elements (sidebar itself)
  - {"type": "tab_text", "text": "..."}          -- a tab button, by its exact label
  - {"type": "widget_label", "label": "...", "container": "..."} --
        an input/selectbox/uploader, found via its label text, then
        widened to the given container testid (stTextInput, stSelectbox,
        stFileUploader, etc.) so the whole widget is highlighted, not
        just the (possibly visually hidden) label text itself.
  - {"type": "button_text", "text": "..."}       -- a plain button, by its exact label

IMPORTANT: `target` label/text values must exactly match the real
widget labels in ui/sidebar.py and the real tab labels in app.py.
If either changes, update the corresponding target here too.
"""

# Each "pathway" is a guided sequence of steps for a specific goal.
# Each step: a short title, a description, which real tab it lives on
# (informational only, shown in the picker/reference), and a `target`
# telling the overlay what to actually highlight.
PATHWAYS = {
    "upload_and_study": {
        "label": "📚 I want to upload material and start studying",
        "steps": [
            {
                "title": "1. Create a Subject",
                "tab": "Sidebar",
                "body": (
                    "In the left sidebar, under **Subject Workspace**, type a subject name "
                    "(e.g. \"Biology\") and click **Create**. This is just a folder — you can "
                    "make as many subjects as you like."
                ),
                "target": {
                    "type": "widget_label",
                    "label": "➕ Create New Subject",
                    "container": "stTextInput",
                },
            },
            {
                "title": "2. Upload Your Material",
                "tab": "Sidebar",
                "body": (
                    "Select your new subject from the **Select Subject** dropdown, then upload a "
                    "**PDF or TXT** file using the uploader that appears. Each file becomes a "
                    "\"chapter\" you can study independently."
                ),
                "target": {
                    "type": "widget_label",
                    "label": "Upload PDF or TXT",
                    "container": "stFileUploader",
                },
            },
            {
                "title": "3. Select Your Active Chapter",
                "tab": "Sidebar",
                "body": (
                    "Once uploaded, pick it from the **Active Chapter** dropdown. Everything else "
                    "in the app — chat, flashcards, quizzes, study tools — works on whichever "
                    "chapter is active here."
                ),
                "target": {
                    "type": "widget_label",
                    "label": "Active Chapter",
                    "container": "stSelectbox",
                },
            },
            {
                "title": "4. Ask the Tutor Anything",
                "tab": "💬 Tutor",
                "body": (
                    "Head to the **Tutor** tab and just ask a question about your material — "
                    "it answers using your uploaded content. Try slash-commands too: "
                    "`!summary`, `!explain`, `!mcq`, or `!diagram <topic>`."
                ),
                "target": {"type": "tab_text", "text": "💬 Tutor"},
            },
            {
                "title": "5. Generate Study Aids",
                "tab": "📚 Study",
                "body": (
                    "In **Study**, generate a Study Roadmap, Summary, Cheat Sheet, Formula "
                    "Sheet, Vocabulary Builder, or a visual Concept Map — all built from your "
                    "actual uploaded chapter."
                ),
                "target": {"type": "tab_text", "text": "📚 Study"},
            },
        ],
    },
    "test_myself": {
        "label": "📝 I want to test myself",
        "steps": [
            {
                "title": "1. Generate a Quiz",
                "tab": "📝 Practice & Exams",
                "body": (
                    "In **Practice & Exams**, choose \"Mixed Interactive Quiz\", set how many "
                    "questions you want, and click Generate. This creates a real, gradeable quiz "
                    "from your active chapter."
                ),
                "target": {"type": "tab_text", "text": "📝 Practice & Exams"},
            },
            {
                "title": "2. Take the Quiz",
                "tab": "📝 Practice & Exams",
                "body": (
                    "Load the quiz and answer each question. You can flip between Multiple "
                    "Choice and Short Answer questions — short answers are graded by AI with "
                    "partial credit, not just right/wrong."
                ),
                "target": {"type": "tab_text", "text": "📝 Practice & Exams"},
            },
            {
                "title": "3. Try Negative Marking (Optional)",
                "tab": "📝 Practice & Exams",
                "body": (
                    "Before loading a quiz, you can toggle **Negative Marking** on if you want "
                    "exam-style scoring, where wrong attempted MCQs deduct partial marks. Skipped "
                    "questions are never penalized."
                ),
                "target": {"type": "tab_text", "text": "📝 Practice & Exams"},
            },
            {
                "title": "4. Review Your Results",
                "tab": "📝 Practice & Exams",
                "body": (
                    "After submitting, you'll see your total score and a question-by-question "
                    "breakdown with feedback — including why a short answer earned partial credit."
                ),
                "target": {"type": "tab_text", "text": "📝 Practice & Exams"},
            },
            {
                "title": "5. Practice with Flashcards",
                "tab": "📚 Study",
                "body": (
                    "Generate a flashcard deck and study it. Mark cards \"Forgot\" or \"Knew It\" — "
                    "forgotten cards land in the dedicated **Practice Forgotten** queue so you can "
                    "drill exactly what's tripping you up."
                ),
                "target": {"type": "tab_text", "text": "📚 Study"},
            },
        ],
    },
    "track_progress": {
        "label": "📊 I want to see my progress",
        "steps": [
            {
                "title": "1. Take a Few Quizzes First",
                "tab": "📝 Practice & Exams",
                "body": (
                    "Progress tracking is built from real quiz attempts — take at least 1-2 "
                    "quizzes before checking this out, or there won't be much to show yet."
                ),
                "target": {"type": "tab_text", "text": "📝 Practice & Exams"},
            },
            {
                "title": "2. Check the AI Dashboard",
                "tab": "🤖 Dashboard",
                "body": (
                    "The **Dashboard** tab gives you a plain-language summary: what you've "
                    "mastered, what needs work, and what to do next — written by AI, but based "
                    "entirely on your real quiz history, not guesses."
                ),
                "target": {"type": "tab_text", "text": "🤖 Dashboard"},
            },
            {
                "title": "3. Explore the Progress Tab",
                "tab": "📊 Progress",
                "body": (
                    "The **Progress** tab shows a color-coded Knowledge Heatmap (green = mastered, "
                    "red = needs work), your weakest topics, study streaks, accuracy trends over "
                    "time, and a Predicted Exam Readiness Score."
                ),
                "target": {"type": "tab_text", "text": "📊 Progress"},
            },
            {
                "title": "4. Let the Tutor Remember Your Weak Spots",
                "tab": "💬 Tutor",
                "body": (
                    "Once weak topics are detected, the Tutor chat automatically knows about them "
                    "and will be extra careful explaining those areas — you don't have to remind it."
                ),
                "target": {"type": "tab_text", "text": "💬 Tutor"},
            },
        ],
    },
}

# Flat list of every tab/feature, for the "show me everything" reference view.
FEATURE_REFERENCE = [
    {
        "tab": "🤖 Dashboard",
        "summary": "An AI-written progress report — what you've perfected, what needs work, and recommended next steps, based on your real quiz history.",
    },
    {
        "tab": "💬 Tutor",
        "summary": "Chat about your uploaded material. Slash-commands: !summary, !explain, !mcq, !short, !long, !translate, !quizme, !diagram <topic>. Toggle Socratic Mode to be guided with questions instead of given direct answers.",
    },
    {
        "tab": "📚 Study",
        "summary": "Generate Study Roadmaps, Summaries, Cheat Sheets, Formula Sheets, Vocabulary Builders, visual Concept Maps, drill Flashcards, and browse/download every generated file for your active chapter.",
    },
    {
        "tab": "📝 Practice & Exams",
        "summary": "Bulk-generate and take interactive quizzes with real grading (exact-match MCQs, AI partial-credit short answers, optional negative marking), or build a full printable Question Paper with sections and a timer.",
    },
    {
        "tab": "📊 Progress",
        "summary": "Knowledge Heatmap, Weak Topic Detection, Study Streaks, Learning Analytics (time/accuracy/trends), and a Predicted Exam Readiness Score.",
    },
    {
        "tab": "⚙️ Settings",
        "summary": "Account info, accent color picker, and a button to replay this tutorial anytime.",
    },
]
