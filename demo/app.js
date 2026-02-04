const $ = (id) => document.getElementById(id);
const history = [];
let sampleCache = null;
let sampleCursor = 0;
let hasConversationTitle = false;
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

function addMessage(role, text) {
  const msg = { role, text, laws: [] };
  history.push(msg);
  const node = renderMessage(msg, history.length - 1);
  $("chat").appendChild(node);
  $("chat").scrollTop = $("chat").scrollHeight;
  return history.length - 1;
}

function addHistoryEntry(text, targetIndex) {
  const item = document.createElement("div");
  item.className = "history-item";
  item.textContent = text.length > 36 ? `${text.slice(0, 36)}...` : text;
  item.dataset.target = String(targetIndex);
  item.addEventListener("click", () => {
    const target = $("chat").querySelector(`.msg[data-index="${targetIndex}"]`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      markActiveHistory(item);
    }
    setSidebarOpen(false);
  });
  $("historyList").prepend(item);
  markActiveHistory(item);
}

function markActiveHistory(activeItem) {
  const items = $("historyList").querySelectorAll(".history-item");
  items.forEach((el) => el.classList.remove("active"));
  activeItem.classList.add("active");
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
  text.textContent = msg.text || "";

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
    node.textContent = msg.text;
    node.parentElement?.classList.remove("pending");
  }
  $("chat").scrollTop = $("chat").scrollHeight;
}

function updateAssistantLaws(index, laws, showEmpty = true) {
  const msg = history[index];
  if (!msg) return;
  msg.laws = laws || [];
  const node = $("chat").querySelector(`.msg[data-index="${index}"] .msg-laws`);
  if (node) {
    node.innerHTML = renderLaws(msg.laws, showEmpty);
  }
  $("chat").scrollTop = $("chat").scrollHeight;
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

function renderLaws(results, showEmpty = false) {
  if (!results.length) {
    return showEmpty ? "<div class=\"card empty\">未找到匹配法条</div>" : "";
  }
  return results.map((r) => {
    const rawText = r.text || "";
    const compactText = rawText.replace(/\s+/g, " ").trim();
    const snippet = compactText.length > 40 ? `${compactText.slice(0, 40)}...` : compactText;
    const encoded = encodeURIComponent(rawText);
    return `
      <details class="law-card" data-full="${encoded}">
        <summary>
          <div class="law-title">
            <span class="law-name">${r.law || ""}</span>
            <span class="law-article">${r.article || ""}</span>
          </div>
          <div class="law-sub">
            <span class="law-tag">${r.article ? "条文" : "节选"}</span>
            <span class="law-snippet">${snippet}</span>
          </div>
        </summary>
        <div class="law-body">
          <div class="law-full"></div>
        </div>
      </details>
    `;
  }).join("");
}

function ensureLawBody(details) {
  if (!details || details.dataset.loaded === "1") return;
  const encoded = details.dataset.full || "";
  const target = details.querySelector(".law-full");
  if (!target) return;
  try {
    target.textContent = decodeURIComponent(encoded);
  } catch (err) {
    target.textContent = encoded;
  }
  details.dataset.loaded = "1";
}

document.addEventListener("toggle", (event) => {
  const details = event.target;
  if (!details || !details.classList || !details.classList.contains("law-card")) return;
  if (details.open) {
    ensureLawBody(details);
  }
}, true);

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
            $("status").textContent = payload.message || "";
          } else if (payload.type === "answer_delta") {
            updateAssistantText(assistantIndex, payload.delta || "");
            gotAnswer = true;
          } else if (payload.type === "laws") {
            const count = payload.count ?? (payload.results || []).length;
            const legal = payload.legal_query === true;
            const recommend = payload.recommend !== false && count > 0;
            if (recommend) {
              $("status").textContent = `找到了 ${count} 条相关法条`;
            } else if (legal) {
              $("status").textContent = "未找到匹配法条";
            } else {
              $("status").textContent = "这条问题不一定需要引用法条";
            }
            updateAssistantLaws(
              assistantIndex,
              recommend ? (payload.results || []) : [],
              recommend || legal
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
    $("status").textContent = "先说说你的情况吧";
    return;
  }
  $("query").value = "";
  $("status").textContent = "正在帮你查找相关法条...";
  const context = buildContext();
  const userIndex = addMessage("user", q);
  if (!hasConversationTitle) {
    addHistoryEntry(q, userIndex);
    hasConversationTitle = true;
  }
  const assistantIndex = addMessage("assistant", "我正在查找相关法条，请稍候…");
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
    $("status").textContent = `找到了 ${data.count} 条相关法条`;
  } else if (legal) {
    $("status").textContent = "未找到匹配法条";
  } else {
    $("status").textContent = "这条问题不一定需要引用法条";
  }
  updateAssistantText(assistantIndex, data.answer || "暂时没能生成完整答复，我再试试。");
  updateAssistantLaws(
    assistantIndex,
    recommend ? (data.results || []) : [],
    recommend || legal
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

const toggleSidebar = document.getElementById("toggleSidebar");
if (toggleSidebar) {
  toggleSidebar.addEventListener("click", () => {
    const isOpen = document.body.classList.contains("sidebar-open");
    setSidebarOpen(!isOpen);
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
  $("status").textContent = "正在准备示例...";
  const samples = await loadSamples();
  if (!samples.length) {
    $("status").textContent = "示例没能加载成功，请稍后再试";
    return;
  }
  const pick = samples[sampleCursor % samples.length];
  sampleCursor += 1;
  $("query").value = pick;
  $("status").textContent = "已填好示例，可以直接提问";
});

$("newChat").addEventListener("click", () => {
  history.length = 0;
  $("chat").innerHTML = "";
  $("historyList").innerHTML = "";
  $("status").textContent = "已重新开始对话";
  $("query").value = "";
  hasConversationTitle = false;
  setSidebarOpen(false);
});

const clearChat = document.getElementById("clearChat");
if (clearChat) {
  clearChat.addEventListener("click", () => {
    history.length = 0;
    $("chat").innerHTML = "";
    $("historyList").innerHTML = "";
    hasConversationTitle = false;
    $("status").textContent = "对话已清空";
  });
}

const historySearch = document.getElementById("historySearch");
if (historySearch) {
  historySearch.addEventListener("input", () => {
    const q = historySearch.value.trim();
    const items = $("historyList").querySelectorAll(".history-item");
    items.forEach((item) => {
      if (!q) {
        item.style.display = "";
      } else {
        const match = item.textContent?.includes(q);
        item.style.display = match ? "" : "none";
      }
    });
  });
}

renderQuickGrid();

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
