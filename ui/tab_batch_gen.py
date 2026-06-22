"""
ui/tab_batch_gen.py
=====================
The "Batch Gen" tab: bulk generation of either a digital interactive
quiz (JSON, for the Assessment tab) or a printable mock exam paper.
"""
import streamlit as st

from features.chat_graph import vedic_graph
from features.mock_exams import build_mock_exam_paper


def render_batch_gen_tab(username: str, active_subject: str, active_chapter: str, target_language: str):
    st.header("Automated Generation Suite")
    gen_mode = st.radio(
        "Select Mode:", ["Mixed Interactive Quiz (JSON)", "Custom Printable Mock Exam"], horizontal=True
    )

    if "Interactive" in gen_mode:
        num_qs = st.number_input("Number of Questions", min_value=5, max_value=50, value=5, step=5)
        if st.button("🚀 Generate Digital Quiz", type="primary"):
            if active_chapter == "Select Chapter":
                st.error("Select Chapter.")
            else:
                with st.status("Building Perfect JSON Exam...", expanded=True) as status:
                    res = vedic_graph.invoke(
                        {
                            "question": f"ver {active_chapter} ! quiz {num_qs}",
                            "chat_history": st.session_state.session_history,
                            "context": "",
                            "relevance_score": 0.0,
                            "source_file": "Unknown",
                            "command_mode": "standard",
                            "quiz_count": num_qs,
                            "response": "",
                            "active_chapter": active_chapter,
                            "data_source": "Local Docs",
                            "language": target_language,
                            "username": username,
                            "subject": active_subject,
                        }
                    )
                    status.update(label="Complete!", state="complete", expanded=False)
                st.success(res["response"]) if "Failed" not in res["response"] else st.error(res["response"])
    else:
        mcq_count, short_count = st.columns(2)
        m = mcq_count.number_input("MCQs", 0, value=10)
        s = short_count.number_input("Short", 0, value=5)
        if st.button("📝 Compile Mock Paper", type="primary"):
            if active_chapter == "Select Chapter":
                st.error("Select Chapter.")
            else:
                with st.status("Assembling...", expanded=True) as status:
                    build_mock_exam_paper(
                        username,
                        active_subject,
                        active_chapter,
                        {"total_marks": 100, "order": ["MCQs", "Short Answer"], "mcq_count": m, "short_count": s},
                    )
                    status.update(label="Compiled!", state="complete", expanded=False)
                    st.success("Export as PDF from the Document Viewer tab.")
