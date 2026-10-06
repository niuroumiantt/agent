"use strict";
(() => {
  const session = document.querySelector('meta[name="agent-session"]')?.content || "";
  const $ = id => document.getElementById(id);
  const list = data => Array.isArray(data) ? data : [];
  const node = (tag, className = "", text) => {
    const el = document.createElement(tag); el.className = className;
    if (text !== undefined) el.textContent = String(text ?? "");
    return el;
  };
  const state = { config:null, files:[], selected:new Set(), conversations:[], current:null,
    jobs:[], submitting:false, uploading:false, timer:null, fingerprint:"", request:0 };
  const active = job => ["queued", "running"].includes(job?.status);
  const busy = () => list(state.current?.messages).some(m => active(m) || active(m.job));
  const labels = { queued:"等待执行", running:"分析中", completed:"已完成", partial:"部分完成", cancelled:"已停止", failed:"未完成", interrupted:"已中断" };
  const welcome = $("welcome").cloneNode(true);
  const formatSize = n => n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;

  async function api(path, options = {}) {
    if (!session || session === "__SESSION_TOKEN__") throw new Error("请刷新页面或重新登录。");
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin) throw new Error("请求地址不属于当前工作台。");
    const headers = { "X-Agent-Session":session };
    if (options.raw !== undefined) headers["Content-Type"] = "application/octet-stream";
    else if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { method:options.method || "GET", headers,
      credentials:"same-origin", redirect:"error", cache:"no-store",
      ...(options.raw !== undefined ? {body:options.raw} : options.body !== undefined ? {body:JSON.stringify(options.body)} : {}) });
    let data; try { data = await response.json(); } catch { throw new Error("服务响应无法读取，请刷新页面或重新登录。"); }
    if (!response.ok) {
      const detail = data.detail || data.error || `请求未完成（${response.status}）`;
      const error = new Error(Array.isArray(detail) ? detail.map(x => x.msg).join("；") : String(detail));
      error.status = response.status; throw error;
    }
    return data;
  }
  function notice(text = "", error = false) {
    $("notice").textContent = text; $("notice").classList.toggle("hidden", !text);
    $("notice").classList.toggle("error", error);
  }
  function showSidebar(show) {
    $("sidebar").classList.toggle("open", show); $("sidebar-backdrop").classList.toggle("hidden", !show);
    $("open-sidebar").setAttribute("aria-expanded", String(show));
  }
  function selection() {
    const names = state.files.filter(f => state.selected.has(f.id));
    const chips = names.slice(0, 20).map(file => {
      const chip = node("span", "attachment"); chip.append(node("span", "", file.name));
      const remove = node("button", "", "×"); remove.type = "button";
      remove.setAttribute("aria-label", `取消选择 ${file.name}`);
      remove.onclick = () => { state.selected.delete(file.id); renderFiles(); };
      chip.append(remove); return chip;
    });
    if (names.length > 20) chips.push(node("span", "attachment", `另有 ${names.length - 20} 份`));
    $("attachments").replaceChildren(...chips);
    $("context-label").textContent = names.length ? `已选择 ${names.length} 份文件` : "未选择文件 · 可以直接对话";
    const supported = state.files.filter(f => f.supported);
    $("select-all").checked = supported.length > 0 && supported.every(f => state.selected.has(f.id));
    $("select-all").indeterminate = names.length > 0 && !$("select-all").checked;
    $("send-button").disabled = state.submitting || state.uploading || !state.config || !$("message-input").value.trim();
    $("stop-button").classList.toggle("hidden", !busy());
  }
  function renderFiles() {
    const rows = state.files.map(file => {
      const row = node("label", `file-row${state.selected.has(file.id) ? " selected" : ""}`);
      const check = node("input"); check.type = "checkbox"; check.checked = state.selected.has(file.id);
      check.disabled = !file.supported; check.setAttribute("aria-label", `选择 ${file.name}`);
      check.onchange = () => { if (check.checked) state.selected.add(file.id); else state.selected.delete(file.id); row.classList.toggle("selected", check.checked); selection(); };
      const info = node("span", "file-info");
      info.append(node("span", "file-name", file.name), node("span", "file-meta", file.supported ? formatSize(file.size) : "当前不可读取"));
      info.title = file.relative_path || file.name;
      row.append(check, node("span", "file-type", file.extension?.slice(1).toUpperCase() || "TXT"), info); return row;
    });
    $("file-list").replaceChildren(...(rows.length ? rows : [node("p", "sidebar-empty", state.config?.mode === "server" ? "把文件拖入对话，或点击输入框旁的 ＋ 上传。" : "发送“扫描文件”，读取已授权的目录。") ]));
    selection();
  }
  function applyFiles(data) {
    state.files = list(data.files);
    state.selected = new Set([...state.selected].filter(id => state.files.some(f => f.id === id && f.supported)));
    renderFiles();
    if (list(data.warnings).length) notice("部分文件未列出或无法读取，请核对文件范围。");
  }
  function renderConversations() {
    const entries = state.conversations.map(c => {
      const button = node("button", `conversation-button${c.id === state.current?.id ? " active" : ""}`, c.title);
      button.type = "button"; button.title = c.title; button.onclick = () => openConversation(c.id);
      if (c.id === state.current?.id) button.setAttribute("aria-current", "page"); return button;
    });
    $("conversation-list").replaceChildren(...(entries.length ? entries : [node("p", "sidebar-empty", "每次讨论都会保存在这里。") ]));
  }
  async function refreshList() {
    const data = await api("/api/conversations"); state.conversations = list(data.conversations); renderConversations();
  }
  function remember(id) { try { if (id) localStorage.setItem("agent-conversation", id); else localStorage.removeItem("agent-conversation"); } catch { /* Storage is optional. */ } }
  async function openConversation(id) {
    const request = ++state.request;
    try {
      const conversation = await api(`/api/conversations/${encodeURIComponent(id)}`);
      if (request !== state.request) return;
      state.current = conversation; state.fingerprint = ""; remember(id);
      const last = [...conversation.messages].reverse().find(m => m.role === "user");
      state.selected = new Set(list(last?.file_ids).filter(fileId => state.files.some(f => f.id === fileId && f.supported)));
      renderFiles(); renderThread(true); renderConversations(); schedule(); showSidebar(false);
    } catch (error) { notice(error.message, true); }
  }
  async function newConversation(jobId) {
    const request = ++state.request;
    const conversation = await api("/api/conversations", {method:"POST", body:jobId ? {job_id:jobId} : {}});
    if (request !== state.request) return conversation;
    state.current = conversation; state.selected.clear(); state.fingerprint = ""; remember(conversation.id);
    renderFiles(); renderThread(true); await refreshList(); schedule(); showSidebar(false); $("message-input").focus();
    return conversation;
  }
  function details(parent, title, key) {
    const wrap = node("details", "report-details"); wrap.dataset.detail = key;
    wrap.append(node("summary", "", title)); parent.append(wrap); return wrap;
  }
  async function download(artifact, button) {
    button.disabled = true;
    try {
      const url = new URL(artifact.url, location.origin);
      if (url.origin !== location.origin || !/^\/api\/jobs\/[0-9a-f]{32}\/artifacts\//.test(url.pathname)) throw new Error("报告地址不属于当前任务。");
      const response = await fetch(url, {headers:{"X-Agent-Session":session}, credentials:"same-origin", redirect:"error"});
      if (!response.ok) throw new Error(`报告下载未完成（${response.status}）。`);
      const blob = await response.blob(); const object = URL.createObjectURL(blob);
      const link = node("a"); link.href = object; link.download = artifact.name; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(object), 30000);
    } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
  }
  function artifacts(parent, values) {
    if (!list(values).length) return;
    const wrap = node("div", "artifact-list");
    for (const artifact of values) {
      const types = {docx:"Word", xlsx:"Excel", csv:"CSV", md:"Markdown", json:"JSON"};
      const extension = artifact.name?.split(".").pop();
      const button = node("button", "artifact-button", `↓ ${types[extension] || artifact.name}`);
      button.type = "button"; button.title = artifact.name; button.onclick = () => download(artifact, button); wrap.append(button);
    }
    parent.append(wrap);
  }
  function jobContent(parent, job, messageId) {
    const stats = job.progress || {};
    const progress = node("div", "job-progress"); progress.append(node("strong", "", labels[job.status] || job.status));
    if (stats.files_total) {
      progress.append(node("p", "", `已处理 ${stats.files_done || 0} / ${stats.files_total} 份文件`));
      const meter = node("progress"); meter.max = stats.files_total; meter.value = stats.files_done || 0;
      meter.setAttribute("aria-label", "文件分析进度"); progress.append(meter);
    }
    if (active(job) && stats.current_file) progress.append(node("p", "", stats.phase === "summarizing" ? "正在综合各段分析…" : `${stats.current_file}${stats.segments_total ? ` · 分段 ${stats.current_segment}/${stats.segments_total}` : ""}`));
    parent.append(progress);
    if (job.error) parent.append(node("div", "notice error", job.error));
    const result = job.result;
    if (result) {
      if (!active(job)) parent.append(node("p", "report-summary", result.summary));
      const warnings = [...new Set(list(result.warnings))];
      if (warnings.some(w => /候选事实/.test(w))) parent.append(node("div", "notice", "部分引用尚未匹配到原文，已在引用中标记，请核对后再使用。"));
      if (job.status === "partial" || list(result.sources).some(s => s.analysis_status !== "completed")) {
        if (!active(job)) parent.append(node("div", "notice", "部分材料未完整分析，请查看下面的读取范围。"));
      }
      if (list(result.recommendations).length && !active(job)) {
        const ul = node("ul", "recommendations"); result.recommendations.forEach(r => ul.append(node("li", "", r))); parent.append(ul);
      }
      if (list(result.facts).length) {
        const wrap = details(parent, `原文引用 · ${result.facts.length}`, `${messageId}-facts`);
        for (const fact of result.facts) {
          const citation = node("section", "citation");
          const source = list(result.sources).find(s => s.source_id === fact.source_id);
          citation.append(node("small", "", `${source?.file?.name || fact.source_id} · ${fact.locator}`), node("p", "", fact.claim), node("blockquote", "", fact.quote), node("small", fact.verified ? "" : "unverified", fact.verified ? "原文片段已匹配" : "引用待核对"));
          wrap.append(citation);
        }
      }
      const sources = details(parent, "文件与读取范围", `${messageId}-sources`);
      for (const source of list(result.sources)) sources.append(node("p", "source-coverage", `${source.file?.name || source.source_id} · ${source.segments_done || 0}/${source.segments_total || 0} 段${list(source.warnings).length ? " · 提取或分析范围有提示，请核对原文件" : ""}`));
    }
    artifacts(parent, job.artifacts);
  }
  function renderThread(forceBottom = false) {
    const conversation = state.current; $("chat-title").textContent = conversation?.title || "新对话";
    const signature = JSON.stringify([conversation?.id, conversation?.updated_at, list(conversation?.messages).map(m => [m.id,m.status,m.updated_at,m.job?.updated_at])]);
    if (signature === state.fingerprint) { selection(); return; }
    state.fingerprint = signature;
    const thread = $("thread"); const bottom = forceBottom || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 110;
    const position = thread.scrollTop;
    const opened = new Set([...thread.querySelectorAll("details[open]")].map(d => d.dataset.detail));
    const messages = list(conversation?.messages).map(message => {
      const row = node("article", `message ${message.role}`); row.dataset.messageId = message.id;
      row.append(node("span", "message-avatar", message.role === "assistant" ? "g" : "你"));
      const content = node("div", "message-content"); content.append(node("div", "message-role", message.role === "assistant" ? "Glocal Agent" : "你"));
      const text = node("p", `message-text${active(message) && !message.job ? " thinking" : ""}`, message.content || "正在等待执行…"); content.append(text);
      if (message.role === "user" && list(message.file_ids).length) {
        const files = node("div", "message-files");
        for (const id of message.file_ids.slice(0, 20)) files.append(node("span", "message-file", state.files.find(f => f.id === id)?.name || "已选文件"));
        content.append(files);
      }
      if (message.job) jobContent(content, message.job, message.id);
      if (message.status === "failed" || message.status === "interrupted") text.classList.add("notice", "error");
      artifacts(content, message.metadata?.artifacts);
      for (const preview of list(message.metadata?.previews)) {
        const panel = details(content, preview.file.name, `${message.id}-${preview.file.id}`);
        if (preview.extraction.status !== "ok") panel.append(node("div", "notice", "该预览没有完整提取文件内容，请核对原件。"));
        for (const block of list(preview.extraction.blocks)) { const entry = node("div", "preview-block"); entry.append(node("small", "", block.locator), node("pre", "", block.text)); panel.append(entry); }
        panel.append(node("p", "source-coverage", `预览 ${preview.extraction.blocks.length} / ${preview.blocks_total} 个原文块`));
      }
      row.append(content); return row;
    });
    thread.replaceChildren(...(messages.length ? messages : [welcome.cloneNode(true)]));
    for (const el of thread.querySelectorAll("details")) el.open = opened.has(el.dataset.detail);
    for (const button of thread.querySelectorAll("[data-message]")) button.onclick = () => send(button.dataset.message);
    selection(); if (bottom) thread.scrollTop = thread.scrollHeight; else thread.scrollTop = position;
  }
  function newCatalog(result, previous) {
    const known = new Set(list(previous?.messages).filter(m => m.metadata?.catalog).map(m => m.id));
    const updated = list(result.messages).filter(m => m.metadata?.catalog && !known.has(m.id)).pop();
    if (updated) applyFiles(updated.metadata.catalog);
  }
  function schedule(delay = 1800) {
    clearTimeout(state.timer);
    if (busy()) state.timer = setTimeout(poll, delay);
  }
  async function poll() {
    const id = state.current?.id; if (!id) return;
    let delay = 1800;
    try {
      const conversation = await api(`/api/conversations/${encodeURIComponent(id)}`);
      if (state.current?.id !== id) return;
      newCatalog(conversation, state.current); state.current = conversation; renderThread();
      if (!busy()) await refreshList();
    } catch (error) {
      notice(`状态暂时无法更新：${error.message}`, true); delay = 5000;
      if ([401,403].includes(error.status)) return;
    }
    schedule(delay);
  }
  async function send(text) {
    const content = String(text ?? $("message-input").value).trim();
    if (!content || state.submitting || state.uploading) return;
    state.submitting = true; notice(); selection();
    const original = $("message-input").value;
    const ids = [...state.selected];
    try {
      const existing = state.current;
      const conversation = existing || await newConversation();
      if (!existing) { state.selected = new Set(ids); renderFiles(); }
      const id = conversation.id;
      const result = await api(`/api/conversations/${encodeURIComponent(id)}/messages`, {method:"POST", body:{content,file_ids:ids}});
      if (state.current?.id === id) {
        newCatalog(result, state.current); state.current = result;
        if ($("message-input").value === original) $("message-input").value = "";
        state.fingerprint = ""; renderThread(true); schedule();
      }
      await refreshList();
    } catch (error) { notice(error.message, true); }
    finally { state.submitting = false; selection(); $("message-input").focus(); }
  }
  async function upload(files) {
    if (!files.length || state.uploading) return;
    if (state.config?.mode !== "server") { notice("本机模式请将文件放到已授权目录，再发送“扫描文件”。"); return; }
    state.uploading = true; selection(); let done = 0;
    try {
      for (const file of files) {
        notice(`正在上传 ${file.name}…`);
        const result = await api(`/api/uploads?name=${encodeURIComponent(file.name)}`, {method:"POST",raw:file});
        applyFiles(result); const added = state.files.find(f => f.name === file.name);
        if (added?.supported) state.selected.add(added.id); done++;
      }
      notice(`已上传 ${done} 份文件。告诉我你希望怎样处理它们。`); renderFiles();
    } catch (error) { notice(`${done ? `已上传 ${done} 份。` : ""}${error.message}`, true); renderFiles(); }
    finally { state.uploading = false; $("upload-files").value = ""; selection(); }
  }
  $("new-chat").onclick = () => newConversation().catch(e => notice(e.message, true));
  $("select-all").onchange = event => { state.selected = new Set(event.target.checked ? state.files.filter(f => f.supported).map(f => f.id) : []); renderFiles(); };
  $("send-button").onclick = () => send(); $("stop-button").onclick = () => send("停止当前任务");
  $("message-input").oninput = selection;
  $("message-input").onkeydown = event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } };
  $("attach-button").onclick = () => state.config?.mode === "server" ? $("upload-files").click() : send("扫描文件");
  $("upload-files").onchange = event => upload([...event.target.files]);
  $("open-sidebar").onclick = () => showSidebar(true); $("close-sidebar").onclick = () => showSidebar(false); $("sidebar-backdrop").onclick = () => showSidebar(false);
  document.addEventListener("keydown", event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); newConversation().catch(e => notice(e.message, true)); } if (event.key === "Escape") showSidebar(false); });
  $("chat-workspace").ondragover = event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); $("composer").classList.add("dragging"); } };
  $("chat-workspace").ondragleave = event => { if (!$("chat-workspace").contains(event.relatedTarget)) $("composer").classList.remove("dragging"); };
  $("chat-workspace").ondrop = event => { event.preventDefault(); $("composer").classList.remove("dragging"); upload([...event.dataTransfer.files]); };
  (async () => {
    try {
      state.config = await api("/api/config");
      $("model-name").textContent = `${state.config.model} · ${state.config.configured ? "已配置" : "未配置"}`;
      $("composer-model").textContent = state.config.model;
      $("model-dot").classList.toggle("configured", state.config.configured);
      $("office-apps").classList.toggle("hidden", state.config.mode !== "server");
      if (state.config.mode !== "server") { $("attach-button").title = "扫描授权目录"; $("attach-button").setAttribute("aria-label", "扫描授权目录"); }
      applyFiles(await api("/api/scan", {method:"POST", body:{recursive:false}}));
      await refreshList();
      const jobs = await api("/api/jobs"); state.jobs = list(jobs.jobs); $("job-count").textContent = state.jobs.length;
      $("job-list").replaceChildren(...state.jobs.map(job => { const button = node("button", "job-button", `${labels[job.status] || job.status} · ${job.instruction}`); button.type = "button"; button.onclick = () => newConversation(job.id).catch(e => notice(e.message, true)); return button; }));
      let remembered; try { remembered = localStorage.getItem("agent-conversation"); } catch { /* No storage. */ }
      if (state.conversations.some(c => c.id === remembered)) await openConversation(remembered);
      else { remember(null); renderThread(true); }
    } catch (error) { notice(error.message, true); }
    selection();
  })();
})();
