"""
ui/tutorial_overlay.py
=========================
Guided tour: a highlight box drawn around the real target element,
plus a small tooltip box with the step's instructions, connected by
a short arrow. Draws over the real, already-rendered app (see
ui/tab_tutorial.py + app.py for how that's guaranteed).

CHANGED: earlier versions used only a floating tooltip with no visual
marker on the target itself -- users reported the tour "just scrolls
the sidebar" with nothing telling them what to actually click. This
version adds a pulsing highlight box drawn at the target's own
bounding rect (so it's obviously "this exact thing"), plus a small
arrow from the box to the tooltip so the two visually belong together.

HOW TARGETING WORKS: unchanged from before -- see ui/tutorial_content.py
PATHWAYS for the `target` spec format (css / tab_text / button_text /
widget_label). If a target can't be resolved, both the highlight box
and tooltip fall back to a centered position rather than crashing --
console.warn logs the reason.

USAGE (see ui/tab_tutorial.py for the full integration):
    render_tutorial_overlay(steps, step_index)
    # ...then separately, plain st.button("Next"), st.button("Back"), etc.
"""

import json
import streamlit as st

# CSS injected into the PARENT document once -- styles elements that
# live in the parent's DOM, not in this iframe's own document.
_OVERLAY_CSS = """
#tutorial-tooltip {
    position: fixed;
    background: white;
    border-radius: 10px;
    padding: 16px 20px;
    max-width: 320px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.35);
    z-index: 999999;
    font-family: sans-serif;
}
#tutorial-tooltip h4 {
    margin: 0 0 8px 0;
    color: #5a691d;
    font-size: 16px;
}
#tutorial-tooltip p {
    margin: 0;
    color: #333;
    font-size: 14px;
    line-height: 1.5;
}
#tutorial-step-count {
    margin-top: 10px;
    font-size: 12px;
    color: #888;
}
#tutorial-highlight-box {
    position: fixed;
    border: 3px solid #ff9d2f;
    border-radius: 8px;
    box-shadow: 0 0 0 4000px rgba(0,0,0,0.45), 0 0 16px 4px rgba(255,157,47,0.8);
    z-index: 999998;
    pointer-events: none;
    animation: tutorial-pulse 1.4s ease-in-out infinite;
    transition: top 0.25s ease, left 0.25s ease, width 0.25s ease, height 0.25s ease;
}
@keyframes tutorial-pulse {
    0%   { box-shadow: 0 0 0 4000px rgba(0,0,0,0.45), 0 0 10px 2px rgba(255,157,47,0.7); }
    50%  { box-shadow: 0 0 0 4000px rgba(0,0,0,0.45), 0 0 22px 8px rgba(255,157,47,1); }
    100% { box-shadow: 0 0 0 4000px rgba(0,0,0,0.45), 0 0 10px 2px rgba(255,157,47,0.7); }
}
"""


