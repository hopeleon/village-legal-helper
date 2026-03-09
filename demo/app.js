const $ = (id) => document.getElementById(id);
let history = [];
let sampleCache = null;
let sampleCursor = 0;
const sidebarCollapsedKey = "rural_assistant_sidebar_collapsed";
const lawPanelCollapsedKey = "rural_assistant_law_panel_collapsed";
let sessionSeq = 1;
const chatSessions = [];
let activeSessionId = null;
const quickPrompts = [
  {
    title: "邻里纠纷",
    desc: "宅基地边界争议，如何处理？",
    text: "邻里因为宅基地边界起了争议，村委会调解不成，接下来该怎么处理？",
  },
  {
    title: "环境卫生",
    desc: "乱倒垃圾影响农田",
    text: "村里有人乱倒垃圾影响农田，我该向谁反映？",
  },
  {
    title: "劳动用工",
    desc: "务工工资拖欠",
    text: "在村里务工被拖欠工资，有哪些法律途径？",
  },
];

function renderQuickGrid() {
  const grid = $("quickGrid");
  if (!grid) return;
  grid.innerHTML = quickPrompts.map((p, i) => `
    <div class="quick-card" data-idx="${i}">
      <div class="quick-title">${p.title}</div>
      <div class="quick-desc">${p.desc}</div>
    </div>
  `).join("");
  grid.querySelectorAll(".quick-card").forEach((card) => {
    card.addEventListener("click", () => {
      const idx = Number(card.dataset.idx || 0);
      const pick = quickPrompts[idx];
      if (!pick) return;
      $("query").value = pick.text;
      $("query").focus();
      $("status").textContent = "已填好示例，可以直接提问";
    });
  });
}

async function loadSamples() {
  if (sampleCache) return sampleCache;
  const res = await fetch("/sample_cases.txt");
  if (!res.ok) return [];
  const text = await res.text();
  sampleCache = text.split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean);
  return sampleCache;
}

function createSession(title = "新聊天") {
  return {
    id: `s${Date.now()}_${sessionSeq++}`,
    title,
    messages: [],
    updatedAt: Date.now(),
    lawPanel: {
      results: [],
      hintText: "提问后会在这里整理相关法条",
    },
  };
}

function getActiveSession() {
  return chatSessions.find((s) => s.id === activeSessionId) || null;
}

function touchActiveSession() {
  const s = getActiveSession();
  if (s) s.updatedAt = Date.now();
}

