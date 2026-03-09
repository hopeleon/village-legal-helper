# -*- coding: utf-8 -*-
"""Tiny demo server: serves a simple UI and keyword search over law chunks."""
import json
import math
import os
import re
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

try:
    from rag_langchain import retrieve_passages

    RAG_LANGCHAIN_AVAILABLE = True
    _RAG_IMPORT_ERROR = None
except Exception as e:
    RAG_LANGCHAIN_AVAILABLE = False
    _RAG_IMPORT_ERROR = f"{type(e).__name__}: {e}"

LEGAL_KEYWORDS = {
    "法",
    "法律",
    "法条",
    "法规",
    "规定",
    "条例",
    "责任",
    "处罚",
    "维权",
    "起诉",
    "立案",
    "诉讼",
    "仲裁",
    "调解",
    "赔偿",
    "合同",
    "侵权",
    "欠款",
    "拖欠",
    "劳动",
    "用工",
    "工资",
    "婚姻",
    "离婚",
    "赡养",
    "抚养",
    "土地",
    "宅基地",
    "征地",
    "耕地",
    "纠纷",
    "环保",
    "污染",
    "垃圾",
    "污水",
    "家暴",
    "未成年",
    "诈骗",
    "治安",
    "报警",
    "律师",
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


def is_legal_query(raw_query: str, tokens: list):
    if not raw_query:
        return False
    for key in TOPIC_LAW_BOOSTS.keys():
        if key in raw_query:
            return True
    for key in LEGAL_KEYWORDS:
        if key in raw_query:
            return True
    for t in tokens:
        if t in LEGAL_KEYWORDS:
            return True
    return False


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
# Legacy common model (kept for backward compatibility).
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
# Legal answers should prefer a stronger reasoning model by default.
# Only use another legal model when explicitly configured via LLM_MODEL_LEGAL.
LLM_MODEL_LEGAL = os.getenv("LLM_MODEL_LEGAL", "").strip() or "deepseek-reasoner"
# General answers keep backward compatibility with old LLM_MODEL.
LLM_MODEL_GENERAL = os.getenv("LLM_MODEL_GENERAL", "").strip() or (LLM_MODEL or "deepseek-chat")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"
RAG_BACKEND = os.getenv("RAG_BACKEND", "langchain").lower()
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_FETCH_K = int(os.getenv("RAG_FETCH_K", "15"))


def _build_user_content(query: str, passages: list, context: str = ""):
    content_lines = [f"用户咨询：{query}"]
    if passages:
        content_lines.append("相关法条节选：")
        for i, p in enumerate(passages, 1):
            title = f"{p.get('law','')}{p.get('article','')}"
            content_lines.append(f"{i}. {title}：{p.get('text','')}")
    if context:
        content_lines.append("历史对话：")
        content_lines.append(context)
    return "\n".join(content_lines)


def _normalize_passages(passages: list):
    out = []
    for p in passages or []:
        law = (p.get("law", "") if isinstance(p, dict) else "") or ""
        article = (p.get("article", "") if isinstance(p, dict) else "") or ""
        text = ""
        if isinstance(p, dict):
            text = p.get("text", "") or p.get("content", "") or p.get("page_content", "") or ""
        out.append({"law": law, "article": article, "text": text})
    return out


def _pick_law_names(passages: list, limit: int = 5):
    names = []
    seen = set()
    for p in passages or []:
        name = (p.get("law", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _ensure_law_mentions(answer: str, passages: list):
    if not answer:
        return answer
    names = _pick_law_names(passages, limit=3)
    if not names:
        return answer
    if any(n in answer for n in names):
        return answer
    refs = "、".join(f"《{n}》" for n in names)
    suffix = f"\n\n可重点参考：{refs}。"
    return answer.rstrip() + suffix


def _sanitize_answer_text(answer: str):
    if not answer:
        return answer
    text = answer.strip()
    # Remove mechanical/meta openings that should not appear in final answer.
    patterns = [
        r"^我正在查找相关法条[，,。.\s]*请稍候[，,。.\s]*",
        r"^根据您提供的法条节选[，,。.\s]*",
        r"^根据你提供的法条节选[，,。.\s]*",
        r"^根据您提供的信息[，,。.\s]*",
        r"^根据你提供的信息[，,。.\s]*",
    ]
    for p in patterns:
        text = re.sub(p, "", text)
    # Remove awkward second-person phrasing globally in case it appears mid-answer.
    text = re.sub(r"根据您提供的法条节选[，,。:\s]*", "", text)
    text = re.sub(r"根据你提供的法条节选[，,。:\s]*", "", text)
    text = re.sub(r"根据您提供的法条[，,。:\s]*", "", text)
    text = re.sub(r"根据你提供的法条[，,。:\s]*", "", text)
    text = re.sub(r"根据您提供的信息[，,。:\s]*", "", text)
    text = re.sub(r"根据你提供的信息[，,。:\s]*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_structured_sections(answer: str):
    if not answer:
        return answer
    required = ["【问题定义】", "【法条分析】", "【行动指南】", "【风险与边界提示】"]
    if all(tag in answer for tag in required):
        return answer
    parts = [p.strip() for p in re.split(r"\n{2,}", answer.strip()) if p.strip()]
    while len(parts) < 4:
        parts.append(parts[-1] if parts else "请补充事实细节后进一步判断。")
    structured = (
        f"【问题定义】{parts[0]}\n\n"
        f"【法条分析】{parts[1]}\n\n"
        f"【行动指南】{parts[2]}\n\n"
        f"【风险与边界提示】{parts[3]}"
    )
    return structured


def _build_messages(query: str, passages: list, stream: bool, context: str = ""):
    law_names = _pick_law_names(passages, limit=5)
    law_name_line = f"候选法条名称：{'; '.join(law_names)}。" if law_names else ""
    user_content = _build_user_content(query, passages, context=context)
    if stream:
        return [
            {
                "role": "system",
                "content": (
                    "你是资深法律咨询助手，服务乡村场景。请基于给定法条节选，"
                    "给出专业、完整、可执行的建议；不能编造法条。"
                    "避免替代法官或律师作最终裁判。只输出纯文本。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{user_content}\n\n"
                    f"{law_name_line}\n"
                    "请按固定结构输出，并使用以下四个小标题："
                    "【问题定义】、【法条分析】、【行动指南】、【风险与边界提示】。"
                    "每个小标题下写2-4句，整体不少于8句。"
                    "必须点名引用1-3个候选法条名称（使用《法条名》格式）。"
                    "语气专业但易懂，不要JSON。"
                    "不要出现“我正在查找相关法条，请稍候”或“根据您提供的法条节选”等过程化话术。"
                    "引用法律时，优先使用“根据我国《...法》...”的句式，不要使用“根据你/您提供的法条...”句式。"
                ),
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是资深法律咨询助手，服务乡村场景。请基于给定法条节选，"
                "给出专业、完整、可执行的建议；不能编造法条。"
                "避免替代法官或律师作最终裁判。仅输出JSON，不要任何额外文字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_content}\n\n"
                f"{law_name_line}\n"
                "请严格只输出JSON，格式：{\"answer\":\"...\"}。\n"
                "answer需使用四个小标题："
                "【问题定义】、【法条分析】、【行动指南】、【风险与边界提示】；"
                "每个小标题下写2-4句，整体不少于8句；"
                "必须点名引用1-3个候选法条名称（使用《法条名》格式）；"
                "语气专业但易懂、贴近乡村场景；"
                "不要出现“我正在查找相关法条，请稍候”或“根据您提供的法条节选”等过程化话术；"
                "引用法律时，优先使用“根据我国《...法》...”的句式，不要使用“根据你/您提供的法条...”句式；"
                "不得输出任何说明文字或Markdown。"
            ),
        },
    ]


def _build_messages_general(query: str, stream: bool, context: str = ""):
    user_content = _build_user_content(query, [], context=context)
    if stream:
        return [
            {
                "role": "system",
                "content": (
                    "你是乡村法律小帮手。若问题不涉及法律，给出清晰可执行的建议即可，"
                    "不要强行引用法条。只输出纯文本。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{user_content}\n\n"
                    "请直接输出整体答复（2-4句），语气温和、贴近乡村场景。"
                    "不要标题、不要编号、不要JSON。"
                ),
            },
        ]
    return [
        {
            "role": "system",
            "content": (
                "你是乡村法律小帮手。若问题不涉及法律，给出清晰可执行的建议即可，"
                "不要强行引用法条。仅输出JSON，不要任何额外文字。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{user_content}\n\n"
                "请严格只输出JSON，格式：{\"answer\":\"...\"}。\n"
                "answer为简明整体答复（2-4句），语气温和、贴近乡村场景；"
                "不得输出任何说明文字或Markdown。"
            ),
        },
    ]


def call_deepseek_answer(query: str, passages: list, context: str = ""):
    if not LLM_API_KEY:
        return None, "missing_api_key", None

    passages = _normalize_passages(passages)
    payload = {
        "model": LLM_MODEL_LEGAL,
        "messages": _build_messages(query, passages, stream=False, context=context),
        "temperature": 0.2,
        "max_tokens": 720,
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
            cleaned = _sanitize_answer_text(parsed["answer"].strip())
            cleaned = _ensure_structured_sections(cleaned)
            return _ensure_law_mentions(cleaned, passages), None, content
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
                cleaned = _sanitize_answer_text(parsed["answer"].strip())
                cleaned = _ensure_structured_sections(cleaned)
                return _ensure_law_mentions(cleaned, passages), None, content
            return content.strip(), "bad_json", content
        return content.strip(), "bad_json_schema", content
    except (URLError, KeyError, ValueError, IndexError) as e:
        return None, f"call_failed:{type(e).__name__}", None


def call_deepseek_answer_general(query: str, context: str = ""):
    if not LLM_API_KEY:
        return None, "missing_api_key", None

    payload = {
        "model": LLM_MODEL_GENERAL,
        "messages": _build_messages_general(query, stream=False, context=context),
        "temperature": 0.1,
        "max_tokens": 420,
        "stream": False,
    }

    url = f"{LLM_BASE_URL}/chat/completions"

    def _extract_json(text: str):
        try:
            return json.loads(text)
        except Exception:
            pass
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except Exception:
                pass
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
            cleaned = _sanitize_answer_text(parsed["answer"].strip())
            return cleaned, None, content
        if not isinstance(parsed, dict):
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
                            f"{_build_user_content(query, [], context=context)}\n\n"
                            "输出格式：{\"answer\":\"...\"}，只允许JSON文本。"
                        ),
                    },
                ],
            }
            data = _post(retry)
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
                cleaned = _sanitize_answer_text(parsed["answer"].strip())
                return cleaned, None, content
            return content.strip(), "bad_json", content
        return content.strip(), "bad_json_schema", content
    except (URLError, KeyError, ValueError, IndexError) as e:
        return None, f"call_failed:{type(e).__name__}", None


