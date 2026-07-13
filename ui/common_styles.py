"""
ui/common_styles.py
=====================
Shared CSS overrides for quiz-style screens (Assessment, Practice,
Question Paper). Streamlit's default widget sizing puts question text
(st.subheader / bold st.markdown) and answer-choice widgets (st.radio,
st.text_area, st.text_input) at mismatched sizes -- questions render
too large or too small relative to their choices depending on which
widget was used to build the screen. This module is the single place
that rebalances both, so any tab with a question-and-choices screen
gets the same, consistent sizing instead of each tab hand-rolling (or
forgetting to hand-roll) its own CSS block.

Usage: call inject_quiz_css() once near the top of a tab's
question-screen render function. It's safe to call on every render --
st.markdown with a <style> block is idempotent, it just re-declares the
same rules.

If you render the question stem via st.subheader(), this covers it
automatically. If you render it via st.markdown() (e.g. bold text),
wrap it in <p class="quiz-question-text">...</p> so the override
applies -- see QUESTION_TEXT_CLASS below.
"""
import streamlit as st

QUESTION_TEXT_CLASS = "quiz-question-text"

_QUIZ_CSS = f"""
<style>
/* Question text rendered via st.subheader() */
div[data-testid="stSubheader"] p,
div[data-testid="stSubheader"],
div[data-testid="stSubheader"] h3 {{
    font-size: 1.25rem !important;
    line-height: 1.5 !important;
    font-weight: 400 !important;
}}

/* Question text rendered via st.markdown() and wrapped in this class */
.{QUESTION_TEXT_CLASS} {{
    font-size: 1.25rem !important;
    font-weight: 600;
    line-height: 1.5 !important;
    margin-bottom: 0.5rem;
}}

/* Radio button option labels (MCQ / assertion-reason choices) */
div[data-testid="stRadio"] label p {{
    font-size: 1.15rem !important;
    line-height: 1.5 !important;
}}

/* Text area answers (subjective / short-answer / long-answer) */
div[data-testid="stTextArea"] textarea {{
    font-size: 1.15rem !important;
}}

/* Text input answers (fill-in-the-blank) */
div[data-testid="stTextInput"] input {{
    font-size: 1.15rem !important;
}}
</style>
"""


def inject_quiz_css():
    """Applies consistent question/choice font sizing. Call once near the
    top of any question-screen render function."""
    st.markdown(_QUIZ_CSS, unsafe_allow_html=True)
    
