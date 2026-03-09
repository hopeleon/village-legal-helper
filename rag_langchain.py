# -*- coding: utf-8 -*-
"""LangChain-based RAG utilities: hybrid retrieval + optional rerank."""
import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_community.retrievers import BM25Retriever
try:
    from langchain.retrievers.ensemble import EnsembleRetriever
except Exception:
    EnsembleRetriever = None
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "laws" / "chunks.jsonl"
FAISS_DIR = Path(os.getenv("FAISS_DIR", str(ROOT / "data" / "laws" / "faiss_index")))

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "及",
    "或",
    "而",
    "对",
    "为",
    "把",
    "被",
    "并",
    "等",
    "及其",
    "相关",
    "可以",
    "是否",
    "怎么",
    "如何",
    "我们",
    "他们",
    "村里",
    "村民",
    "问题",
    "处理",
    "法律",
    "规定",
}


def tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    for seg in TOKEN_RE.findall(text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", seg):
            seg = seg.strip()
            if len(seg) == 1:
                if seg not in STOPWORDS:
                    tokens.append(seg)
            else:
                tokens.extend(bg for bg in (seg[i : i + 2] for i in range(len(seg) - 1)) if bg not in STOPWORDS)
        else:
            t = seg.lower()
            if t not in STOPWORDS:
                tokens.append(t)
    return tokens


def load_chunks() -> List[Dict]:
    if not DATA_FILE.exists():
        return []
    items: List[Dict] = []
    with DATA_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def chunks_to_documents(chunks: List[Dict]) -> List[Document]:
    docs: List[Document] = []
    for item in chunks:
        law = item.get("law", "")
        article = item.get("article", "")
        text = item.get("text", "")
        content = f"{law}{article} {text}".strip()
        docs.append(
            Document(
                page_content=content,
                metadata={
                    "id": item.get("id", ""),
                    "law": law,
                    "article": article,
                    "text": text,
                },
            )
        )
    return docs


def get_embeddings() -> HuggingFaceEmbeddings:
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    device = os.getenv("EMBEDDING_DEVICE", "cpu")
    normalize = os.getenv("EMBEDDING_NORMALIZE", "1") == "1"
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": normalize},
    )


def _load_vectorstore(embeddings: HuggingFaceEmbeddings) -> Optional[FAISS]:
    if not FAISS_DIR.exists():
        return None
    try:
        return FAISS.load_local(str(FAISS_DIR), embeddings, allow_dangerous_deserialization=True)
    except Exception:
        return None


def _build_bm25(docs: List[Document], k: int) -> BM25Retriever:
    retriever = BM25Retriever.from_documents(docs, preprocess_func=tokenize)
    retriever.k = k
    return retriever


def _build_retriever(
    docs: List[Document], vectorstore: Optional[FAISS], bm25_k: int, vector_k: int
):
    bm25 = _build_bm25(docs, bm25_k)
    if not vectorstore:
        return bm25, {"ensemble": False, "vector": False}
    vret = vectorstore.as_retriever(search_kwargs={"k": vector_k})
    if EnsembleRetriever:
        ensemble = EnsembleRetriever(retrievers=[bm25, vret], weights=[0.45, 0.55])
        return ensemble, {"ensemble": True, "vector": True}

    class HybridRetriever:
        def __init__(self, a, b):
            self.a = a
            self.b = b

        def get_relevant_documents(self, query: str):
            docs_a = self.a.get_relevant_documents(query)
            docs_b = self.b.get_relevant_documents(query)
            # Simple merge: interleave then dedupe
            merged = []
            seen = set()
            for pair in zip(docs_a, docs_b):
                for d in pair:
                    doc_id = d.metadata.get("id", "")
                    if doc_id and doc_id in seen:
                        continue
                    seen.add(doc_id)
                    merged.append(d)
            for d in docs_a + docs_b:
                doc_id = d.metadata.get("id", "")
                if doc_id and doc_id in seen:
                    continue
                seen.add(doc_id)
                merged.append(d)
            return merged

    return HybridRetriever(bm25, vret), {"ensemble": False, "vector": True}


