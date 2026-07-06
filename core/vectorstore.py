"""
core/vectorstore.py
====================
Per-subject Chroma vector store management, plus raw chapter text retrieval
(used by features that need the full, exact text rather than embedded chunks).

CHANGED (this revision): embeddings now route through storage_bridge.py's
/ollama/embed route instead of calling Ollama directly via
langchain_ollama.OllamaEmbeddings. The old direct path
(OllamaEmbeddings(base_url=OLLAMA_BASE_URL) -> .../ollama/api/embed
through the tunnel) was a leftover bypass: path_proxy.py's "/ollama"
route was removed specifically to force all AI calls through the
bridge's subscription checks (see path_proxy.py's change log), but this
file was never updated to match -- it kept hitting the now-deleted
route, which is why get_vector_store(force_rebuild=True) started
404ing with "No route configured for path: /ollama/api/embed".

BridgeEmbeddings below is the embeddings-side counterpart to
core/llm.py's BridgeChatLLM, using core/llm.py's embed_texts() (which
itself calls bridge_client.ollama_embed()) instead of talking to
Ollama directly.

CHANGED (earlier revision): source PDFs/TXT files no longer live in a
persistent local folder (paths["subject_source"] no longer exists —
see core/paths.py). They live on the storage bridge now. Both
functions in this file fetch the relevant file(s) from the bridge into
a TEMPORARY scratch directory first, then use the existing
PyPDFLoader/TextLoader exactly as before — those loader classes need
real file paths, so this is the minimal change that keeps everything
downstream (chunking, embeddings, Chroma) untouched.

The temp scratch directory is cleaned up after use. ChromaDB itself
(paths["chroma"]) is still rebuilt and cached locally on Streamlit
Cloud's container disk — that part is unchanged. It just gets rebuilt
from bridge-fetched files instead of a local "sources" folder.

FIX (readonly database on force_rebuild): chromadb caches a process-level
System client keyed by persist_directory path (see
chromadb.api.client.SharedSystemClient). Since Streamlit Cloud reuses the
same worker process across reruns, doing shutil.rmtree() on chroma_db_dir
and then immediately reopening Chroma at the SAME path returns a stale
cached client pointing at a now-deleted sqlite file, causing
"attempt to write a readonly database" (SQLITE_READONLY_DBMOVED) on the
next write. Clearing the cache right after rmtree forces a genuinely
fresh client to be opened. See get_vector_store() below.
"""
import os
import shutil
import tempfile

from chromadb.api.client import SharedSystemClient
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import OLLAMA_EMBED_MODEL
from core.paths import get_user_paths, sanitize_filename
from core import bridge_client
from core.bridge_client import BridgeUnavailableError
from core.llm import embed_texts


class BridgeEmbeddings:
    """
    Drop-in replacement for langchain_ollama.OllamaEmbeddings that routes
    through storage_bridge.py's /ollama/embed route (via
    core.llm.embed_texts()) instead of calling Ollama directly -- mirrors
    BridgeChatLLM's fix for chat/generate. Implements the minimal
    Embeddings interface Chroma needs: embed_documents(list[str]) ->
    list[list[float]], embed_query(str) -> list[float].

    NOT a LangChain Embeddings subclass -- like BridgeChatLLM, this is a
    plain object. Chroma only actually calls .embed_documents() and
    .embed_query(), both of which are implemented here, so it works as
    a duck-typed embedding_function without inheriting from anything.
    """

    def __init__(self, model: str, username: str):
        self.model = model
        self.username = username

    def embed_documents(self, texts: list) -> list:
        return embed_texts(username=self.username, texts=texts, model=self.model)

    def embed_query(self, text: str) -> list:
        return embed_texts(username=self.username, texts=[text], model=self.model)[0]


def _clear_local_cache(chroma_db_dir: str):
    shutil.rmtree(chroma_db_dir, ignore_errors=True)
    SharedSystemClient.clear_system_cache()


