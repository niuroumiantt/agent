"use strict";

(() => {
  const session = document.querySelector('meta[name="agent-session"]')?.content || "";
  const state = { config: null, files: [], selected: new Set(), jobs: [], currentFile: null, currentJob: null, previewRequest: 0, jobRequest: 0, pollTimer: null, refreshing: null, submitting: false };
  const $ = (id) => document.getElementById(id);
  const value = (input) => typeof input === "string" ? input : input == null ? "" : String(input);
  const list = (input) => Array.isArray(input) ? input : [];
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = value(text);
    return element;
  };
  const jobLabels = { queued: "等待执行", running: "分析中", completed: "已完成", partial: "部分完成", cancelled: "已停止", failed: "执行失败", interrupted: "已中断" };
  const analysisLabels = { completed: "已分析", partial: "部分分析", skipped: "已跳过", failed: "分析失败", pending: "待处理", analyzing: "分析中", not_processed: "未处理" };
  const extractionLabels = { blocked: "已保护", pending: "未读取", ok: "已提取", ready: "已提取", completed: "已提取", partial: "部分提取", unsupported: "无法提取", needs_ocr: "需要 OCR", empty: "无可读文本", error: "提取失败", failed: "提取失败" };
  const extractorLabels = { plain_text: "文本内容", delimited_text: "表格文本", pypdf: "PDF 文本", "python-docx": "Word 内容", openpyxl: "Excel 单元格", pptx_xml: "PPT 文本", html_text: "HTML 正文" };
  const warningLabels = {
    sensitive_file_blocked: "凭据或私钥文件默认禁止读取，不会发送给模型。",
    credential_content_detected: "内容含明显凭据标记，已阻止预览和模型分析。",
    file_too_large: "文件超过 15 MiB 读取上限。",
    rescan_required: "扫描后文件发生变化或无法访问，请重新扫描再提交。",
    unsafe_path: "文件路径已变化或存在不安全链接，拒绝读取。",
    scan_required: "请先扫描授权目录。",
    unknown_file: "文件不在当前扫描结果中，请重新扫描。",
    model_analysis_failed: "该文件的模型分析未完成。",
    segments_incomplete: "部分分段未完成模型分析，请核对分段进度。",
    not_processed: "任务已停止或失败，此文件尚未处理。",
    summary_based_on_segment_notes: "综合摘要根据各段的分析摘要归纳；原文引用请查看下方分段结果。",
    summary_generation_failed: "综合摘要未完成，已保留逐文件和分段分析。",
    summary_length_limit_reached: "综合归纳的一部分中间摘要超过长度上限，完整分段结果保留在报告中。",
    scan_limit_reached: "已达到 2000 份文件扫描上限，尚有文件未列出。",
    directory_limit_reached: "达到子目录扫描上限，尚有目录未扫描。",
    directory_entry_limit_reached: "目录条目过多，扫描结果可能不完整。",
    unsafe_entries_skipped: "已跳过符号链接和特殊文件。",
    unreadable_entries_skipped: "部分文件或目录无法访问，已跳过。",
    character_limit_reached: "文本超过读取上限，只提取了部分内容。",
    row_limit_reached: "行数超过读取上限，后续内容未提取。",
    cell_limit_reached: "单元格超过读取上限，后续内容未提取。",
    invalid_text_encoding: "部分文字编码无法识别，请核对原文件。",
    delimited_text_parse_failed: "表格文本解析失败，请核对原文件。",
    document_parse_failed: "文件解析失败，未能读取内容。",
    encrypted_pdf: "PDF 已加密，无法读取正文。",
    page_limit_reached: "页数超过读取上限，后续页面未提取。",
    page_extraction_failed: "部分页面提取失败，请核对原文件。",
    ocr_required: "当前版本未接入 OCR，扫描件和图片文字尚未读取。",
    nested_tables_not_extracted: "嵌套表格尚未提取。",
    notes_or_comments_not_extracted: "备注或批注尚未提取。",
    text_boxes_not_extracted: "文本框内容尚未提取。",
    images_not_ocr: "图片中的文字尚未识别，需要 OCR。",
    macros_not_executed: "宏未执行，相关生成内容可能缺失。",
    worksheet_dimensions_missing: "工作表范围信息缺失，可能未完整读取。",
    formulas_not_recalculated: "表格公式未重新计算，显示的是已保存的结果。",
    formula_cache_missing: "部分公式没有已保存的结果，请在原文件中核对。",
    graphics_may_contain_unextracted_data: "图形可能包含未提取的信息，请核对原文件。",
    unsupported_format: "当前版本不支持该文件格式。",
    file_size_limit_reached: "文件超过大小限制，内容尚未读取。",
    archive_limit_reached: "文件内部内容超过解析限制，未能完整读取。"
  };
  const activeJob = (job) => job.status === "queued" || job.status === "running";

  async function api(path, options = {}) {
    if (!session || session === "__SESSION_TOKEN__") throw new Error("请刷新工作台页面；当前页面尚未获得会话凭证。");
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) throw new Error("请求地址必须属于当前工作台。");
    const headers = { "X-Agent-Session": session };
    if (options.rawBody !== undefined) headers["Content-Type"] = "application/octet-stream";
    else if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method: options.method || "GET", headers, credentials: "same-origin", cache: "no-store", redirect: "error",
      ...(options.rawBody !== undefined ? { body: options.rawBody } : options.body !== undefined ? { body: JSON.stringify(options.body) } : {})
    });
    let data;
    try { data = await response.json(); } catch { throw new Error("服务返回了无法读取的响应，请刷新页面或重新登录。"); }
    if (!response.ok) {
      const detail = data.error || data.detail || data.message;
      const message = (Array.isArray(detail) ? detail.map((entry) => value(entry.msg || entry)).join("；") : value(detail)).slice(0, 700);
      const error = new Error(warningLabel(message) || `请求未完成（HTTP ${response.status}）。`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function notice(id, message, isError = false) {
    const element = $(id);
    element.textContent = value(message);
    element.classList.toggle("hidden", !message);
    element.classList.toggle("error", isError);
  }
  function warnings(container, messages) {
    const entries = list(messages).map(warningLabel).filter(Boolean);
    if (entries.length) container.append(node("div", "notice", entries.join("\n")));
  }
  function warningLabel(input) {
    const message = value(input);
    if (warningLabels[message]) return warningLabels[message];
    if (message.startsWith("pages_without_text:")) return `以下页面没有可读文字，可能需要 OCR：${message.slice("pages_without_text:".length)}`;
    const prefixed = message.match(/^(S\d+)[：:]\s*(.*)$/);
    return prefixed ? `${prefixed[1]}：${warningLabel(prefixed[2])}` : message;
  }
  function empty(container, title, description, glyph = "▤") {
    const wrap = node("div", "empty-state");
    wrap.append(node("span", "empty-glyph", glyph), node("h2", "", title), node("p", "", description));
    container.replaceChildren(wrap);
  }
  function sizeLabel(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size < 0) return "";
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }
  function dateLabel(input) {
    const date = new Date(input);
    return Number.isNaN(date.getTime()) ? value(input) : new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false
    }).format(date);
  }
  function fileName(sourceId, result) {
    return list(result?.sources).find((source) => source.source_id === sourceId)?.file?.name
      || state.files.find((file) => file.id === sourceId)?.name || value(sourceId) || "未标注来源";
  }
  function chip(text, warning = false) { return node("span", `chip${warning ? " warning" : ""}`, text); }
  function activate(element, callback) {
    element.addEventListener("click", callback);
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); callback(); }
    });
  }

  function switchView(view) {
    const files = view === "files";
    $("files-view").classList.toggle("hidden", !files);
    $("jobs-view").classList.toggle("hidden", files);
    $("nav-files").classList.toggle("active", files);
    $("nav-jobs").classList.toggle("active", !files);
    $("nav-files").toggleAttribute("aria-current", files);
    $("nav-jobs").toggleAttribute("aria-current", !files);
    if (files) $("nav-files").setAttribute("aria-current", "page");
    else $("nav-jobs").setAttribute("aria-current", "page");
    $("page-title").textContent = files ? (state.config?.mode === "server" ? "我的文件" : "我的 Downloads") : "任务记录";
    $("page-description").textContent = files ? "选择材料，交给 AI 阅读、归纳和分析。" : "回到一项任务，查看结果、引用与报告。";
  }
  function switchDetail(tab) {
    const source = tab === "source";
    $("source-panel").classList.toggle("hidden", !source);
    $("report-panel").classList.toggle("hidden", source);
    $("tab-source").classList.toggle("active", source);
    $("tab-report").classList.toggle("active", !source);
    $("tab-source").setAttribute("aria-selected", String(source));
    $("tab-report").setAttribute("aria-selected", String(!source));
  }
  function updateSelection() {
    $("selected-count").textContent = `已选择 ${state.selected.size} 份`;
    const inProgress = state.jobs.some(activeJob);
    $("analyze-button").disabled = state.submitting || inProgress || !state.config?.configured || state.selected.size === 0 || !$("instruction").value.trim();
    $("analyze-button").title = !state.config?.configured ? "请联系操作员配置模型连接" : inProgress ? "请等待当前后台任务完成" : "";
    if (!state.submitting) $("analyze-button").replaceChildren(document.createTextNode(inProgress ? "任务进行中" : "开始分析 "), ...(inProgress ? [] : [node("span", "", "↗")]));
  }
  function renderFiles() {
    const container = $("file-list");
    $("file-count").textContent = String(state.files.length);
    if (!state.files.length) {
      empty(container, "还没有文件", state.config?.mode === "server" ? "点击上传文件，开始阅读和分析你的资料。" : "可将需要分析的材料放入授权目录，然后重新扫描。");
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const file of state.files) {
      const row = node("div", "file-row");
      row.classList.toggle("selected", state.selected.has(file.id));
      row.classList.toggle("previewing", state.currentFile === file.id);
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selected.has(file.id);
      checkbox.disabled = !file.supported;
      checkbox.setAttribute("aria-label", `选择 ${value(file.name)}`);
      if (!file.supported) checkbox.title = file.blocked_reason ? warningLabel(file.blocked_reason) : "当前版本不能提取该文件类型";
      checkbox.addEventListener("click", (event) => event.stopPropagation());
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selected.add(file.id); else state.selected.delete(file.id);
        row.classList.toggle("selected", checkbox.checked);
        updateSelection();
      });
      const type = node("span", "file-type", value(file.extension).replace(/^\./, "").toUpperCase() || "FILE");
      const open = node("button", "file-meta file-open");
      open.type = "button";
      open.append(node("span", "file-name", file.name), node("span", "file-path", file.relative_path));
      open.addEventListener("click", (event) => { event.stopPropagation(); previewFile(file.id); });
      row.append(checkbox, type, open, node("span", "file-size", sizeLabel(file.size)));
      if (!file.supported) row.append(node("span", "file-badge", file.blocked_reason === "sensitive_file_blocked" ? "凭据已保护" : file.blocked_reason ? "超出上限" : "暂不支持"));
      row.addEventListener("click", () => previewFile(file.id));
      fragment.append(row);
    }
    container.replaceChildren(fragment);
  }

  async function loadConfig() {
    try {
      state.config = await api("/api/config");
      if (state.config.mode === "server") {
        $("file-nav-label").textContent = "我的文件";
        $("page-title").textContent = "我的文件";
        document.querySelector(".scope-tag").textContent = "个人空间";
        document.querySelector(".folder-info strong").textContent = "上传的资料";
        document.querySelector(".scan-option").classList.add("hidden");
        document.querySelector(".future-label").textContent = "文件阅读 · 分析 · 报告";
        document.querySelector(".sidebar-footnote").textContent = "文件、任务和报告保存在你的个人空间。";
        document.querySelector(".composer-footer p").textContent = "选中文件交给模型分析，报告保存在你的个人空间，可随时下载。";
        $("upload-button").classList.remove("hidden");
        $("office-apps").classList.remove("hidden");
        $("scan-button").textContent = "刷新文件";
        await scanFiles();
      }
      $("root-path").textContent = value(state.config.root) || "未配置文件目录";
      $("model-status").textContent = state.config.configured ? "模型已配置" : "尚未配置模型";
      $("model-name").textContent = state.config.model ? `${value(state.config.provider)} · ${value(state.config.model)}` : "请配置 Spark Ollama";
      $("model-dot").classList.toggle("configured", Boolean(state.config.configured));
      if (!state.config.configured) notice("global-notice", "模型尚未配置。你可以先扫描和预览文件；配置 Spark Ollama 后即可开始分析。");
      updateSelection();
    } catch (error) {
      $("model-status").textContent = "配置读取失败";
      notice("global-notice", error.message, true);
    }
  }
  function applyFiles(data) {
    state.files = list(data.files);
    state.selected = new Set([...state.selected].filter((id) => state.files.some((file) => file.id === id && file.supported)));
    if (!state.files.some((file) => file.id === state.currentFile)) state.currentFile = null;
    renderFiles(); updateSelection();
    $("list-description").textContent = `${state.files.length} 份文件 · ${state.files.filter((file) => file.supported).length} 份可处理`;
    notice("scan-warnings", list(data.warnings).map(warningLabel).join("\n"));
  }
  async function uploadFiles() {
    const files = [...$("upload-files").files];
    if (!files.length) return;
    const button = $("upload-button");
    button.disabled = true;
    let done = 0;
    try {
      for (const file of files) {
        if (file.size > 15 * 1024 * 1024) throw new Error(`${file.name} 超过单文件 15 MiB 上限。`);
        button.textContent = `上传 ${done + 1}/${files.length}…`;
        const data = await api(`/api/uploads?name=${encodeURIComponent(file.name)}`, { method: "POST", rawBody: file });
        applyFiles(data);
        done += 1;
      }
      notice("scan-warnings", `已上传 ${done} 份文件。请选择材料并填写分析任务。`);
    } catch (error) { notice("scan-warnings", `已上传 ${done} 份。${error.message}`, true); }
    finally { button.disabled = false; button.textContent = "上传文件"; $("upload-files").value = ""; }
  }
  async function scanFiles() {
    const button = $("scan-button");
    button.disabled = true;
    button.textContent = "扫描中…";
    notice("scan-warnings", "");
    try {
      const data = await api("/api/scan", { method: "POST", body: { recursive: $("recursive-scan").checked } });
      applyFiles(data);
    } catch (error) { notice("scan-warnings", error.message, true); }
    finally { button.disabled = false; button.textContent = state.config?.mode === "server" ? "刷新文件" : "重新扫描"; }
  }

  async function previewFile(id) {
    const request = ++state.previewRequest;
    state.currentFile = id;
    renderFiles();
    switchDetail("source");
    document.querySelector(".detail-panel").scrollTop = 0;
    const loading = node("div", "detail-content");
    loading.append(node("h2", "", "读取来源中…"), node("p", "", "正在从授权目录提取文本。"));
    $("source-panel").replaceChildren(loading);
    try {
      const data = await api(`/api/files/${encodeURIComponent(id)}/preview`);
      if (request !== state.previewRequest) return;
      const extraction = data.extraction || {};
      const content = node("div", "detail-content");
      content.append(node("h2", "", data.file?.name || fileName(id)), node("div", "detail-subtitle", data.file?.relative_path));
      const metadata = node("div", "detail-meta");
      const needsAttention = ["blocked", "partial", "unsupported", "needs_ocr", "empty", "failed", "error"].includes(extraction.status);
      metadata.append(chip(extractionLabels[extraction.status] || value(extraction.status) || "状态未知", needsAttention));
      if (extraction.extractor && extraction.extractor !== "none") metadata.append(chip(extractorLabels[extraction.extractor] || extraction.extractor));
      content.append(metadata);
      warnings(content, extraction.warnings);
      if (needsAttention) content.append(node("div", "notice", "该文件未完整读取。请核对原件；扫描件或图片文字可能需要 OCR，当前预览不能代表全部内容。"));
      const blocks = list(extraction.blocks);
      if (!blocks.length) content.append(node("p", "", "未提取到可显示的文本。请查看上方提示和原文件。"));
      for (const block of blocks) {
        const entry = node("section", "extraction-block");
        entry.append(node("div", "locator", block.locator || "未标注位置"), node("pre", "block-text", block.text));
        content.append(entry);
      }
      if (data.sha256) content.append(node("div", "source-record", `文件 SHA-256：${value(data.sha256)}`));
      $("source-panel").replaceChildren(content);
    } catch (error) {
      if (request !== state.previewRequest) return;
      const content = node("div", "detail-content");
      content.append(node("h2", "", "无法预览来源"), node("div", "notice error", error.message));
      $("source-panel").replaceChildren(content);
    }
  }

  function renderJobs() {
    $("job-count").textContent = String(state.jobs.length);
    const container = $("job-list");
    if (!state.jobs.length) { empty(container, "还没有任务", "选择材料并开始分析，任务会保存在这里。", "◷"); return; }
    const fragment = document.createDocumentFragment();
    for (const job of state.jobs) {
      const card = node("div", "job-card");
      card.setAttribute("role", "button");
      card.tabIndex = 0;
      card.classList.toggle("active", job.id === state.currentJob);
      card.setAttribute("aria-pressed", String(job.id === state.currentJob));
      const heading = node("div", "job-heading");
      const date = node("time", "", dateLabel(job.created_at));
      date.title = "北京时间";
      if (job.created_at) date.setAttribute("datetime", value(job.created_at));
      heading.append(node("span", `job-state ${value(job.status)}`, jobLabels[job.status] || job.status), date);
      card.append(heading, node("p", "", job.instruction));
      if (job.progress?.files_total) card.append(node("small", "", `已处理 ${job.progress.files_done}/${job.progress.files_total} 份`));
      if (job.error) card.append(node("p", "job-error", job.error));
      activate(card, () => selectJob(job.id));
      fragment.append(card);
    }
    container.replaceChildren(fragment);
  }
  function renderReport(job) {
    $("report-indicator").classList.remove("hidden");
    const content = node("div", "detail-content");
    content.append(node("h2", "", "材料分析"), node("div", "detail-subtitle", `${dateLabel(job.created_at)} · ${jobLabels[job.status] || value(job.status)}`));
    content.append(node("p", "ai-note", "以下为 AI 建议。引用标记“原文片段已匹配”仅表示文本命中，不保证模型推断正确。请核对金额、日期和业务决定。"));
    const instruction = node("section", "report-section");
    instruction.append(node("h3", "", "任务要求"), node("p", "", job.instruction));
    content.append(instruction);
    if (job.progress?.files_total || activeJob(job)) {
      const progress = node("div", "job-progress");
      const stats = job.progress || {};
      progress.append(node("span", `job-state ${value(job.status)}`, jobLabels[job.status]));
      if (stats.files_total) {
        progress.append(node("p", "", `已处理 ${stats.files_done}/${stats.files_total} 份 · 已分析 ${stats.files_analyzed} 份 · 跳过或失败 ${stats.files_skipped} 份`));
        const meter = node("progress");
        meter.max = stats.files_total;
        meter.value = stats.files_done || 0;
        meter.setAttribute("aria-label", "文件处理进度");
        progress.append(meter);
        if (activeJob(job) && stats.current_file) progress.append(node("p", "", stats.phase === "summarizing" ? "正在综合各段摘要…" : `当前：${stats.current_file}${stats.segments_total ? ` · 分段 ${stats.current_segment}/${stats.segments_total}` : ""}`));
      }
      if (activeJob(job)) {
        progress.append(node("p", "", "关闭页面不影响后台任务；停止将在当前模型调用结束后生效。"));
        const cancel = node("button", "button button-secondary", "停止并保留已有结果");
        cancel.type = "button";
        cancel.addEventListener("click", async () => {
          cancel.disabled = true;
          try {
            const response = await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST", body: {} });
            notice("global-notice", response.message);
          } catch (error) { notice("global-notice", error.message, true); cancel.disabled = false; }
        });
        progress.append(cancel);
      }
      content.append(progress);
    }
    if (job.error) content.append(node("div", "notice error", job.error));
    if (job.status === "interrupted") content.append(node("div", "notice", "该任务未完成。请检查服务状态；需要时重新选择材料并提交任务。"));
    const result = job.result;
    if (result) {
      warnings(content, result.warnings);
      content.append(node("h3", "", "内容摘要"), node("p", "", result.summary || "模型未返回摘要。"));
      if (list(result.documents).length) {
        content.append(node("h3", "", "材料归纳"));
        for (const document of result.documents) {
          const card = node("div", "document-summary");
          card.append(node("strong", "", `${fileName(document.source_id, result)}${document.segment ? ` · 分段 ${document.segment}` : ""}`));
          if (document.category) {
            const metadata = node("div", "detail-meta");
            metadata.append(chip(document.category));
            card.append(metadata);
          }
          card.append(node("p", "", document.summary));
          content.append(card);
        }
      }
      if (list(result.facts).length) {
        content.append(node("h3", "", "事实与原文引用"));
        for (const fact of result.facts) {
          const card = node("section", "fact-card");
          card.append(node("div", "locator", `${fileName(fact.source_id, result)} · ${value(fact.locator) || "未标注位置"}`), node("p", "", fact.claim));
          if (fact.quote) card.append(node("blockquote", "", fact.quote));
          card.append(chip(fact.verified ? "原文片段已匹配" : "引用待人工核对", !fact.verified));
          content.append(card);
        }
      }
      if (list(result.recommendations).length) {
        content.append(node("h3", "", "后续建议"));
        const recommendations = node("ul", "recommendations");
        for (const recommendation of result.recommendations) recommendations.append(node("li", "", recommendation));
        content.append(recommendations);
      }
      if (list(result.sources).length) {
        content.append(node("h3", "", "本次来源"));
        for (const source of result.sources) {
          const record = node("div", "source-record");
          record.append(node("strong", "", source.file?.name || fileName(source.source_id, result)), node("small", "", source.file?.relative_path));
          record.append(node("small", "", `提取状态：${extractionLabels[source.status] || value(source.status) || "未知"}`));
          if (source.analysis_status) record.append(node("small", "", `分析状态：${analysisLabels[source.analysis_status] || source.analysis_status} · 分段 ${source.segments_done}/${source.segments_total}`));
          if (source.sha256) record.append(node("small", "", `SHA-256：${value(source.sha256)}`));
          content.append(record);
          warnings(content, source.warnings);
        }
      }
      if (result.model) content.append(node("div", "source-record", `分析模型：${value(result.model)}`));
    }
    if (list(job.artifacts).length) {
      content.append(node("h3", "", "报告文件"));
      for (const artifact of job.artifacts) {
        const button = node("button", "artifact-button");
        button.type = "button";
        button.append(node("span", "", artifact.name), node("span", "", "下载 ↓"));
        button.addEventListener("click", () => downloadArtifact(artifact, button, content));
        content.append(button);
      }
    }
    if (job.status === "completed" && !result) content.append(node("p", "", "暂无可显示的结构化结果，请查看报告文件或服务日志。"));
    $("report-panel").replaceChildren(content);
  }

  async function downloadArtifact(artifact, button, container) {
    button.disabled = true;
    try {
      const url = new URL(value(artifact.url), window.location.origin);
      if (url.origin !== window.location.origin) throw new Error("报告地址不属于当前工作台，无法下载。");
      const response = await fetch(url, { headers: { "X-Agent-Session": session }, credentials: "same-origin", redirect: "error" });
      if (!response.ok) throw new Error(`报告下载失败（HTTP ${response.status}）。`);
      const blob = await response.blob();
      const link = document.createElement("a");
      const objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl;
      link.download = value(artifact.name) || "agent-report";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
    } catch (error) { container.append(node("div", "notice error", error.message)); }
    finally { button.disabled = false; }
  }
  async function selectJob(id) {
    const request = ++state.jobRequest;
    state.currentJob = id;
    renderJobs();
    switchDetail("report");
    document.querySelector(".detail-panel").scrollTop = 0;
    const cached = state.jobs.find((job) => job.id === id);
    if (cached) renderReport(cached);
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (request !== state.jobRequest || state.currentJob !== id) return;
      const index = state.jobs.findIndex((item) => item.id === id);
      if (index >= 0) state.jobs[index] = job;
      renderReport(job);
      renderJobs();
    } catch (error) { if (request === state.jobRequest) notice("global-notice", error.message, true); }
  }
  function schedulePoll(delay = 2200) {
    window.clearTimeout(state.pollTimer);
    if (state.jobs.some(activeJob)) state.pollTimer = window.setTimeout(() => refreshJobs(true), delay);
  }
  async function refreshJobs(silent = false) {
    if (state.refreshing) return state.refreshing;
    const button = $("refresh-jobs");
    button.disabled = true;
    state.refreshing = (async () => {
      let nextDelay = 2200;
      let continuePolling = true;
      try {
        const data = await api("/api/jobs");
        state.jobs = list(data.jobs);
        renderJobs();
        updateSelection();
        if (state.currentJob) {
          const current = state.jobs.find((job) => job.id === state.currentJob);
          if (current) {
            if (current.result || activeJob(current) || current.status !== "completed") renderReport(current);
            else {
              const selectedId = state.currentJob;
              const job = await api(`/api/jobs/${encodeURIComponent(selectedId)}`);
              if (state.currentJob === selectedId) renderReport(job);
            }
          }
        }
      } catch (error) {
        notice("global-notice", `${silent ? "任务状态暂时无法更新：" : ""}${error.message}`, true);
        nextDelay = 6000;
        if (error.status === 401 || error.status === 403) continuePolling = false;
      } finally {
        button.disabled = false;
        state.refreshing = null;
        if (continuePolling) schedulePoll(nextDelay);
      }
    })();
    return state.refreshing;
  }
  async function submitJob() {
    if (state.submitting || !state.selected.size || !$("instruction").value.trim()) return;
    state.submitting = true;
    updateSelection();
    $("analyze-button").textContent = "提交中…";
    notice("global-notice", "");
    try {
      const instruction = $("instruction").value.trim();
      const job = await api("/api/jobs", { method: "POST", body: { file_ids: [...state.selected], instruction } });
      state.jobs.unshift({ ...job, instruction });
      state.currentJob = job.id;
      renderJobs();
      renderReport({ ...job, instruction });
      switchDetail("report");
      await refreshJobs();
      await selectJob(job.id);
      switchDetail("report");
      schedulePoll();
    } catch (error) { notice("global-notice", error.message, true); }
    finally {
      state.submitting = false;
      $("analyze-button").replaceChildren(document.createTextNode("开始分析 "), node("span", "", "↗"));
      updateSelection();
    }
  }

  $("nav-files").addEventListener("click", () => switchView("files"));
  $("nav-jobs").addEventListener("click", () => { switchView("jobs"); refreshJobs(); });
  $("tab-source").addEventListener("click", () => switchDetail("source"));
  $("tab-report").addEventListener("click", () => switchDetail("report"));
  for (const [id, target] of [["tab-source", "report"], ["tab-report", "source"]]) {
    $(id).addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); switchDetail(target); $(`tab-${target}`).focus(); }
    });
  }
  $("scan-button").addEventListener("click", scanFiles);
  $("upload-button").addEventListener("click", () => $("upload-files").click());
  $("upload-files").addEventListener("change", uploadFiles);
  $("select-all").addEventListener("click", () => {
    state.selected = new Set(state.files.filter((file) => file.supported).map((file) => file.id));
    renderFiles(); updateSelection();
  });
  $("clear-selection").addEventListener("click", () => {
    state.selected.clear(); renderFiles(); updateSelection();
  });
  $("refresh-jobs").addEventListener("click", () => refreshJobs());
  $("analyze-button").addEventListener("click", submitJob);
  $("instruction").addEventListener("input", updateSelection);
  for (const button of document.querySelectorAll("[data-preset]")) {
    button.addEventListener("click", () => { $("instruction").value = button.dataset.preset; $("instruction").focus(); updateSelection(); });
  }
  Promise.allSettled([loadConfig(), refreshJobs()]);
})();
