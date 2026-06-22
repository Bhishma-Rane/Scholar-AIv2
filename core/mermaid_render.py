"""
core/mermaid_render.py
========================
Renders Mermaid.js diagram syntax inline in Streamlit via a small HTML
component using the Mermaid CDN. This is more robust across Streamlit
versions than relying on native markdown mermaid fences, and lets us
control sizing/error display directly.
"""
import html as html_lib

import streamlit.components.v1 as components

MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"


def render_mermaid(mermaid_code: str, height: int = 420):
    """
    Renders a Mermaid diagram block inline. If Mermaid.js itself fails to
    parse the code (e.g. the LLM produced subtly invalid syntax), it shows
    a visible error in the component rather than a blank space, so the
    failure is obvious to the user instead of silently disappearing.
    """
    # Escape for safe embedding inside the JS template literal / HTML.
    escaped_code = html_lib.escape(mermaid_code, quote=True)

    component_html = f"""
    <div id="mermaid-container" style="width:100%; overflow:auto; background:white; border-radius:10px; padding:16px;">
        <pre class="mermaid">{escaped_code}</pre>
        <div id="mermaid-error" style="display:none; color:#b00020; font-family:monospace; white-space:pre-wrap; margin-top:10px;"></div>
    </div>
    <script type="module">
        import mermaid from '{MERMAID_CDN_URL}';
        mermaid.initialize({{ startOnLoad: false, theme: 'default', securityLevel: 'strict' }});
        try {{
            await mermaid.run({{ querySelector: '.mermaid' }});
        }} catch (err) {{
            document.querySelector('.mermaid').style.display = 'none';
            const errBox = document.getElementById('mermaid-error');
            errBox.style.display = 'block';
            errBox.innerText = 'Diagram failed to render: ' + err.message;
        }}
    </script>
    """
    components.html(component_html, height=height, scrolling=True)
