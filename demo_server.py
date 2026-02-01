# -*- coding: utf-8 -*-
"""Tiny demo server: serves a simple UI and keyword search over law chunks."""
import json
import math
import os
import re
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "laws" / "chunks.jsonl"
DEMO_DIR = ROOT / "demo"

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

# Keyword -> preferred laws (used to boost recall for typical rural cases)
TOPIC_LAW_BOOSTS = {
    "家暴": ["中华人民共和国反家庭暴力法", "中华人民共和国妇女权益保障法", "中华人民共和国民法典"],
    "家庭暴力": ["中华人民共和国反家庭暴力法", "中华人民共和国妇女权益保障法", "中华人民共和国民法典"],
    "妇女": ["中华人民共和国妇女权益保障法", "中华人民共和国反家庭暴力法"],
    "未成年人": ["中华人民共和国未成年人保护法", "中华人民共和国预防未成年人犯罪法"],
    "未成年": ["中华人民共和国未成年人保护法", "中华人民共和国预防未成年人犯罪法"],
    "赡养": ["中华人民共和国老年人权益保障法", "中华人民共和国民法典"],
    "老年人": ["中华人民共和国老年人权益保障法", "中华人民共和国民法典"],
    "耕地": ["中华人民共和国土地管理法", "中华人民共和国黑土地保护法"],
    "土地": ["中华人民共和国土地管理法"],
    "占地": ["中华人民共和国土地管理法"],
    "垃圾": ["中华人民共和国固体废物污染环境防治法", "中华人民共和国环境保护法"],
    "固废": ["中华人民共和国固体废物污染环境防治法", "中华人民共和国环境保护法"],
    "污水": ["中华人民共和国水污染防治法", "中华人民共和国环境保护法"],
    "排污": ["中华人民共和国水污染防治法", "中华人民共和国环境保护法"],
    "养殖": ["中华人民共和国畜牧法", "中华人民共和国水污染防治法"],
    "种子": ["中华人民共和国种子法", "中华人民共和国消费者权益保护法"],
    "农药": ["中华人民共和国消费者权益保护法"],
    "噪声": ["中华人民共和国噪声污染防治法", "中华人民共和国环境保护法"],
    "焚烧": ["中华人民共和国大气污染防治法", "中华人民共和国环境保护法"],
    "秸秆": ["中华人民共和国大气污染防治法", "中华人民共和国环境保护法"],
}


def tokenize(q: str):
    tokens = []
    for seg in TOKEN_RE.findall(q):
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


def build_index(chunks):
    docs = []
    df = Counter()
    total_len = 0
    for item in chunks:
        # Index both law title + article + text for better matching
        title = f"{item.get('law','')} {item.get('article','')}"
        terms = tokenize(title + " " + item.get("text", ""))
        tf = Counter(terms)
        if not tf:
            docs.append({"item": item, "tf": Counter(), "len": 0})
            continue
        for term in tf.keys():
            df[term] += 1
        dl = sum(tf.values())
        total_len += dl
        docs.append({"item": item, "tf": tf, "len": dl})
    avgdl = (total_len / len(docs)) if docs else 0
    return {"docs": docs, "df": df, "avgdl": avgdl, "N": len(docs)}


def bm25_score(index, query_tokens, top_n=5, raw_query=""):
    if not query_tokens or index["N"] == 0:
        return []
    k1 = 1.2
    b = 0.75
    df = index["df"]
    avgdl = index["avgdl"] or 1
    N = index["N"]

    scores = []
    q_terms = Counter(query_tokens)
    boosts = []
    # Collect preferred laws based on keyword hits in the raw query
    for key, laws in TOPIC_LAW_BOOSTS.items():
        if key in raw_query:
            boosts.extend(laws)
    boost_set = set(boosts)

    for doc in index["docs"]:
        tf = doc["tf"]
        if not tf:
            continue
        score = 0.0
        dl = doc["len"] or 1
        for term in q_terms.keys():
            if term not in tf:
                continue
            # IDF with smoothing
            idf = math.log((N + 1) / (df.get(term, 0) + 1)) + 1
            freq = tf[term]
            denom = freq + k1 * (1 - b + b * dl / avgdl)
            score += idf * (freq * (k1 + 1) / denom)
        # Prefer laws that match topic keywords explicitly
        law_name = doc["item"].get("law", "")
        if law_name in boost_set:
            score *= 1.6
        if score > 0:
            scores.append((score, doc["item"]))
    scores.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scores[:top_n]]


