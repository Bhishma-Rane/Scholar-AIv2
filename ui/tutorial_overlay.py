"""
ui/tutorial_overlay.py
=========================
A simple tooltip-box guided tour: no screen dimming, no spotlight
cutout -- just a small box with the step's instructions, positioned
right next to the real widget it's talking about. Draws over the real,
already-rendered app (see ui/tab_tutorial.py + app.py for how that's
guaranteed).

(Earlier versions of this file used a dimmed spotlight overlay. That
was dropped by request -- a plain positioned tooltip is simpler and
avoids an entire class of "the highlight box doesn't move / covers the
wrong thing" bugs, since there's only one visual element to keep in
sync with the target instead of two.)

HOW TARGETING WORKS:
Each step (defined in ui/tutorial_content.py, inside PATHWAYS) carries a
`target` dict describing HOW to find the real element, by its actual
visible label/text rather than a guessed CSS selector or sibling index
(both of which break silently the moment Streamlit's internal markup
shifts, or another element appears earlier in the DOM). Supported types:

  - {"type": "css", "selector": "..."}
        A single, genuinely unique element. querySelector directly.
        Used for things like the sidebar container itself.

  - {"type": "tab_text", "text": "💬 Tutor"}
        A tab button, matched by its exact visible label among all
        `button[data-baseweb='tab']` elements.

  - {"type": "widget_label", "label": "Active Chapter", "container": "stSelectbox"}
        An input/selectbox/uploader, found by its label text among
        `[data-testid='stWidgetLabel']` elements, then widened to the
        nearest ancestor matching the given container testid (e.g.
        stTextInput, stSelectbox, stFileUploader, stNumberInput) so the
        tooltip anchors to the whole widget, not just the (possibly
        visually hidden) label text itself.

  - {"type": "button_text", "text": "Create"}
        A plain button, matched by its exact visible text.

If a target can't be resolved (label wording drifted, element not yet
rendered, etc.) the tooltip falls back to a centered position rather
than crashing -- console.warn logs the reason.

LIMITATION: this overlay lives in an iframe (st.iframe always sandboxes
its content), so it cannot directly see elements in the parent
Streamlit page through normal means -- but Streamlit's component
iframes ARE same-origin with the parent page, so `window.parent.document`
is reachable. That's what makes targeting real elements possible at all.

IMPORTANT: the CSS for #tutorial-tooltip is injected into the PARENT
document (once, guarded by an id check) rather than left in this
iframe's own <style> block -- the tooltip div is created via
`parentDoc.createElement(...)` and appended to `parentDoc.body`, so it
lives in the parent page's DOM, and a stylesheet scoped to this
iframe's own srcdoc never applies to it.

NAVIGATION: the actual Next/Back/Skip controls are plain Streamlit
buttons rendered OUTSIDE this component (in tab_tutorial.py, right below
the call to render_tutorial_overlay()) -- an iframe can't host clickable
Streamlit widgets itself, so this component is visual-only and the real
interaction happens via normal st.button calls alongside it.

USAGE (see ui/tab_tutorial.py for the full integration):
    render_tutorial_overlay(steps, step_index)
    # ...then separately, plain st.button("Next"), st.button("Back"), etc.
"""
import json

import streamlit as st

# CSS for the tooltip box. Injected into the PARENT document once -- it
# styles an element that lives in the parent's DOM, not in this
# iframe's own document.
_OVERLAY_CSS = """
#tutorial-tooltip {
    position: fixed;
    background: white;
    border-radius: 10px;
    padding: 16px 20px;
    max-width: 320px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.3);
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
"""


def render_tutorial_overlay(steps: list, step_index: int):
    """
    Renders the tooltip box for the given step, positioned next to its
    resolved target (or centered if the target can't be found). Pair
    this with real st.button calls right after for Next/Back/Skip.

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

        try {{
            const parentDoc = window.parent.document;

            ensureStylesInjected(parentDoc);

            const existing = parentDoc.getElementById('tutorial-tooltip');
            if (existing) existing.remove();

            const tooltip = parentDoc.createElement('div');
            tooltip.id = 'tutorial-tooltip';
            tooltip.innerHTML = `<h4>{title_js}</h4><p>{body_js}</p>
                <div id="tutorial-step-count">Step {step_index + 1} of {len(steps)}</div>`;

            const target = resolveTarget(parentDoc, match);

            if (!target) {{
                console.warn('Tutorial overlay: no element matched', match);
                tooltip.style.top = '40%';
                tooltip.style.left = '50%';
                tooltip.style.transform = 'translate(-50%, -50%)';
                parentDoc.body.appendChild(tooltip);
                return;
            }}

            const rect = target.getBoundingClientRect();

            const tooltipTop = (rect.bottom + 16 + 150 < window.innerHeight)
                ? rect.bottom + 16
                : Math.max(16, rect.top - 166);
            tooltip.style.top = tooltipTop + 'px';
            tooltip.style.left = Math.min(rect.left, window.innerWidth - 340) + 'px';

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
    leftover tooltip/style nodes from the parent document.
    """
    html = """
    <script>
    try {
        const parentDoc = window.parent.document;
        ['tutorial-tooltip', 'tutorial-overlay-styles'].forEach(id => {
            const el = parentDoc.getElementById(id);
            if (el) el.remove();
        });
    } catch (e) {}
    </script>
    """
    st.iframe(html, height=1, width=1)
