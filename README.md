# 乡村法律小帮手（RAG + LLM）

面向乡村治理与基层生活场景的法律咨询系统。项目以法条检索增强生成（RAG）为核心，支持流式问答、法条依据联动展示、多会话管理与语音输入，适用于产品演示、原型验证与后续工程化扩展。

## 1. 项目目标

- 提供“可解释、可核验、可执行”的法律问答体验
- 在回答中明确关联检索法条，降低泛化回答风险
- 兼顾专业性与可读性，适配基层用户使用场景

## 2. 核心能力

### 2.1 法律问答质量增强

- 法律问题与通用问题双模型分流，法律问题默认使用更强推理模型
- 法律回答采用结构化模板输出，重点覆盖：
  - `【问题定义】`
  - `【法条分析】`
  - `【行动指南】`
  - `【风险与边界提示】`
- 输出后处理包含话术清洗、结构兜底与法条引用补全

### 2.2 RAG 检索与引用增强

- 支持 LangChain 混合检索（BM25 + FAISS 向量检索 + 重排）
- 检索结果统一归一化为 `law/article/text`，避免字段漂移
- LangChain 结果为空时自动回退 BM25，提高检索稳定性
- 强制回答点名引用检索到的法条名称，提升答案可核验性

### 2.3 前端体验与可操作性

- 页面布局为“主对话区 + 法条面板 + 侧栏会话”
- 支持多会话历史保留与会话切换（`新聊天` 不清空历史）
- 法条面板集中展示依据，支持展开查看完整条文
- 支持 Markdown 关键格式渲染（如 `**加粗**`、行内代码）
- 支持语音输入（Web Speech API）

### 2.4 流式与调试能力

- SSE 流式输出回答增量、法条结果与状态信息
- 可通过 `LLM_DEBUG=1` 输出模型与检索调试信息

## 3. 技术架构

### 3.1 后端

- `demo_server.py`
  - HTTP 服务与接口分发
  - 检索编排（LangChain/BM25）
  - 模型调用（非流式/流式）
  - 输出清洗与结构化后处理

- `rag_langchain.py`
  - 数据加载与 Document 构建
  - BM25 与向量检索混合召回
  - 可选 Cross-Encoder 重排
  - 返回统一法条字段与检索元信息

### 3.2 前端

- `demo/index.html`：页面结构
- `demo/style.css`：界面样式与响应式布局
- `demo/app.js`：会话管理、流式渲染、法条联动、语音输入接入

### 3.3 数据

- `data/laws/chunks.jsonl`：法条切片数据（核心检索语料）
- `data/laws/faiss_index/`：向量索引（首次构建）

## 4. 目录结构

```text
.
├─ demo_server.py                # 后端主服务（RAG 编排 + LLM 调用 + SSE）
├─ rag_langchain.py              # LangChain 检索实现（混合召回 + 重排）
├─ demo/
│  ├─ index.html                 # 前端页面
│  ├─ style.css                  # 前端样式
│  ├─ app.js                     # 前端逻辑（多会话、流式渲染、法条面板）
│  └─ asr_helper.js              # 语音识别辅助
├─ data/laws/chunks.jsonl        # 法条切片语料
├─ scripts/build_vector_index.py # 向量索引构建脚本
├─ requirements.txt              # Python 依赖
├─ .env.example                  # 环境变量模板
└─ README.md
```

## 5. 快速开始

### 5.1 环境要求

- Python 3.10+
- 推荐浏览器：Chrome / Edge（语音输入兼容更好）

### 5.2 安装依赖

```bash
pip install -r requirements.txt
```

### 5.3 配置环境变量

复制 `.env.example` 到 `.env` 并按需填写：

```bash
copy .env.example .env
```

常用配置：

- `LLM_PROVIDER`：默认 `deepseek`
- `LLM_API_KEY`：模型服务密钥
- `LLM_BASE_URL`：模型服务地址
- `LLM_MODEL_LEGAL`：法律问题模型（默认 `deepseek-reasoner`）
- `LLM_MODEL_GENERAL`：通用问题模型（默认 `deepseek-chat`）
- `RAG_BACKEND`：`langchain` 或 `bm25`
- `RAG_TOP_K`：返回法条数量（默认 `5`）
- `RAG_FETCH_K`：召回候选数（默认 `15`）
- `LLM_DEBUG`：`1` 开启调试输出

### 5.4 构建向量索引（首次）

```bash
python scripts/build_vector_index.py
```

### 5.5 启动服务

```bash
python demo_server.py
```

访问：

- `http://localhost:9000`

## 6. API 说明

### 6.1 `GET /search`

查询参数：

- `q`：用户问题（必填）
- `context`：历史上下文（可选，后端自动截断）
- `stream`：是否流式（`1/true/yes` 开启 SSE）

非流式返回（JSON）核心字段：

- `answer`：最终回答
- `results`：法条结果列表（`law/article/text`）
- `recommend_laws`：是否推荐法条
- `legal_query`：是否识别为法律问题

流式返回（SSE）事件 `type`：

- `status`：状态信息
- `answer_delta`：回答增量
- `laws`：法条结果
- `debug`：调试信息（`LLM_DEBUG=1`）
- `done`：结束标记

## 7. 关键逻辑位置（便于二次开发）

### 7.1 RAG 检索与回退

- `demo_server.py`
  - `if RAG_BACKEND == "langchain"...`（LangChain 检索入口）
  - LangChain 为空时回退 BM25
  - `_normalize_passages(...)` 统一法条字段

- `rag_langchain.py`
  - `retrieve_passages(...)` 检索主流程
  - `_build_retriever(...)` 混合检索器构建
  - `_rerank(...)` 重排

### 7.2 回答质量与引用约束

- `demo_server.py`
  - `_build_messages(...)` 法律回答模板
  - `_sanitize_answer_text(...)` 输出清洗
  - `_ensure_structured_sections(...)` 结构兜底
  - `_ensure_law_mentions(...)` 法条引用兜底

## 8. 常见问题

### 8.1 法条为空或命中不稳定

- 检查 `data/laws/chunks.jsonl` 是否存在且字段完整
- 检查 FAISS 索引是否已构建
- 调高 `RAG_FETCH_K` 并观察 `LLM_DEBUG` 输出

### 8.2 模型回答过短或不稳定

- 检查 `LLM_MODEL_LEGAL` 是否正确配置
- 确认 `LLM_API_KEY` 可用、请求未超时
- 开启 `LLM_DEBUG` 查看 `llm_error` 与原始片段

### 8.3 语音输入不可用

- 使用支持 Web Speech API 的浏览器
- 确认麦克风权限
- 建议使用 `localhost` 访问

## 9. 路线图（Roadmap）

- GraphRAG 增量接入（法条-行为-责任主体-处置机关关系图）
- 检索准确率专项优化（查询分析器、多路召回、低置信度追问）
- 用户体验与可操作化升级（会话持久化、法条一键引用、行动清单）

## 10. 合规声明

本项目用于技术演示与产品验证，不构成正式法律意见。实际案件处理请结合当地主管部门要求，并在必要时咨询持证律师。