QUERY_EXPANSIONS = {
    "家暴": ["家庭暴力", "暴力", "殴打", "人身安全保护令"],
    "家庭暴力": ["家暴", "暴力", "殴打", "人身安全保护令"],
    "妇女": ["女性", "性别"],
    "赡养": ["抚养", "扶养", "老年人"],
    "垃圾": ["固废", "废弃物", "生活垃圾"],
    "污水": ["排污", "污染"],
    "耕地": ["基本农田", "占地"],
}


def expand_query(raw_query: str, tokens: list):
    expanded = list(tokens)
    for key, extra in QUERY_EXPANSIONS.items():
        if key in raw_query:
            for e in extra:
                expanded.extend(tokenize(e))
    return expanded


def load_chunks():
    if not DATA_FILE.exists():
        return []
    items = []
    with DATA_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def load_env(env_path: Path):
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env(ROOT / ".env")

CHUNKS = load_chunks()
INDEX = build_index(CHUNKS)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"


def _build_user_content(query: str, passages: list, context: str = ""):
    content_lines = [f"用户咨询：{query}", "相关法条节选："]
    for i, p in enumerate(passages, 1):
        title = f"{p.get('law','')}{p.get('article','')}"
        content_lines.append(f"{i}. {title}：{p.get('text','')}")
    if context:
        content_lines.append("历史对话：")
        content_lines.append(context)
    return "\n".join(content_lines)


def _build_messages(query: str, passages: list, stream: bool, context: str = ""):
    user_content = _build_user_content(query, passages, context=context)
    if stream:
        return [
            {
                "role": "system",
                "content": (
                    "你是乡村法律咨询助手。根据给定法条节选生成整体答复，"
                    "避免下结论或替代律师意见。只输出纯文本。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{user_content}\n\n"
                    "请直接输出整体答复（3-6句）并给出可执行建议（1-3条）。"
                    "不要标题、不要编号、不要JSON。"
                ),
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是乡村法律咨询助手。根据给定法条节选生成整体答复，"
                "避免下结论或替代律师意见。仅输出JSON，不要任何额外文字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_content}\n\n"
                "请严格只输出JSON，格式：{\"answer\":\"...\"}。\n"
                "answer为简明整体答复（3-6句）并给出可执行建议（1-3条）；"
                "不得输出任何说明文字或Markdown。"
            ),
        },
    ]


def call_deepseek_answer(query: str, passages: list, context: str = ""):
    if not LLM_API_KEY:
        return None, "missing_api_key", None

    payload = {
        "model": LLM_MODEL,
        "messages": _build_messages(query, passages, stream=False, context=context),
        "temperature": 0.1,
        "max_tokens": 300,
        "stream": False,
    }

    url = f"{LLM_BASE_URL}/chat/completions"
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )
    def _extract_json(text: str):
        # Try direct parse
        try:
            return json.loads(text)
        except Exception:
            pass
        # Strip code fences if present
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except Exception:
                pass
        # Fallback: first JSON object-like block
        obj = re.search(r"(\{.*\})", text, re.S)
        if obj:
            try:
                return json.loads(obj.group(1))
            except Exception:
                pass
        return None

    def _post(payload_obj):
        req = Request(
            url,
            data=json.dumps(payload_obj).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}",
            },
            method="POST",
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data

    try:
        data = _post(payload)
        content = data["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
            return parsed["answer"].strip(), None, content
        if not isinstance(parsed, dict):
            # One retry with an even stricter prompt
            retry = {
                **payload,
                "messages": [
                    {
                        "role": "system",
                        "content": "只输出有效JSON，禁止任何其它字符。",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{_build_user_content(query, passages, context=context)}\n\n"
                            "输出格式：{\"answer\":\"...\"}，只允许JSON文本。"
                        ),
                    },
                ],
            }
            data = _post(retry)
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
                return parsed["answer"].strip(), None, content
            return content.strip(), "bad_json", content
        return content.strip(), "bad_json_schema", content
    except (URLError, KeyError, ValueError, IndexError) as e:
        return None, f"call_failed:{type(e).__name__}", None


def call_deepseek_answer_stream(query: str, passages: list, context: str = ""):
    if not LLM_API_KEY:
        return None, "missing_api_key", ""

    payload = {
        "model": LLM_MODEL,
        "messages": _build_messages(query, passages, stream=True, context=context),
        "temperature": 0.1,
        "max_tokens": 300,
        "stream": True,
    }

    url = f"{LLM_BASE_URL}/chat/completions"
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LLM_API_KEY}",
        },
        method="POST",
    )

    def _iter_stream(resp):
        for raw_line in resp:
            if not raw_line:
                continue
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except Exception:
                continue
            delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                yield delta

    try:
        with urlopen(req, timeout=30) as resp:
            for delta in _iter_stream(resp):
                yield delta, None
    except (URLError, ValueError, KeyError) as e:
        yield "", f"call_failed:{type(e).__name__}"


