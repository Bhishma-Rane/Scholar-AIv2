"""
ui/tutorial_overlay.py
=========================
A spotlight-style guided tour overlay: dims the whole screen except a
highlighted cutout around the step's target element, with a tooltip box
showing the instruction + Next/Back/Skip controls. Replaces the old
plain step-card tutorial (ui/tab_tutorial.py's _render_pathway_walkthrough)
with something that actually points at real UI elements instead of just
describing them in text.

HOW TARGETING WORKS:
Each step specifies a CSS selector for the real element to highlight
(e.g. a tab button, a sidebar input). The component finds that element
in the DOM via JS, reads its on-screen position/size with
getBoundingClientRect(), and draws the spotlight cutout + tooltip
positioned relative to it.

LIMITATION: this overlay lives in an iframe (components.html always
sandboxes its content), so it cannot directly see elements in the
parent Streamlit page through normal means -- but Streamlit's component
iframes ARE same-origin with the parent page, so `window.parent.document`
is reachable. That's what makes targeting real elements possible at all.

NAVIGATION: the actual Next/Back/Skip controls are plain Streamlit
buttons rendered OUTSIDE this component (in tab_tutorial.py, right below
the call to render_tutorial_overlay()) -- a components.html iframe can't
host clickable Streamlit widgets itself, so this component is visual-only
and the real interaction happens via normal st.button calls alongside it.

USAGE (see ui/tab_tutorial.py for the full integration):
    render_tutorial_overlay(TUTORIAL_STEPS, step_index)
    # ...then separately, plain st.button("Next"), st.button("Back"), etc.
"""
import streamlit as st

# Each step: a target selector (matched against the PARENT document,
# i.e. the real rendered Streamlit app), a title, and body text.
# `selector` should be as stable as possible -- data-testid attributes
# survive Streamlit version bumps better than auto-generated class names.
TUTORIAL_STEPS = [
    {
        "selector": "[data-testid='stSidebar']",
        "title": "Your Workspace",
        "body": "This is your sidebar — create subjects, upload chapters, and switch between them here.",
    },
    {
        "selector": "button[data-baseweb='tab']:nth-of-type(1)",
        "title": "Dashboard",
        "body": "Your AI coach reads your real quiz history here and tells you what to focus on next.",
    },
    {
        "selector": "button[data-baseweb='tab']:nth-of-type(2)",
        "title": "Tutor",
        "body": "Chat with an AI tutor about your uploaded chapter — ask questions, get explanations.",
    },
    {
        "selector": "button[data-baseweb='tab']:nth-of-type(3)",
        "title": "Study",
        "body": "Generate study aids, drill flashcards, and review your chapter's source PDF, all in one place.",
    },
    {
        "selector": "button[data-baseweb='tab']:nth-of-type(4)",
        "title": "Practice & Exams",
        "body": "Generate and take quizzes, or build a full Question Paper with sections and a timer.",
    },
]


def render_tutorial_overlay(steps: list, step_index: int):
    """
    Renders the spotlight overlay for the given step. Visual only --
    pair this with real st.button calls right after for Next/Back/Skip.
    """
    if step_index < 0 or step_index >= len(steps):
        return

    step = steps[step_index]
    selector_js = step["selector"].replace("\\", "\\\\").replace("'", "\\'")
    title_js = step["title"].replace("\\", "\\\\").replace("'", "\\'")
    body_js = step["body"].replace("\\", "\\\\").replace("'", "\\'")

    html = f"""
    <div id="tutorial-root"></div>
    <style>
        #tutorial-spotlight {{
            position: fixed;
            border-radius: 8px;
            box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.65);
            transition: all 0.3s ease;
            pointer-events: none;
            z-index: 999998;
        }}
        #tutorial-tooltip {{
            position: fixed;
            background: white;
            border-radius: 10px;
            padding: 16px 20px;
            max-width: 320px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.3);
            z-index: 999999;
            font-family: sans-serif;
        }}
        #tutorial-tooltip h4 {{
            margin: 0 0 8px 0;
            color: #5a691d;
            font-size: 16px;
        }}
        #tutorial-tooltip p {{
            margin: 0;
            color: #333;
            font-size: 14px;
            line-height: 1.5;
        }}
        #tutorial-step-count {{
            margin-top: 10px;
            font-size: 12px;
            color: #888;
        }}
    </style>
    <script>
    (function() {{
        function renderTooltipOnly() {{
            const parentDoc = window.parent.document;
            const tooltip = parentDoc.createElement('div');
            tooltip.id = 'tutorial-tooltip';
            tooltip.style.top = '40%';
            tooltip.style.left = '50%';
            tooltip.style.transform = 'translate(-50%, -50%)';
            tooltip.innerHTML = `<h4>{title_js}</h4><p>{body_js}</p>`;
            const existing = parentDoc.getElementById('tutorial-tooltip');
            if (existing) existing.remove();
            parentDoc.body.appendChild(tooltip);
        }}

        try {{
            const parentDoc = window.parent.document;
            const target = parentDoc.querySelector('{selector_js}');

            ['tutorial-spotlight', 'tutorial-tooltip'].forEach(id => {{
                const existing = parentDoc.getElementById(id);
                if (existing) existing.remove();
            }});

            if (!target) {{
                renderTooltipOnly();
                return;
            }}

            const rect = target.getBoundingClientRect();
            const padding = 8;

            const spotlight = parentDoc.createElement('div');
            spotlight.id = 'tutorial-spotlight';
            spotlight.style.top = (rect.top - padding) + 'px';
            spotlight.style.left = (rect.left - padding) + 'px';
            spotlight.style.width = (rect.width + padding * 2) + 'px';
            spotlight.style.height = (rect.height + padding * 2) + 'px';

            const tooltip = parentDoc.createElement('div');
            tooltip.id = 'tutorial-tooltip';
            tooltip.innerHTML = `<h4>{title_js}</h4><p>{body_js}</p>
                <div id="tutorial-step-count">Step {step_index + 1} of {len(steps)}</div>`;

            const tooltipTop = (rect.bottom + 16 + 150 < window.innerHeight)
                ? rect.bottom + 16
                : Math.max(16, rect.top - 166);
            tooltip.style.top = tooltipTop + 'px';
            tooltip.style.left = Math.min(rect.left, window.innerWidth - 340) + 'px';

            parentDoc.body.appendChild(spotlight);
            parentDoc.body.appendChild(tooltip);

            target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        }} catch (e) {{
            console.warn('Tutorial overlay could not render:', e);
        }}
    }})();
    </script>
    """
    st.iframe(html, height=1, width=1)


def cleanup_tutorial_overlay():
    """
    Call this once when the tour ends (skip or finish) to remove any
    leftover spotlight/tooltip nodes from the parent document.
    """
    html = """
    <script>
    try {
        const parentDoc = window.parent.document;
        ['tutorial-spotlight', 'tutorial-tooltip'].forEach(id => {
            const el = parentDoc.getElementById(id);
            if (el) el.remove();
        });
    } catch (e) {}
    </script>
    """
    st.iframe(html, height=1, width=1)
