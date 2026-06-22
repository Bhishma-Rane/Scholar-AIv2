"""
ui/tutorial_content.py
=========================
Pure content for the onboarding tutorial — no Streamlit calls here, just
data structures. Kept separate from ui/tab_tutorial.py (the renderer) so
the actual wording/steps can be edited without touching rendering logic,
and so the content is easy to scan/update as features change.
"""

# Each "pathway" is a guided sequence of steps for a specific goal.
# Each step: a short title, a description, and which real tab it points to.
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
            },
            {
                "title": "2. Upload Your Material",
                "tab": "Sidebar",
                "body": (
                    "Select your new subject from the dropdown, then upload a **PDF or TXT** "
                    "file using the uploader that appears. Each file becomes a \"chapter\" you can "
                    "study independently."
                ),
            },
            {
                "title": "3. Select Your Active Chapter",
                "tab": "Sidebar",
                "body": (
                    "Once uploaded, pick it from the **Active Chapter** dropdown. Everything else "
                    "in the app — chat, flashcards, quizzes, study tools — works on whichever "
                    "chapter is active here."
                ),
            },
            {
                "title": "4. Ask the Tutor Anything",
                "tab": "💬 Tutor",
                "body": (
                    "Head to the **Tutor** tab and just ask a question about your material — "
                    "it answers using your uploaded content. Try slash-commands too: "
                    "`!summary`, `!explain`, `!mcq`, or `!diagram <topic>`."
                ),
            },
            {
                "title": "5. Generate Study Aids",
                "tab": "🛠️ Study Tools",
                "body": (
                    "In **Study Tools**, generate a Study Roadmap, Summary, Cheat Sheet, Formula "
                    "Sheet, Vocabulary Builder, or a visual Concept Map — all built from your "
                    "actual uploaded chapter."
                ),
            },
        ],
    },
    "test_myself": {
        "label": "📝 I want to test myself",
        "steps": [
            {
                "title": "1. Generate a Quiz",
                "tab": "⚡ Batch Gen",
                "body": (
                    "In **Batch Gen**, choose \"Mixed Interactive Quiz\", set how many questions "
                    "you want, and click Generate. This creates a real, gradeable quiz from your "
                    "active chapter."
                ),
            },
            {
                "title": "2. Take the Quiz",
                "tab": "📝 Assessment",
                "body": (
                    "Go to **Assessment**, load the quiz, and answer each question. You can flip "
                    "between Multiple Choice and Short Answer questions — short answers are graded "
                    "by AI with partial credit, not just right/wrong."
                ),
            },
            {
                "title": "3. Try Negative Marking (Optional)",
                "tab": "📝 Assessment",
                "body": (
                    "Before loading a quiz, you can toggle **Negative Marking** on if you want "
                    "exam-style scoring, where wrong attempted MCQs deduct partial marks. Skipped "
                    "questions are never penalized."
                ),
            },
            {
                "title": "4. Review Your Results",
                "tab": "📝 Assessment",
                "body": (
                    "After submitting, you'll see your total score and a question-by-question "
                    "breakdown with feedback — including why a short answer earned partial credit."
                ),
            },
            {
                "title": "5. Practice with Flashcards",
                "tab": "🗂️ Flashcards",
                "body": (
                    "Generate a flashcard deck and study it. Mark cards \"Forgot\" or \"Knew It\" — "
                    "forgotten cards land in the dedicated **Practice Forgotten** queue so you can "
                    "drill exactly what's tripping you up."
                ),
            },
        ],
    },
    "track_progress": {
        "label": "📊 I want to see my progress",
        "steps": [
            {
                "title": "1. Take a Few Quizzes First",
                "tab": "📝 Assessment",
                "body": (
                    "Progress tracking is built from real quiz attempts — take at least 1-2 "
                    "quizzes before checking this out, or there won't be much to show yet."
                ),
            },
            {
                "title": "2. Check the AI Dashboard",
                "tab": "🤖 Dashboard",
                "body": (
                    "The **Dashboard** tab gives you a plain-language summary: what you've "
                    "mastered, what needs work, and what to do next — written by AI, but based "
                    "entirely on your real quiz history, not guesses."
                ),
            },
            {
                "title": "3. Explore the Progress Tab",
                "tab": "📊 Progress",
                "body": (
                    "The **Progress** tab shows a color-coded Knowledge Heatmap (green = mastered, "
                    "red = needs work), your weakest topics, study streaks, accuracy trends over "
                    "time, and a Predicted Exam Readiness Score."
                ),
            },
            {
                "title": "4. Let the Tutor Remember Your Weak Spots",
                "tab": "💬 Tutor",
                "body": (
                    "Once weak topics are detected, the Tutor chat automatically knows about them "
                    "and will be extra careful explaining those areas — you don't have to remind it."
                ),
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
        "tab": "🛠️ Study Tools",
        "summary": "Generate Study Roadmaps, Summaries, Cheat Sheets, Formula Sheets, Vocabulary Builders, visual Concept Maps, Daily Learning Goals, and view your AI Mistake Notebook.",
    },
    {
        "tab": "🗂️ Flashcards",
        "summary": "Generate and study flashcard decks with a Leitner-box system. Practice Forgotten cards separately, generate more cards anytime, or reset progress.",
    },
    {
        "tab": "⚡ Batch Gen",
        "summary": "Bulk-generate either a digital interactive quiz (for the Assessment tab) or a printable mock exam paper with a question sheet and answer key.",
    },
    {
        "tab": "📝 Assessment",
        "summary": "Take generated quizzes with real grading: exact-match for MCQs, AI partial-credit grading for short answers, and an optional negative marking toggle.",
    },
    {
        "tab": "📊 Progress",
        "summary": "Knowledge Heatmap, Weak Topic Detection, Study Streaks, Learning Analytics (time/accuracy/trends), and a Predicted Exam Readiness Score.",
    },
    {
        "tab": "📄 Viewer",
        "summary": "Browse and download every file generated for your active chapter — study guides, quizzes, flashcards, mock exams — and export text files to PDF.",
    },
]