def _fallback_answer():
    return "根据检索到的相关法条，可以先整理事实与证据，明确当事人、时间、地点和主要争议点。建议先与村委、司法所或调解组织沟通，必要时咨询律师，依法通过调解或诉讼方式维护权益。"


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str = "text/plain; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def _sse_send(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = (DEMO_DIR / "index.html").read_bytes()
            return self._send(body, "text/html; charset=utf-8")
        if parsed.path == "/app.js":
            body = (DEMO_DIR / "app.js").read_bytes()
            return self._send(body, "text/javascript; charset=utf-8")
        if parsed.path == "/style.css":
            body = (DEMO_DIR / "style.css").read_bytes()
            return self._send(body, "text/css; charset=utf-8")
        if parsed.path == "/sample_cases.txt":
            body = (DEMO_DIR / "sample_cases.txt").read_bytes()
            return self._send(body, "text/plain; charset=utf-8")
        if parsed.path == "/search":
            q = parse_qs(parsed.query).get("q", [""])[0].strip()
            stream = parse_qs(parsed.query).get("stream", [""])[0].strip() in ("1", "true", "yes")
            context = parse_qs(parsed.query).get("context", [""])[0].strip()
            if len(context) > 1200:
                context = context[:1200]
            tokens = tokenize(q)
            tokens = expand_query(q, tokens)
            top = bm25_score(INDEX, tokens, top_n=5, raw_query=q) if q else []

            # If still empty, fall back to boosted laws directly
            if not top and q:
                boost_laws = []
                for key, laws in TOPIC_LAW_BOOSTS.items():
                    if key in q:
                        boost_laws.extend(laws)
                if boost_laws:
                    seen = set()
                    for item in CHUNKS:
                        law = item.get("law", "")
                        if law in boost_laws and law not in seen:
                            top.append(item)
                            seen.add(law)
                        if len(top) >= 5:
                            break
            results = [
                {
                    "law": item.get("law", ""),
                    "article": item.get("article", ""),
                    "text": item.get("text", ""),
                }
                for item in top
            ]
            if stream:
                self._send_sse_headers()
                self._sse_send({"type": "status", "message": "生成整体答复中..."})

                answer = ""
                gen_error = None
                if LLM_PROVIDER == "deepseek" and top:
                    for delta, err in call_deepseek_answer_stream(q, top, context=context):
                        if err:
                            gen_error = err
                            break
                        if delta:
                            answer += delta
                            self._sse_send({"type": "answer_delta", "delta": delta})
                if not answer:
                    answer = _fallback_answer()
                    if gen_error:
                        self._sse_send({"type": "status", "message": "模型响应异常，已使用默认答复。"})
                    self._sse_send({"type": "answer_delta", "delta": answer})

                self._sse_send({"type": "laws", "count": len(results), "results": results})
                if LLM_DEBUG:
                    self._sse_send(
                        {
                            "type": "debug",
                            "tokens": tokens[:20],
                            "llm_enabled": bool(LLM_API_KEY),
                            "llm_error": gen_error,
                            "model": LLM_MODEL,
                            "provider": LLM_PROVIDER,
                        }
                    )
                self._sse_send({"type": "done"})
                return

            generated = None
            gen_error = None
            gen_raw = None
            if LLM_PROVIDER == "deepseek" and top:
                generated, gen_error, gen_raw = call_deepseek_answer(q, top, context=context)
                time.sleep(0.1)
            if not generated:
                generated = _fallback_answer()

            response = {
                "query": q,
                "count": len(results),
                "answer": generated,
                "results": results,
            }
            if LLM_DEBUG:
                response["debug"] = {
                    "tokens": tokens[:20],
                    "llm_enabled": bool(LLM_API_KEY),
                    "llm_error": gen_error,
                    "model": LLM_MODEL,
                    "provider": LLM_PROVIDER,
                }
                if gen_raw:
                    response["debug"]["llm_raw"] = gen_raw[:800]
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            return self._send(body, "application/json; charset=utf-8")

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print("Demo server running on http://localhost:8000")
    HTTPServer(("", 8000), Handler).serve_forever()