def call_deepseek_answer_stream(query: str, passages: list, context: str = ""):
    if not LLM_API_KEY:
        yield "", "missing_api_key"
        return

    passages = _normalize_passages(passages)
    payload = {
        "model": LLM_MODEL_LEGAL,
        "messages": _build_messages(query, passages, stream=True, context=context),
        "temperature": 0.2,
        "max_tokens": 720,
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


def call_deepseek_answer_stream_general(query: str, context: str = ""):
    if not LLM_API_KEY:
        yield "", "missing_api_key"
        return

    payload = {
        "model": LLM_MODEL_GENERAL,
        "messages": _build_messages_general(query, stream=True, context=context),
        "temperature": 0.1,
        "max_tokens": 420,
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
    return (
        "先把事情的时间、地点、涉及人员和关键证据整理清楚（如照片、视频、聊天记录等）。"
        "可以先找村委、司法所或调解组织帮忙沟通，必要时再咨询律师。"
        "后续若需要维权，也可以走调解或依法起诉的路径。"
    )


def _fallback_answer_with_laws(query: str, passages: list):
    names = _pick_law_names(passages, limit=3)
    refs = "、".join(f"《{n}》" for n in names) if names else "相关法律法规"
    first_text = ""
    if passages:
        first_text = (passages[0].get("text", "") or "").strip()
    evidence_hint = first_text[:80] + ("..." if len(first_text) > 80 else "") if first_text else "请结合当地具体事实与证据进一步核对条款适用。"
    return (
        f"【问题定义】你描述的情形属于现实生活中常见且危害性较高的权益侵害/环境治理问题，核心在于尽快制止违法行为并固定证据。"
        f"争议焦点通常包括：是否构成违法、应由哪个部门先受理、以及后续追责或救济路径如何衔接。"
        f"【法条分析】可重点参考{refs}。这些法律通常覆盖禁止性规范、监管职责和责任承担机制。"
        f"在落地判断时，应把行为事实与法条构成要件逐项对应，而不是只看原则性表述。当前检索片段提示：{evidence_hint}"
        f"【行动指南】第一步先全面固定证据（照片视频、时间地点、受影响范围、证人证言、沟通记录）。"
        f"第二步向有管辖权的部门实名反映并保留受理回执，必要时同步走村委会/司法所协同处置。"
        f"第三步根据处理结果选择继续行政投诉、调解或诉讼，形成“取证-受理-处置-追责”闭环。"
        f"【风险与边界提示】如存在持续性人身风险或公共安全风险，应优先报警或申请紧急保护措施。"
        f"若涉及未成年人、耕地红线或群体性影响，建议尽快升级到县级主管部门并同步咨询律师。"
    )


def _fallback_general_answer():
    return (
        "这类问题可能不涉及具体法条，我可以先给出一般建议。"
        "你可以把情况再说明得更具体一些（时间、地点、涉及人员、诉求）。"
        "如果后续确实牵涉权益或纠纷，再帮你对应法律依据。"
    )


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
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

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
        if parsed.path == "/asr_helper.js":
            body = (DEMO_DIR / "asr_helper.js").read_bytes()
            return self._send(body, "text/javascript; charset=utf-8")
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
            legal_query = is_legal_query(q, tokens)
            top = []
            rag_meta = None
            if q and legal_query:
                if RAG_BACKEND == "langchain" and RAG_LANGCHAIN_AVAILABLE:
                    top, rag_meta = retrieve_passages(q, top_k=RAG_TOP_K, fetch_k=RAG_FETCH_K)
                    # If langchain returns empty/noisy results, fallback to local BM25.
                    if not top:
                        top = bm25_score(INDEX, tokens, top_n=RAG_TOP_K, raw_query=q)
                else:
                    top = bm25_score(INDEX, tokens, top_n=RAG_TOP_K, raw_query=q)

            # If still empty, fall back to boosted laws directly
            if not top and q and legal_query:
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
            top = _normalize_passages(top)
            results = [
                {
                    "law": item.get("law", ""),
                    "article": item.get("article", ""),
                    "text": item.get("text", ""),
                }
                for item in top
            ]
            recommend_laws = bool(legal_query and results)
            if stream:
                self._send_sse_headers()
                if recommend_laws:
                    self._sse_send({"type": "status", "message": "正在整理思路，马上回复你..."})
                else:
                    msg = "未找到匹配法条，先给你直接建议。" if legal_query else "该问题不一定涉及法条，先给你直接建议。"
                    self._sse_send({"type": "status", "message": msg})

                answer = ""
                shown = ""
                gen_error = None
                if LLM_PROVIDER == "deepseek" and recommend_laws:
                    for delta, err in call_deepseek_answer_stream(q, top, context=context):
                        if err:
                            gen_error = err
                            break
                        if delta:
                            answer += delta
                            cleaned = _sanitize_answer_text(answer)
                            if cleaned.startswith(shown):
                                out_delta = cleaned[len(shown) :]
                            else:
                                out_delta = cleaned
                            if out_delta:
                                shown = cleaned
                                self._sse_send({"type": "answer_delta", "delta": out_delta})
                if LLM_PROVIDER == "deepseek" and not recommend_laws:
                    for delta, err in call_deepseek_answer_stream_general(q, context=context):
                        if err:
                            gen_error = err
                            break
                        if delta:
                            answer += delta
                            cleaned = _sanitize_answer_text(answer)
                            if cleaned.startswith(shown):
                                out_delta = cleaned[len(shown) :]
                            else:
                                out_delta = cleaned
                            if out_delta:
                                shown = cleaned
                                self._sse_send({"type": "answer_delta", "delta": out_delta})
                if not answer:
                    answer = _fallback_answer_with_laws(q, top) if recommend_laws else _fallback_general_answer()
                    if gen_error:
                        self._sse_send({"type": "status", "message": "模型暂时没响应，先给你一份通用建议。"})
                    self._sse_send({"type": "answer_delta", "delta": answer})
                elif recommend_laws:
                    ensured = _sanitize_answer_text(answer)
                    ensured = _ensure_structured_sections(ensured)
                    ensured = _ensure_law_mentions(ensured, top)
                    if ensured != answer:
                        extra = ensured[len(shown) :] if ensured.startswith(shown) else ensured
                        answer = ensured
                        if extra:
                            self._sse_send({"type": "answer_delta", "delta": extra})
                else:
                    ensured = _sanitize_answer_text(answer)
                    if ensured != answer:
                        extra = ensured[len(shown) :] if ensured.startswith(shown) else ensured
                        answer = ensured
                        if extra:
                            self._sse_send({"type": "answer_delta", "delta": extra})

                self._sse_send(
                    {
                        "type": "laws",
                        "count": len(results),
                        "results": results,
                        "recommend": recommend_laws,
                        "legal_query": legal_query,
                    }
                )
            if LLM_DEBUG and stream:
                self._sse_send(
                    {
                        "type": "debug",
                        "tokens": tokens[:20],
                        "llm_enabled": bool(LLM_API_KEY),
                        "llm_error": gen_error,
                        "model_selected": LLM_MODEL_LEGAL if recommend_laws else LLM_MODEL_GENERAL,
                        "model_legal": LLM_MODEL_LEGAL,
                        "model_general": LLM_MODEL_GENERAL,
                        "provider": LLM_PROVIDER,
                        "rag_backend": RAG_BACKEND,
                        "rag_available": RAG_LANGCHAIN_AVAILABLE,
                        "rag_meta": rag_meta,
                        "rag_import_error": _RAG_IMPORT_ERROR,
                    }
                )
                self._sse_send({"type": "done"})
                return

            generated = None
            gen_error = None
            gen_raw = None
            if LLM_PROVIDER == "deepseek" and recommend_laws:
                generated, gen_error, gen_raw = call_deepseek_answer(q, top, context=context)
                time.sleep(0.1)
            if LLM_PROVIDER == "deepseek" and not recommend_laws:
                generated, gen_error, gen_raw = call_deepseek_answer_general(q, context=context)
                time.sleep(0.1)
            if not generated:
                generated = _fallback_answer_with_laws(q, top) if recommend_laws else _fallback_general_answer()
            elif recommend_laws:
                generated = _ensure_structured_sections(_sanitize_answer_text(generated))
                generated = _ensure_law_mentions(generated, top)
            else:
                generated = _sanitize_answer_text(generated)

            response = {
                "query": q,
                "count": len(results),
                "answer": generated,
                "results": results,
                "recommend_laws": recommend_laws,
                "legal_query": legal_query,
            }
            if LLM_DEBUG:
                response["debug"] = {
                    "tokens": tokens[:20],
                    "llm_enabled": bool(LLM_API_KEY),
                    "llm_error": gen_error,
                    "model_selected": LLM_MODEL_LEGAL if recommend_laws else LLM_MODEL_GENERAL,
                    "model_legal": LLM_MODEL_LEGAL,
                    "model_general": LLM_MODEL_GENERAL,
                    "provider": LLM_PROVIDER,
                    "rag_backend": RAG_BACKEND,
                    "rag_available": RAG_LANGCHAIN_AVAILABLE,
                    "rag_meta": rag_meta,
                    "rag_import_error": _RAG_IMPORT_ERROR,
                }
                if gen_raw:
                    response["debug"]["llm_raw"] = gen_raw[:800]
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            return self._send(body, "application/json; charset=utf-8")

        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print("Demo server running on http://localhost:9000")
    ThreadingHTTPServer(("", 9000), Handler).serve_forever()
