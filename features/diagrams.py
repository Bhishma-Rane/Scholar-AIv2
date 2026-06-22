"""
features/diagrams.py
======================
Generates the right kind of "diagram" for a topic:

- STRUCTURAL topics (processes, cycles, hierarchies, systems) get an
  LLM-generated Mermaid diagram. The diagram TYPE is chosen in a separate,
  forced first step rather than left to the main generation call — small
  local models default to "flowchart" almost every time when given an
  open choice in a single prompt, so we pick the type explicitly first.

- VISUAL topics (anatomy, real-world object appearance — e.g. "the human
  heart") cannot be represented by Mermaid at all. These are routed to a
  real labeled image instead (handled by core.diagram_router + the image
  search tool at the UI layer, since image search is a top-level tool,
  not something this module calls directly).
"""
import re

from core.llm import get_llm
from core.diagram_router import classify_diagram_topic

# Each Mermaid diagram type, with a one-line description used to help the
# LLM pick correctly, and a short syntax reminder used when generating.
DIAGRAM_TYPES = {
    "flowchart": {
        "description": "A process, decision tree, pipeline, or cause-and-effect chain with directional steps.",
        "syntax_hint": 'Start with "flowchart TD" (top-down) or "flowchart LR" (left-right). '
        'Nodes: A[Label] for a box, A{Label} for a decision, A(Label) for rounded. '
        'Edges: A --> B, or A -->|condition| B.',
    },
    "sequenceDiagram": {
        "description": "Interactions or messages exchanged between actors/components over time.",
        "syntax_hint": 'Start with "sequenceDiagram". Declare participants with "participant Name". '
        "Messages: Alice->>Bob: message text.",
    },
    "mindmap": {
        "description": "A central topic branching into sub-concepts and sub-sub-concepts (good for "
        "summarizing a chapter's structure or brainstorming related ideas).",
        "syntax_hint": 'Start with "mindmap". Root: root((Central Topic)). Indent child lines with 2 spaces '
        "per level to nest them.",
    },
    "classDiagram": {
        "description": "Entities/objects and the structural relationships or hierarchy between them "
        "(good for taxonomies, type hierarchies, or component relationships).",
        "syntax_hint": 'Start with "classDiagram". Define with "class Name" using a single bare word for '
        "Name (no quotes, no spaces, no braces -- e.g. \"class Heart\" not 'class \"Heart\" {'). "
        "To add a label, use a separate line: \"Name : description\". "
        "Relationships: ClassA --|> ClassB (inheritance), ClassA --> ClassB (association).",
    },
    "stateDiagram-v2": {
        "description": "A system or object that moves between distinct states, with transitions between them "
        "(good for state machines, lifecycle stages, or status flows).",
        "syntax_hint": 'Start with "stateDiagram-v2". States: [*] --> StateA. Transitions: StateA --> StateB : trigger.',
    },
}

VALID_DIAGRAM_STARTS = tuple(t.lower() for t in DIAGRAM_TYPES) + ("graph",)  # "graph" is a flowchart alias

# Keywords that sometimes get glued onto the end of a preceding token with
# no separating newline (e.g. "...stroke-width:1pxconcept ..." or
# "classDiagramclass \"Heart\" {"). Used to repair missing newlines anywhere
# in the output, including right after the very first diagram-type keyword.
_GLUE_KEYWORDS = (
    "flowchart", "graph", "sequenceDiagram", "mindmap", "classDiagram",
    "stateDiagram-v2", "stateDiagram", "style", "class", "click",
    "subgraph", "end", "concept", "participant", "note", "state",
)
_GLUE_PATTERN = re.compile(
    r"(?<=[a-zA-Z0-9#;%\}\)\]])(?=(?:" + "|".join(_GLUE_KEYWORDS) + r")\b)"
)