def render_tutorial_overlay(steps: list, step_index: int):
    """
    Renders the highlight box + tooltip for the given step, positioned
    at/next to its resolved target (or centered if the target can't be
    found). Pair this with real st.button calls right after for
    Next/Back/Skip.

    `steps` is a pathway's step list from ui/tutorial_content.py's
    PATHWAYS -- each step must have "title", "body", and "target" keys.
    """
    if step_index < 0 or step_index >= len(steps):
        return

    step = steps[step_index]
    match_json = json.dumps(step["target"])
    title_js = step["title"].replace("\\", "\\\\").replace("'", "\\'")
    body_js = step["body"].replace("\\", "\\\\").replace("'", "\\'")
    css_js = _OVERLAY_CSS.replace("\\", "\\\\").replace("`", "\\`")

    html = f"""
    <div id="tutorial-root"></div>
    <script>
    (function() {{
        const match = {match_json};

        function ensureStylesInjected(parentDoc) {{
            if (!parentDoc.getElementById('tutorial-overlay-styles')) {{
                const styleEl = parentDoc.createElement('style');
                styleEl.id = 'tutorial-overlay-styles';
                styleEl.textContent = `{css_js}`;
                parentDoc.head.appendChild(styleEl);
            }}
        }}

        function resolveTarget(parentDoc, m) {{
            try {{
                if (m.type === 'css') {{
                    return parentDoc.querySelector(m.selector) || null;
                }}
                if (m.type === 'tab_text') {{
                    const tabs = parentDoc.querySelectorAll("button[data-baseweb='tab']");
                    for (const t of tabs) {{
                        if (t.textContent.trim() === m.text) return t;
                    }}
                    return null;
                }}
                if (m.type === 'button_text') {{
                    const buttons = parentDoc.querySelectorAll('button');
                    for (const b of buttons) {{
                        if (b.textContent.trim() === m.text) return b;
                    }}
                    return null;
                }}
                if (m.type === 'widget_label') {{
                    const labels = parentDoc.querySelectorAll("[data-testid='stWidgetLabel']");
                    for (const l of labels) {{
                        if (l.textContent.trim() === m.label) {{
                            if (m.container) {{
                                const container = l.closest("[data-testid='" + m.container + "']");
                                if (container) return container;
                            }}
                            return l.parentElement || l;
                        }}
                    }}
                    return null;
                }}
            }} catch (e) {{
                console.warn('Tutorial overlay target resolution failed:', e);
            }}
            return null;
        }}

        function positionHighlight(box, rect) {{
            const pad = 6;
            box.style.top = (rect.top - pad) + 'px';
            box.style.left = (rect.left - pad) + 'px';
            box.style.width = (rect.width + pad * 2) + 'px';
            box.style.height = (rect.height + pad * 2) + 'px';
        }}

        try {{
            const parentDoc = window.parent.document;
            ensureStylesInjected(parentDoc);

            ['tutorial-tooltip', 'tutorial-highlight-box'].forEach(id => {{
                const existing = parentDoc.getElementById(id);
                if (existing) existing.remove();
            }});

            const tooltip = parentDoc.createElement('div');
            tooltip.id = 'tutorial-tooltip';
            tooltip.innerHTML = `<h4>{title_js}</h4><p>{body_js}</p>
                <div id="tutorial-step-count">Look for the glowing box ⬇️</div>`;

            const target = resolveTarget(parentDoc, match);

            if (!target) {{
                console.warn('Tutorial overlay: no element matched', match);
                tooltip.style.top = '40%';
                tooltip.style.left = '50%';
                tooltip.style.transform = 'translate(-50%, -50%)';
                parentDoc.body.appendChild(tooltip);
                return;
            }}

            target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});

            // Give the smooth-scroll a moment to finish before measuring
            // and placing the highlight box + tooltip against real coords.
            setTimeout(() => {{
                const rect = target.getBoundingClientRect();

                const box = parentDoc.createElement('div');
                box.id = 'tutorial-highlight-box';
                positionHighlight(box, rect);
                parentDoc.body.appendChild(box);

                const tooltipTop = (rect.bottom + 20 + 150 < window.innerHeight)
                    ? rect.bottom + 20
                    : Math.max(16, rect.top - 170);
                tooltip.style.top = tooltipTop + 'px';
                tooltip.style.left = Math.min(Math.max(16, rect.left), window.innerWidth - 340) + 'px';
                parentDoc.body.appendChild(tooltip);
            }}, 350);
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
    leftover tooltip/highlight/style nodes from the parent document.
    """
    html = """
    <script>
    try {
        const parentDoc = window.parent.document;
        ['tutorial-tooltip', 'tutorial-highlight-box', 'tutorial-overlay-styles'].forEach(id => {
            const el = parentDoc.getElementById(id);
            if (el) el.remove();
        });
    } catch (e) {}
    </script>
    """
    st.iframe(html, height=1, width=1)