def _get_reranker() -> Optional[CrossEncoder]:
    model = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base").strip()
    if not model:
        return None
    device = os.getenv("RERANKER_DEVICE", os.getenv("EMBEDDING_DEVICE", "cpu"))
    key = f"{model}@@{device}"
    with _STATE.lock:
        cached = getattr(_STATE, "reranker", None)
        cached_key = getattr(_STATE, "reranker_key", None)
        if cached is not None and cached_key == key:
            return cached
        try:
            loaded = CrossEncoder(model, device=device)
            _STATE.reranker = loaded
            _STATE.reranker_key = key
            return loaded
        except Exception:
            _STATE.reranker = None
            _STATE.reranker_key = None
            return None


def _dedupe_docs(docs: List[Document]) -> List[Document]:
    seen = set()
    unique: List[Document] = []
    for doc in docs:
        doc_id = doc.metadata.get("id", "")
        if doc_id and doc_id in seen:
            continue
        seen.add(doc_id)
        unique.append(doc)
    return unique


def _rerank(query: str, docs: List[Document], top_k: int) -> Tuple[List[Document], Optional[str]]:
    reranker = _get_reranker()
    if not reranker:
        return docs[:top_k], None
    try:
        pairs = [(query, d.page_content) for d in docs]
        scores = reranker.predict(pairs)
        ranked = sorted(zip(scores, docs), key=lambda x: x[0], reverse=True)
        out: List[Document] = []
        for score, doc in ranked[:top_k]:
            doc.metadata["rerank_score"] = float(score)
            out.append(doc)
        return out, None
    except Exception as e:
        return docs[:top_k], f"rerank_failed:{type(e).__name__}"


class _RagState:
    def __init__(self):
        self.lock = threading.Lock()
        self.docs: Optional[List[Document]] = None
        self.vectorstore: Optional[FAISS] = None
        self.last_error: Optional[str] = None


_STATE = _RagState()


def _ensure_loaded() -> _RagState:
    with _STATE.lock:
        if _STATE.docs is not None:
            return _STATE
        chunks = load_chunks()
        _STATE.docs = chunks_to_documents(chunks)
        try:
            embeddings = get_embeddings()
            _STATE.vectorstore = _load_vectorstore(embeddings)
        except Exception as e:
            _STATE.last_error = f"vector_load_failed:{type(e).__name__}"
            _STATE.vectorstore = None
        return _STATE


def retrieve_passages(
    query: str,
    top_k: int = 5,
    fetch_k: int = 15,
) -> Tuple[List[Dict], Dict]:
    state = _ensure_loaded()
    docs = state.docs or []
    if not docs:
        return [], {"error": "no_docs"}

    retriever, meta = _build_retriever(
        docs, state.vectorstore, bm25_k=fetch_k, vector_k=fetch_k
    )

    try:
        if hasattr(retriever, "invoke"):
            raw_docs = retriever.invoke(query)
        else:
            raw_docs = retriever.get_relevant_documents(query)
    except Exception as e:
        return [], {"error": f"retrieve_failed:{type(e).__name__}"}

    raw_docs = _dedupe_docs(raw_docs)
    reranked, rerank_err = _rerank(query, raw_docs, top_k=top_k)

    results = [
        {
            "law": d.metadata.get("law", ""),
            "article": d.metadata.get("article", ""),
            "text": d.metadata.get("text", ""),
        }
        for d in reranked
    ]

    meta.update(
        {
            "count": len(results),
            "rerank_enabled": bool(_get_reranker()),
            "rerank_error": rerank_err,
            "vector_loaded": bool(state.vectorstore),
            "last_error": state.last_error,
        }
    )
    return results, meta
