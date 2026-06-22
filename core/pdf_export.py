"""
core/pdf_export.py
===================
Plain-text -> PDF export, used by the Document Viewer tab to let users
download study guides / mock exams as PDFs.
"""
try:
    from fpdf import FPDF
except ImportError:
    FPDF = None


def create_pdf_from_text(text_data: str, filename: str) -> str:
    """
    Renders plain text into a simple formatted PDF.
    Section headers (lines containing '===', 'EXAM:', or 'ANSWER KEY:')
    are bolded and colored for visual structure.

    Returns the output PDF path on success, or an "Error"/"Failure" string
    on failure (callers should check for those substrings).
    """
    if not FPDF:
        return "Error: FPDF not installed. Run 'pip install fpdf2'."

    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        lines = text_data.split("\n")
        for line in lines:
            line = line.replace("**", "").replace("### ", "").replace("## ", "").replace("# ", "")
            # Fix Issue #20: Handle non-Latin encoding gracefully.
            safe_text = line.encode("latin-1", "replace").decode("latin-1")

            if "===" in safe_text or "EXAM:" in safe_text or "ANSWER KEY:" in safe_text:
                pdf.set_font("Arial", "B", size=14)
                pdf.set_text_color(0, 51, 102)
                pdf.multi_cell(0, 10, txt=safe_text)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.set_font("Arial", size=11)
                pdf.multi_cell(0, 7, txt=safe_text)

        pdf_path = filename.replace(".txt", ".pdf").replace(".json", ".pdf")
        pdf.output(pdf_path)
        return pdf_path
    except Exception as e:
        return (
            f"PDF Engine Failure: {str(e)}\n"
            "Hint: If exporting Hindi/Sanskrit, complex font mapping is required locally."
        )
