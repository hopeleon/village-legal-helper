# -*- coding: utf-8 -*-
"""Build FAISS index for law chunks."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_community.vectorstores import FAISS
from rag_langchain import chunks_to_documents, get_embeddings, load_chunks  # noqa: E402


def load_env(env_path: Path):
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def main():
    load_env(ROOT / ".env")
    chunks = load_chunks()
    if not chunks:
        print("No chunks found. Please check data/laws/chunks.jsonl")
        return 1
    docs = chunks_to_documents(chunks)
    embeddings = get_embeddings()
    print("Building FAISS index...")
    vs = FAISS.from_documents(docs, embeddings)
    out_dir = Path(
        os.getenv("FAISS_DIR", str(ROOT / "data" / "laws" / "faiss_index"))
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    vs.save_local(str(out_dir))
    print(f"Saved index to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
