const $ = (id) => document.getElementById(id);
const history = [];
let sampleCache = null;
let sampleCursor = 0;

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
  }
  $("chat").scrollTop = $("chat").scrollHeight;
}

function updateAssistantLaws(index, laws) {
  const msg = history[index];
  if (!msg) return;
  msg.laws = laws || [];
  const node = $("chat").querySelector(`.msg[data-index="${index}"] .msg-laws`);
  if (node) {
    node.innerHTML = renderLaws(msg.laws, true);
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
            $("status").textContent = `找到 ${count} 条相关法条`;
            updateAssistantLaws(assistantIndex, payload.results || []);
          } else if (payload.type === "done") {
            const msg = history[assistantIndex];
            if (!gotAnswer && msg && !msg.text.trim()) {
              updateAssistantText(assistantIndex, "未能生成答复。");
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
    $("status").textContent = "请输入咨询内容";
    return;
  }
  $("query").value = "";
  $("status").textContent = "检索中...";
  const context = buildContext();
  const userIndex = addMessage("user", q);
  addHistoryEntry(q, userIndex);
  const assistantIndex = addMessage("assistant", "");

  const streamed = await streamSearch(q, context, assistantIndex);
  if (streamed) return;

  const res = await fetch(
    `/search?q=${encodeURIComponent(q)}&context=${encodeURIComponent(context)}`
  );
  const data = await res.json();
  $("status").textContent = `找到 ${data.count} 条相关法条`;
  updateAssistantText(assistantIndex, data.answer || "未能生成答复。");
  updateAssistantLaws(assistantIndex, data.results || []);
}

$("search").addEventListener("click", search);
$("query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") search();
});

$("fill").addEventListener("click", async () => {
  $("status").textContent = "载入示例中...";
  const samples = await loadSamples();
  if (!samples.length) {
    $("status").textContent = "示例加载失败，请稍后再试";
    return;
  }
  const pick = samples[sampleCursor % samples.length];
  sampleCursor += 1;
  $("query").value = pick;
  $("status").textContent = "已填充示例，可直接查询";
});

$("newChat").addEventListener("click", () => {
  history.length = 0;
  $("chat").innerHTML = "";
  $("historyList").innerHTML = "";
  $("status").textContent = "已开启新对话";
  $("query").value = "";
});