function renderSessionList(filterText = "") {
  const list = $("historyList");
  if (!list) return;
  const q = (filterText || "").trim();
  const items = [...chatSessions].sort((a, b) => b.updatedAt - a.updatedAt);
  list.innerHTML = "";
  items.forEach((session) => {
    const raw = `${session.title} ${session.messages.map((m) => m.text || "").join(" ")}`;
    if (q && !raw.includes(q)) return;
    const item = document.createElement("div");
    item.className = "history-item";
    if (session.id === activeSessionId) item.classList.add("active");
    const timeLabel = new Date(session.updatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    item.innerHTML = `
      <div class="history-main">${escapeHtml(session.title || "新聊天")}</div>
      <div class="history-meta">会话记录 · ${timeLabel}</div>
    `;
    item.addEventListener("click", () => {
      switchSession(session.id);
      setSidebarOpen(false);
    });
    list.appendChild(item);
  });
}

function renderChatFromActiveSession() {
  const chat = $("chat");
  if (!chat) return;
  const session = getActiveSession();
  history = session ? session.messages : [];
  chat.innerHTML = "";
  history.forEach((msg, idx) => {
    chat.appendChild(renderMessage(msg, idx));
  });
  chat.scrollTop = chat.scrollHeight;
  const lawState = session?.lawPanel || { results: [], hintText: "提问后会在这里整理相关法条" };
  updateLawPanel(lawState.results || [], true, lawState.hintText || "");
}

function switchSession(sessionId) {
  activeSessionId = sessionId;
  renderChatFromActiveSession();
  renderSessionList($("historySearch")?.value || "");
}

function addMessage(role, text) {
  const msg = { role, text, laws: [] };
  history.push(msg);
  touchActiveSession();
  const node = renderMessage(msg, history.length - 1);
  $("chat").appendChild(node);
  $("chat").scrollTop = $("chat").scrollHeight;
  renderSessionList($("historySearch")?.value || "");
  return history.length - 1;
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMarkdownSafe(text) {
  let out = escapeHtml(text || "").replace(/\r\n/g, "\n");
  // Handle common markdown inline marks after escaping HTML.
  out = out.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  // Clean up dangling bold markers produced by model glitches.
  out = out.replace(/\*\*/g, "");
  return out.replace(/\n/g, "<br>");
}

function renderMessage(msg, index) {
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${msg.role}`;
  wrapper.dataset.index = String(index);

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = msg.role === "user" ? "问" : "答";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const text = document.createElement("div");
  text.className = "msg-text";
  text.innerHTML = renderMarkdownSafe(msg.text || "");

  bubble.appendChild(text);

  if (msg.role === "assistant") {
    const laws = document.createElement("div");
    laws.className = "msg-laws";
    laws.innerHTML = renderLaws(msg.laws || [], false);
    bubble.appendChild(laws);
  }

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  return wrapper;
}

function updateAssistantText(index, delta) {
  const msg = history[index];
  if (!msg) return;
  msg.text += delta;
  const node = $("chat").querySelector(`.msg[data-index="${index}"] .msg-text`);
  if (node) {
    node.innerHTML = renderMarkdownSafe(msg.text);
    node.parentElement?.classList.remove("pending");
  }
  $("chat").scrollTop = $("chat").scrollHeight;
}

function updateAssistantLaws(index, laws, showEmpty = true, hintText = "") {
  const msg = history[index];
  if (!msg) return;
  msg.laws = laws || [];
  const session = getActiveSession();
  if (session) {
    session.lawPanel = {
      results: msg.laws,
      hintText: hintText || "提问后会在这里整理相关法条",
    };
    touchActiveSession();
  }
  const node = $("chat").querySelector(`.msg[data-index="${index}"] .msg-laws`);
  if (node) {
    node.innerHTML = renderLaws(msg.laws, showEmpty);
  }
  renderSessionList($("historySearch")?.value || "");
  updateLawPanel(msg.laws, showEmpty, hintText);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function updateLawPanel(results, showEmpty = false, hintText = "") {
  const panel = $("lawPanelList");
  const hint = $("lawPanelHint");
  if ((results || []).length > 0 && document.body.classList.contains("law-collapsed") && window.innerWidth > 1024) {
    setLawPanelCollapsed(false);
  }
  if (panel) {
    const emptyText = hintText && hintText.includes("不一定需要")
      ? "该问题可先按通用建议处理"
      : "暂未检索到直接匹配法条";
    panel.innerHTML = renderLaws(results || [], true, emptyText);
  }
  if (hint) {
    hint.textContent = hintText || "提问后会在这里整理相关法条";
  }
}

function buildContext(maxTurns = 6, maxLen = 900) {
  const items = [];
  for (let i = Math.max(0, history.length - maxTurns * 2); i < history.length; i += 1) {
    const m = history[i];
    if (!m || !m.text) continue;
    const role = m.role === "user" ? "用户" : "助手";
    items.push(`${role}: ${m.text}`);
  }
  let context = items.join("\n");
  if (context.length > maxLen) {
    context = context.slice(context.length - maxLen);
  }
  return context;
}

function renderLaws(results, showEmpty = false, emptyText = "未找到匹配法条") {
  if (!results.length) {
    return showEmpty ? `<div class="card empty">${emptyText}</div>` : "";
  }
  return results.map((r, idx) => {
    const rawText = r.text || "";
    const compactText = rawText.replace(/\s+/g, " ").trim();
    const snippet = compactText.length > 40 ? `${compactText.slice(0, 40)}...` : compactText;
    return `
      <details class="law-card"${idx === 0 ? " open" : ""}>
        <summary>
          <div class="law-title">
            <span class="law-name">${escapeHtml(r.law || "")}</span>
            <span class="law-article">${escapeHtml(r.article || "")}</span>
          </div>
          <div class="law-sub">
            <span class="law-tag">${r.article ? "条文" : "节选"}</span>
            <span class="law-snippet">${escapeHtml(snippet)}</span>
          </div>
        </summary>
        <div class="law-body">
          <pre class="law-full">${escapeHtml(rawText || "暂无条文内容")}</pre>
        </div>
      </details>
    `;
  }).join("");
}

async function streamSearch(q, context, assistantIndex) {
  try {
    const res = await fetch(
      `/search?stream=1&q=${encodeURIComponent(q)}&context=${encodeURIComponent(context)}`
    );
    if (!res.ok || !res.body) return false;

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let gotAnswer = false;
    let gotAny = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const lines = chunk.split("\n");
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const dataStr = line.slice(5).trim();
          if (!dataStr) continue;
          let payload;
          try {
            payload = JSON.parse(dataStr);
          } catch (err) {
            continue;
          }
          gotAny = true;
          if (payload.type === "status") {
            setStatus(payload.message || "", "info");
          } else if (payload.type === "answer_delta") {
            updateAssistantText(assistantIndex, payload.delta || "");
            gotAnswer = true;
          } else if (payload.type === "laws") {
            const count = payload.count ?? (payload.results || []).length;
            const legal = payload.legal_query === true;
            const recommend = payload.recommend !== false && count > 0;
            if (recommend) {
              setStatus(`找到了 ${count} 条相关法条`, "success");
            } else if (legal) {
              setStatus("未找到匹配法条", "warn");
            } else {
              setStatus("这条问题不一定需要引用法条", "info");
            }
            const hintText = recommend
              ? `已整理 ${count} 条法条，右侧可展开查看详情。`
              : (legal ? "当前没有直接匹配法条，可补充时间地点和证据细节。" : "该问题不一定需要法条，可先按通用建议处理。");
            updateAssistantLaws(
              assistantIndex,
              recommend ? (payload.results || []) : [],
              recommend || legal,
              hintText
            );
          } else if (payload.type === "done") {
            const msg = history[assistantIndex];
            if (!gotAnswer && msg && !msg.text.trim()) {
              updateAssistantText(assistantIndex, "暂时没能生成完整答复，我再试试。");
            }
            return true;
          }
        }
      }
    }
    return gotAny;
  } catch (err) {
    return false;
  }
}

async function search() {
  const q = $("query").value.trim();
  if (!q) {
    setStatus("先说说你的情况吧", "warn");
    return;
  }
  $("query").value = "";
  setStatus("正在帮你查找相关法条...", "info");
  const context = buildContext();
  const session = getActiveSession();
  if (session && (!session.title || session.title === "新聊天")) {
    session.title = q.length > 18 ? `${q.slice(0, 18)}...` : q;
    touchActiveSession();
    renderSessionList($("historySearch")?.value || "");
  }
  const userIndex = addMessage("user", q);
  const assistantIndex = addMessage("assistant", "");
  const pending = $("chat").querySelector(`.msg[data-index="${assistantIndex}"] .bubble`);
  if (pending) pending.classList.add("pending");

  const streamed = await streamSearch(q, context, assistantIndex);
  if (streamed) return;

  const res = await fetch(
    `/search?q=${encodeURIComponent(q)}&context=${encodeURIComponent(context)}`
  );
  const data = await res.json();
  const legal = data.legal_query === true;
  const recommend = data.recommend_laws !== false && data.count > 0;
  if (recommend) {
    setStatus(`找到了 ${data.count} 条相关法条`, "success");
  } else if (legal) {
    setStatus("未找到匹配法条", "warn");
  } else {
    setStatus("这条问题不一定需要引用法条", "info");
  }
  const hintText = recommend
    ? `已整理 ${data.count} 条法条，右侧可展开查看详情。`
    : (legal ? "当前没有直接匹配法条，可补充时间地点和证据细节。" : "该问题不一定需要法条，可先按通用建议处理。");
  updateAssistantText(assistantIndex, data.answer || "暂时没能生成完整答复，我再试试。");
  updateAssistantLaws(
    assistantIndex,
    recommend ? (data.results || []) : [],
    recommend || legal,
    hintText
  );
}

const sendBtn = document.getElementById("send");
if (sendBtn) sendBtn.addEventListener("click", search);
$("query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") search();
});

function setSidebarOpen(open) {
  document.body.classList.toggle("sidebar-open", Boolean(open));
}

function setStatus(text, level = "info") {
  const node = $("status");
  if (!node) return;
  node.textContent = text;
  node.classList.remove("info", "success", "warn");
  node.classList.add(level);
}

function setSidebarCollapsed(collapsed) {
  const value = Boolean(collapsed);
  document.body.classList.toggle("sidebar-collapsed", value);
  const btn = $("toggleSidebarDesktop");
  if (btn) {
    btn.textContent = value ? "展开左栏" : "收起左栏";
    btn.setAttribute("aria-label", value ? "展开左侧栏" : "收起左侧栏");
  }
  try {
    localStorage.setItem(sidebarCollapsedKey, value ? "1" : "0");
  } catch (err) {
    // ignore storage failure
  }
}

function setLawPanelCollapsed(collapsed) {
  const value = Boolean(collapsed);
  document.body.classList.toggle("law-collapsed", value);
  const btn = $("toggleLawPanel");
  if (btn) {
    btn.textContent = value ? "展开法条" : "收起法条";
    btn.setAttribute("aria-label", value ? "展开法条面板" : "收起法条面板");
  }
  try {
    localStorage.setItem(lawPanelCollapsedKey, value ? "1" : "0");
  } catch (err) {
    // ignore storage failure
  }
}

const toggleSidebar = document.getElementById("toggleSidebar");
if (toggleSidebar) {
  toggleSidebar.addEventListener("click", () => {
    const isOpen = document.body.classList.contains("sidebar-open");
    setSidebarOpen(!isOpen);
  });
}

const toggleSidebarDesktop = document.getElementById("toggleSidebarDesktop");
if (toggleSidebarDesktop) {
  toggleSidebarDesktop.addEventListener("click", () => {
    const isCollapsed = document.body.classList.contains("sidebar-collapsed");
    setSidebarCollapsed(!isCollapsed);
  });
}

const toggleLawPanel = document.getElementById("toggleLawPanel");
if (toggleLawPanel) {
  toggleLawPanel.addEventListener("click", () => {
    const isCollapsed = document.body.classList.contains("law-collapsed");
    setLawPanelCollapsed(!isCollapsed);
  });
}

document.addEventListener("click", (event) => {
  if (!document.body.classList.contains("sidebar-open")) return;
  const sidebar = document.querySelector(".sidebar");
  const isToggle = event.target && event.target.id === "toggleSidebar";
  if (sidebar && !sidebar.contains(event.target) && !isToggle) {
    setSidebarOpen(false);
  }
});

$("fill").addEventListener("click", async () => {
  setStatus("正在准备示例...", "info");
  const samples = await loadSamples();
  if (!samples.length) {
    setStatus("示例没能加载成功，请稍后再试", "warn");
    return;
  }
  const pick = samples[sampleCursor % samples.length];
  sampleCursor += 1;
  $("query").value = pick;
  setStatus("已填好示例，可以直接提问", "success");
});

$("newChat").addEventListener("click", () => {
  const session = createSession("新聊天");
  chatSessions.push(session);
  switchSession(session.id);
  setStatus("已创建新聊天", "success");
  $("query").value = "";
  setSidebarOpen(false);
});

const clearChat = document.getElementById("clearChat");
if (clearChat) {
  clearChat.addEventListener("click", () => {
    const session = getActiveSession();
    if (!session) return;
    session.messages = [];
    session.lawPanel = { results: [], hintText: "提问后会在这里整理相关法条" };
    session.title = "新聊天";
    session.updatedAt = Date.now();
    renderChatFromActiveSession();
    renderSessionList($("historySearch")?.value || "");
    setStatus("当前会话已清空", "success");
  });
}

const historySearch = document.getElementById("historySearch");
if (historySearch) {
  historySearch.addEventListener("input", () => {
    renderSessionList(historySearch.value);
  });
}

renderQuickGrid();
const initialSession = createSession("新聊天");
chatSessions.push(initialSession);
switchSession(initialSession.id);
try {
  setSidebarCollapsed(localStorage.getItem(sidebarCollapsedKey) === "1");
  setLawPanelCollapsed(localStorage.getItem(lawPanelCollapsedKey) === "1");
} catch (err) {
  setSidebarCollapsed(false);
  setLawPanelCollapsed(false);
}
setStatus("准备就绪，可以开始咨询", "info");

const voiceBtn = document.getElementById("voiceBtn");
const voiceStatus = document.getElementById("voiceStatus");
if (voiceBtn && voiceStatus) {
  if (!window.ASRHelper || !ASRHelper.isSupported()) {
    voiceStatus.textContent = "语音识别：当前浏览器不支持";
    voiceBtn.disabled = true;
  } else {
    const asr = new ASRHelper({
      onStart: () => {
        voiceBtn.classList.add("recording");
        voiceBtn.setAttribute("aria-label", "停止语音识别");
        voiceStatus.textContent = "语音识别：正在聆听...";
      },
      onResult: (data) => {
        if (data.interimText) {
          voiceStatus.textContent = `识别中：${data.interimText}`;
        }
        if (data.isFinal) {
          $("query").value = data.finalText.trim();
          voiceStatus.textContent = "语音识别：已完成，可点击提问";
          $("query").focus();
        }
      },
      onEnd: () => {
        voiceBtn.classList.remove("recording");
        voiceBtn.setAttribute("aria-label", "开始语音识别");
      },
      onError: (err) => {
        voiceBtn.classList.remove("recording");
        voiceBtn.setAttribute("aria-label", "开始语音识别");
        voiceStatus.textContent = `语音识别出错：${err}`;
      },
    });

    voiceBtn.addEventListener("click", () => {
      if (asr.isListening) {
        asr.stop();
      } else {
        asr.start();
      }
    });
  }
}
