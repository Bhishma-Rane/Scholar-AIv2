"""
core/pdf_export.py
===================
Plain-text -> PDF export, used by the Document Viewer tab to let users
download study guides / mock exams as PDFs.

Supports mixed English + Hindi/Sanskrit (Devanagari) text via Unicode TTF
fonts with fpdf2's automatic per-character fallback, and explicitly resets
the cursor to the left margin after each multi_cell (fpdf2's default
new_x=XPos.RIGHT does NOT do this, which is what actually caused the
"Not enough horizontal space to render a single character" crash: the next
w=0 multi_cell computed its width from wherever the cursor was left, not
from the left margin).
"""
import os

try:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
except ImportError:
    FPDF = None

# core/ -> project root -> assets/fonts
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

LATIN_REGULAR = os.path.join(FONT_DIR, "NotoSans-Regular.ttf")
LATIN_BOLD = os.path.join(FONT_DIR, "NotoSans-Bold.ttf")
DEVANAGARI_REGULAR = os.path.join(FONT_DIR, "NotoSansDevanagari-Regular.ttf")
DEVANAGARI_BOLD = os.path.join(FONT_DIR, "NotoSansDevanagari-Bold.ttf")


def create_pdf_from_text(text_data: str, filename: str) -> str:
    """
    Renders plain text into a simple formatted PDF.
    Section headers (lines containing '===', 'EXAM:', or 'ANSWER KEY:')
    are bolded and colored for visual structure.

    Supports mixed English + Devanagari (Hindi/Sanskrit) text: Latin
    characters render in NotoSans, and any character NotoSans can't
    render (Devanagari, etc.) automatically falls back to
    NotoSansDevanagari, glyph by glyph.

    Returns the output PDF path on success, or an "Error"/"Failure" string
    on failure (callers should check for those substrings).
    """
    if not FPDF:
        return "Error: FPDF not installed. Run 'pip install fpdf2'."

    missing = [p for p in (LATIN_REGULAR, LATIN_BOLD, DEVANAGARI_REGULAR, DEVANAGARI_BOLD) if not os.path.exists(p)]
    if missing:
        return (
            "Error: Missing font file(s) for PDF export: "
            + ", ".join(missing)
            + ". Place NotoSans-Regular.ttf, NotoSans-Bold.ttf, "
              "NotoSansDevanagari-Regular.ttf, and NotoSansDevanagari-Bold.ttf "
              "in assets/fonts/."
        )

    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Register Unicode fonts (Latin + Devanagari) instead of the
        # old core "Arial" font, which only supports latin-1 and forced
        # every Devanagari character into a garbled '?' run.
        pdf.add_font("NotoSans", "", LATIN_REGULAR)
        pdf.add_font("NotoSans", "B", LATIN_BOLD)
        pdf.add_font("NotoSansDevanagari", "", DEVANAGARI_REGULAR)
        pdf.add_font("NotoSansDevanagari", "B", DEVANAGARI_BOLD)
        pdf.set_fallback_fonts(["NotoSansDevanagari"])

        lines = text_data.split("\n")
        for line in lines:
            line = line.replace("**", "").replace("### ", "").replace("## ", "").replace("# ", "")

            if "===" in line or "EXAM:" in line or "ANSWER KEY:" in line:
                pdf.set_font("NotoSans", "B", size=14)
                pdf.set_text_color(0, 51, 102)
                # new_x/new_y explicitly reset the cursor to the left
                # margin after this cell -- this is the actual fix for
                # the "Not enough horizontal space" crash.
                pdf.multi_cell(0, 10, text=line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.set_font("NotoSans", size=11)
                pdf.multi_cell(0, 7, text=line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf_path = filename.replace(".txt", ".pdf").replace(".json", ".pdf")
        pdf.output(pdf_path)
        return pdf_path
    except Exception as e:
        return f"PDF Engine Failure: {str(e)}"
