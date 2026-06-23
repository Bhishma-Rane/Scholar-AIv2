"""
core/vectorstore.py
====================
Per-subject Chroma vector store management, plus raw chapter text retrieval
(used by features that need the full, exact text rather than embedded chunks).
"""
import os
import shutil

from langchain_community.vectorstores import Chroma
from langchain_ollama import OllamaEmbeddings
from langchain_community.document_loaders import DirectoryLoader, TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import OLLAMA_EMBED_MODEL, OLLAMA_BASE_URL
from core.paths import get_user_paths, sanitize_filename


def get_vector_store(username: str, subject: str, force_rebuild: bool = False):
    """
    Fix Issue #16, #17, #18, #19: Scoped Vector Store & Rebuilding.

    Loads (or builds) a Chroma vector store scoped to a single user+subject.
    Returns None if there are no source documents to embed.
    """
    paths = get_user_paths(username, subject)
    chroma_db_dir = paths["chroma"]
    embeddings = OllamaEmbeddings(
    model=OLLAMA_EMBED_MODEL,
    base_url=OLLAMA_BASE_URL,
    client_kwargs={"headers": {"ngrok-skip-browser-warning": "true"}},
    )

    if force_rebuild and os.path.exists(chroma_db_dir):
        shutil.rmtree(chroma_db_dir)

    if not force_rebuild and os.path.exists(chroma_db_dir) and os.listdir(chroma_db_dir):
        return Chroma(persist_directory=chroma_db_dir, embedding_function=embeddings)

    loader_pdf = DirectoryLoader(paths["subject_source"], glob="**/*.pdf", loader_cls=PyPDFLoader)
    loader_txt = DirectoryLoader(paths["subject_source"], glob="**/*.txt", loader_cls=TextLoader)

    try:
        documents = loader_pdf.load() + loader_txt.load()
    except Exception:
        documents = []

    if not documents:
        return None

    chunks, metadatas = [], []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200, separators=["\n\n", "\n", ".", " ", ""]
    )

    for doc in documents:
        clean_chapter_name = sanitize_filename(
            os.path.basename(doc.metadata.get("source", "Unknown File")).rsplit(".", 1)[0]
        )
        doc_chunks = text_splitter.split_text(doc.page_content)
        chunks.extend(doc_chunks)
        metadatas.extend(
            [{"chapter": clean_chapter_name, "source": doc.metadata.get("source", "Unknown")}] * len(doc_chunks)
        )

    if chunks:
        return Chroma.from_texts(
            texts=chunks, embedding=embeddings, metadatas=metadatas, persist_directory=chroma_db_dir
        )
    return None


def get_chapter_text(username: str, subject: str, chapter: str):
    """
    Returns the exact, full text of a chapter's source file (txt or pdf),
    rather than retrieving embedded chunks. Used wherever a feature needs
    the complete chapter content (e.g. flashcard/quiz generation).
    """
    paths = get_user_paths(username, subject)
    matched = [
        os.path.join(paths["subject_source"], f)
        for f in os.listdir(paths["subject_source"])
        if sanitize_filename(chapter).lower() in sanitize_filename(f).lower() and f.endswith((".txt", ".pdf"))
    ]
    if not matched:
        return None

    # Fix Issue #28: File descriptor leak (use context manager).
    if matched[0].lower().endswith(".txt"):
        with open(matched[0], "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    else:
        return "\n\n".join(page.page_content for page in PyPDFLoader(matched[0]).load())