def _strip_code_fences(text: str) -> str:
    """LLMs sometimes wrap output in ```mermaid ... ``` even when told not to. Strip it if present."""
    text = text.strip()
    fence_match = re.match(r"^```(?:mermaid)?\s*\n(.*)\n```$", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    # Sometimes the closing fence is missing or there's trailing text after it.
    text = re.sub(r"^```(?:mermaid)?\s*\n?", "", text)
    text = re.sub(r"\n?```.*$", "", text, flags=re.DOTALL)
    return text.strip()


def _repair_glued_lines(mermaid_code: str) -> str:
    """
    Inserts a newline anywhere a known Mermaid keyword is glued directly
    onto the end of the previous token with no separator. This is the fix
    for errors like "stroke-width:1pxconcept ..." or
    "classDiagramclass \"Heart\" {" -- both are the same root cause: the
    LLM forgot a line break between two statements.
    """
    return _GLUE_PATTERN.sub("\n", mermaid_code)


_INLINE_STYLE_PATTERN = re.compile(
    r"\bstyle\s+\S+\s+[^\n]*?(?=\n|$)"
)


def _strip_stray_concept_lines(mermaid_code: str) -> str:
    """
    "concept ..." is not valid Mermaid syntax in ANY diagram type -- the
    model occasionally invents it as a pseudo-keyword (seen in mindmap
    output describing historical/conceptual topics). Drop such lines
    regardless of diagram type, since this isn't type-specific.
    """
    return re.sub(r"^\s*concept\b[^\n]*$", "", mermaid_code, flags=re.MULTILINE)


def _strip_invalid_style_lines(mermaid_code: str, diagram_type: str) -> str:
    """
    Removes style/class(css)/click directives entirely for diagram types
    that don't support them the way flowchart does. The model is told not
    to emit these at all, but small local models don't reliably comply --
    and sometimes a "style ..." directive ends up glued mid-line onto the
    end of an unrelated node label (e.g. "Alexander style A fill:#fff,...")
    rather than starting its own line, so a line-start check alone isn't
    enough; this also strips inline occurrences anywhere in the text.
    """
    if diagram_type not in ("mindmap", "sequenceDiagram", "stateDiagram-v2", "classDiagram"):
        return mermaid_code

    # Strip "style X <anything up to end of line>" wherever it appears,
    # even mid-line, since the glue can happen before the newline repair
    # fully separates it onto its own line.
    mermaid_code = _INLINE_STYLE_PATTERN.sub("", mermaid_code)

    lines = mermaid_code.splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if re.match(r"^click\s", stripped):
            continue
        if diagram_type != "classDiagram" and re.match(r"^class\s+\S+\s+\S+\s*;?\s*$", stripped):
            # CSS-class assignment form, e.g. "class A,B someCssClass;"
            continue
        kept.append(line)
    return "\n".join(kept)


def _fix_class_diagram_braces(mermaid_code: str) -> str:
    """
    llama3.2 frequently bleeds PlantUML/UML syntax into classDiagram output:
    quoted, brace-delimited blocks like:
        class "Heart" {
          + pumpBlood()
        }
    Mermaid's classDiagram does NOT support quoted names or brace blocks in
    this form. This rewrites them into valid Mermaid:
        class Heart
        Heart : +pumpBlood()
    """
    lines = mermaid_code.splitlines()
    out = []
    current_class = None
    for raw_line in lines:
        line = raw_line.strip()

        # "class "Name" {"  ->  start a class block, strip quotes/brace.
        block_open = re.match(r'^class\s+"?([A-Za-z0-9_]+)"?\s*\{\s*$', line)
        if block_open:
            current_class = block_open.group(1)
            out.append(f"class {current_class}")
            continue

        if line == "}" and current_class is not None:
            current_class = None
            continue

        if current_class is not None and line:
            # Member line inside a brace block, e.g. "+ pumpBlood()" or
            # "- chambers : int". Turn it into Mermaid's "ClassName : member".
            member = line.rstrip(";")
            out.append(f"{current_class} : {member}")
            continue

        # "class "Name"" (quoted, no brace) -> strip the quotes.
        simple_quoted = re.match(r'^class\s+"([A-Za-z0-9_ ]+)"\s*$', line)
        if simple_quoted:
            out.append(f"class {simple_quoted.group(1).replace(' ', '_')}")
            continue

        out.append(raw_line)

    return "\n".join(out)


def _sanitize_mermaid(mermaid_code: str, diagram_type: str) -> str:
    """
    Defensive cleanup applied to every LLM-generated diagram before it's
    handed to the renderer. Order matters: repair missing newlines first
    (so later line-based checks see correctly split lines), then fix
    classDiagram-specific brace/quote issues, then drop any remaining
    invalid style/class/click/concept directives.
    """
    mermaid_code = _repair_glued_lines(mermaid_code)
    mermaid_code = _strip_stray_concept_lines(mermaid_code)

    if diagram_type == "classDiagram":
        mermaid_code = _fix_class_diagram_braces(mermaid_code)
        # Braces/quotes can introduce new glue points; repair once more.
        mermaid_code = _repair_glued_lines(mermaid_code)

    mermaid_code = _strip_invalid_style_lines(mermaid_code, diagram_type)

    # Collapse 3+ blank lines (can happen after stripping) down to 1.
    mermaid_code = re.sub(r"\n{3,}", "\n\n", mermaid_code)

    return mermaid_code.strip()


def _choose_diagram_type(topic: str, llm) -> str:
    """
    Forces an explicit pick between the supported Mermaid diagram types,
    as a separate call from the actual generation. This is the fix for
    the "everything comes out as a flowchart" problem: asking the model
    to choose AND generate in one shot biases it toward the most common
    pattern (flowchart) regardless of fit.
    """
    options_text = "\n".join(f'- "{name}": {info["description"]}' for name, info in DIAGRAM_TYPES.items())
    prompt = f"""Pick the SINGLE best diagram type for this topic from the list below.
Topic: "{topic}"

Options:
{options_text}

Respond with ONLY the exact diagram type name from the list above, nothing else."""
    try:
        raw = llm.invoke(prompt).content.strip()
    except Exception:
        return "flowchart"

    raw_lower = raw.lower()
    for name in DIAGRAM_TYPES:
        if name.lower() in raw_lower:
            return name
    return "flowchart"  # safe fallback if the model's response didn't match any option


def generate_diagram_syntax(topic: str, context: str = "") -> dict:
    """
    Generates Mermaid syntax for a STRUCTURAL topic. Picks the diagram type
    explicitly first, then generates in that type.

    Returns:
        {"success": True, "mermaid_code": "...", "diagram_type": "mindmap"}
        or
        {"success": False, "error": "<reason>", "raw_output": "<what the LLM said>"}
    """
    llm = get_llm()
    if not llm:
        return {"success": False, "error": "LLM engine is offline.", "raw_output": ""}

    chosen_type = _choose_diagram_type(topic, llm)
    syntax_hint = DIAGRAM_TYPES[chosen_type]["syntax_hint"]

    system_prompt = f"""You are a diagram-generation engine for a study app.
Output ONLY valid Mermaid.js "{chosen_type}" syntax that visually explains the given topic.

RULES:
- Output ONLY the Mermaid code. No markdown code fences, no explanation, no preamble.
- You MUST use the "{chosen_type}" diagram type. {syntax_hint}
- Every statement goes on its own line. Never put two statements on the same line.
- CRITICAL: Never output `style`, `click`, CSS class assignments, quoted class names, or brace blocks ({{ }}). Mermaid does not use UML-style braces.
- Keep labels short and clear (a few words each).
- Keep the diagram focused: 5-12 nodes/steps is usually right. Do not pad with filler.
- Use double quotes around any label containing spaces or special characters (but never around class/entity names in classDiagram).
- Do not include comments or click/interaction syntax.
"""

    human_prompt = f"Topic: {topic}"
    if context.strip():
        human_prompt += f"\n\nRelevant source material (ground the diagram in this if applicable):\n{context[:4000]}"

    try:
        raw_output = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": human_prompt},
            ]
        ).content
    except Exception as e:
        return {"success": False, "error": f"LLM call failed: {str(e)}", "raw_output": ""}

    mermaid_code = _strip_code_fences(raw_output)
    mermaid_code = _sanitize_mermaid(mermaid_code, chosen_type)

    if not mermaid_code:
        return {"success": False, "error": "LLM returned an empty diagram.", "raw_output": raw_output}

    first_line = mermaid_code.splitlines()[0].strip().lower()
    if not any(first_line.startswith(start) for start in VALID_DIAGRAM_STARTS):
        return {
            "success": False,
            "error": "LLM output didn't start with a recognized Mermaid diagram type.",
            "raw_output": raw_output,
        }

    diagram_type = first_line.split()[0]
    return {"success": True, "mermaid_code": mermaid_code, "diagram_type": diagram_type}


def resolve_diagram_request(topic: str, context: str = "") -> dict:
    """
    Top-level entry point for the '!diagram <topic>' command. Classifies
    the topic, then either generates Mermaid syntax (structural) or signals
    that an image search should be performed instead (visual) — the actual
    image search call happens at the UI layer since it's a top-level tool,
    not something this features/ module has access to.

    Returns one of:
      {"render_as": "mermaid", "success": True/False, "mermaid_code": ..., ...}
      {"render_as": "image_search", "search_query": "<topic prepared for image search>"}
    """
    classification = classify_diagram_topic(topic)

    if classification == "visual":
        return {"render_as": "image_search", "search_query": f"{topic} labeled diagram educational"}

    result = generate_diagram_syntax(topic, context)
    result["render_as"] = "mermaid"
    return result