def _fetch_subject_files_to_temp_dir(username: str, subject: str) -> "tuple[str, list]":
    """
    Downloads every source file for a subject from the bridge into a
    fresh temp directory. Returns (temp_dir_path, list_of_local_file_paths).
    Caller is responsible for cleaning up temp_dir_path (shutil.rmtree)
    once done with it.
    """
    temp_dir = tempfile.mkdtemp(prefix="scholarai_src_")
    local_paths = []

    try:
        filenames = bridge_client.list_files(username, subject)
    except BridgeUnavailableError:
        # Bridge is down — return empty rather than crashing the whole
        # chat/quiz flow. Callers already handle "no documents" gracefully.
        return temp_dir, []

    for filename in filenames:
        try:
            file_bytes = bridge_client.download_file(username, subject, filename)
        except BridgeUnavailableError:
            continue  # skip this one file, try the rest
        local_path = os.path.join(temp_dir, filename)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        local_paths.append(local_path)

    return temp_dir, local_paths


def get_vector_store(username: str, subject: str, force_rebuild: bool = False):
    """
    Fix Issue #16, #17, #18, #19: Scoped Vector Store & Rebuilding.
    Loads (or builds) a Chroma vector store scoped to a single user+subject.
    Returns None if there are no source documents to embed.

    Source files are now fetched from the storage bridge into a temp
    directory before being loaded — see _fetch_subject_files_to_temp_dir.

    Embeddings go through BridgeEmbeddings (bridge-routed) rather than
    OllamaEmbeddings (direct-to-Ollama) -- see module docstring.
    """
    paths = get_user_paths(username, subject)
    chroma_db_dir = paths["chroma"]
    embeddings = BridgeEmbeddings(model=OLLAMA_EMBED_MODEL, username=username)

    if force_rebuild:
        _clear_local_cache(chroma_db_dir)

    if not force_rebuild and os.path.exists(chroma_db_dir) and os.listdir(chroma_db_dir):
        try:
            return Chroma(persist_directory=chroma_db_dir, embedding_function=embeddings)
        except Exception as e:
            print(f"[ScholarAI] Local Chroma cache unusable ({e}); rebuilding...")
            _clear_local_cache(chroma_db_dir)

    temp_dir, local_file_paths = _fetch_subject_files_to_temp_dir(username, subject)
    try:
        documents = []
        for local_path in local_file_paths:
            try:
                if local_path.lower().endswith(".pdf"):
                    documents.extend(PyPDFLoader(local_path).load())
                elif local_path.lower().endswith(".txt"):
                    documents.extend(TextLoader(local_path).load())
            except Exception:
                continue  # skip a single corrupt/unreadable file rather than failing the whole subject

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
            try:
                return Chroma.from_texts(
                    texts=chunks, embedding=embeddings, metadatas=metadatas, persist_directory=chroma_db_dir
                )
            except Exception:
                _clear_local_cache(chroma_db_dir)
                raise
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_chapter_text(username: str, subject: str, chapter: str):
    """
    Returns the exact, full text of a chapter's source file (txt or pdf),
    rather than retrieving embedded chunks. Used wherever a feature needs
    the complete chapter content (e.g. flashcard/quiz generation).

    Fetches the matching file from the bridge into a temp location first,
    reads it, then cleans up — rather than reading a persistent local
    "sources" folder that no longer exists.
    """
    try:
        filenames = bridge_client.list_files(username, subject)
    except BridgeUnavailableError:
        return None

    safe_chapter = sanitize_filename(chapter).lower()
    matched_filename = next(
        (f for f in filenames if safe_chapter in sanitize_filename(f).lower() and f.endswith((".txt", ".pdf"))),
        None,
    )
    if not matched_filename:
        return None

    try:
        file_bytes = bridge_client.download_file(username, subject, matched_filename)
    except BridgeUnavailableError:
        return None

    temp_dir = tempfile.mkdtemp(prefix="scholarai_chap_")
    try:
        local_path = os.path.join(temp_dir, matched_filename)
        with open(local_path, "wb") as f:
            f.write(file_bytes)

        if local_path.lower().endswith(".txt"):
            with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        else:
            return "\n\n".join(page.page_content for page in PyPDFLoader(local_path).load())
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
