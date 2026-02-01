# -*- coding: utf-8 -*-
"""Split related laws into article-level JSONL chunks for a minimal RAG demo."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "Chinese-Laws" / "相关"
OUTPUT_DIR = ROOT / "data" / "laws"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "chunks.jsonl"

ARTICLE_RE = re.compile(r"(第[一二三四五六七八九十百千零〇0-9]+条)")


def split_articles(text: str):
    parts = ARTICLE_RE.split(text)
    if len(parts) <= 1:
        return [("", text.strip())]

    chunks = []
    # parts: [preamble, article1, body1, article2, body2, ...]
    preamble = parts[0].strip()
    if preamble:
        chunks.append(("前言", preamble))

    for i in range(1, len(parts), 2):
        article = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            chunks.append((article, body))
    return chunks


def main():
    if not INPUT_DIR.exists():
        raise SystemExit(f"Missing input dir: {INPUT_DIR}")

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for path in sorted(INPUT_DIR.glob("*.txt")):
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            text = text.strip()
            for article, body in split_articles(text):
                chunk = {
                    "id": f"{path.stem}_{article}" if article else path.stem,
                    "law": path.stem,
                    "article": article,
                    "text": (article + body).strip() if article else body.strip(),
                }
                if chunk["text"]:
                    f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"Wrote: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
