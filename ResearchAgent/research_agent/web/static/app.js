const state = {
  library: [],
  dates: [],
  topics: [],
  activeArticleId: null,
  selectedDate: "all",
  selectedTopic: "all",
  search: "",
  activeJobId: null,
  activeJobStartedAt: 0,
  sidebarCollapsed: false,
  viewMode: "both",
};

const nodes = {
  appShell: document.getElementById("appShell"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  articleList: document.getElementById("articleList"),
  articleCount: document.getElementById("articleCount"),
  dateFilters: document.getElementById("dateFilters"),
  topicFilters: document.getElementById("topicFilters"),
  searchInput: document.getElementById("searchInput"),
  emptyState: document.getElementById("emptyState"),
  articleView: document.getElementById("articleView"),
  heroTitle: document.getElementById("heroTitle"),
  metaBadges: document.getElementById("metaBadges"),
  viewToggle: document.getElementById("viewToggle"),
  summaryText: document.getElementById("summaryText"),
  usageInline: document.getElementById("usageInline"),
  articleBody: document.getElementById("articleBody"),
  fileActions: document.getElementById("fileActions"),
  sourceLink: document.getElementById("sourceLink"),
  headingIndex: document.getElementById("headingIndex"),
  pdfPageRefs: document.getElementById("pdfPageRefs"),
  previewStrip: document.getElementById("previewStrip"),
  previewTitle: document.getElementById("previewTitle"),
  pdfPreviewGallery: document.getElementById("pdfPreviewGallery"),
  pdfPane: document.getElementById("pdfPane"),
  pdfViewer: document.getElementById("pdfViewer"),
  pdfEmpty: document.getElementById("pdfEmpty"),
  pdfCaption: document.getElementById("pdfCaption"),
  ingestUrlInput: document.getElementById("ingestUrlInput"),
  ingestUrlButton: document.getElementById("ingestUrlButton"),
  uploadDropzone: document.getElementById("uploadDropzone"),
  pdfUploadInput: document.getElementById("pdfUploadInput"),
  ingestStatus: document.getElementById("ingestStatus"),
  progressShell: document.getElementById("progressShell"),
  progressFill: document.getElementById("progressFill"),
  progressLabel: document.getElementById("progressLabel"),
  progressPercent: document.getElementById("progressPercent"),
  progressHint: document.getElementById("progressHint"),
};

async function bootstrap() {
  await refreshLibrary();
}

async function refreshLibrary() {
  const response = await fetch("/api/library");
  const payload = await response.json();
  state.library = payload.articles || [];
  state.dates = payload.dates || [];
  state.topics = payload.topics || [];
  renderFilters();
  renderArticleList();
}

function renderFilters() {
  nodes.dateFilters.innerHTML = "";
  nodes.topicFilters.innerHTML = "";

  nodes.dateFilters.appendChild(createChip("全部", state.selectedDate === "all", () => {
    state.selectedDate = "all";
    renderFilters();
    renderArticleList();
  }));

  state.dates.forEach((dateValue) => {
    nodes.dateFilters.appendChild(createChip(dateValue, state.selectedDate === dateValue, () => {
      state.selectedDate = dateValue;
      renderFilters();
      renderArticleList();
    }));
  });

  nodes.topicFilters.appendChild(createChip("全部", state.selectedTopic === "all", () => {
    state.selectedTopic = "all";
    renderFilters();
    renderArticleList();
  }));

  state.topics.forEach((topic) => {
    nodes.topicFilters.appendChild(createChip(`${topic.name} (${topic.count})`, state.selectedTopic === topic.name, () => {
      state.selectedTopic = topic.name;
      renderFilters();
      renderArticleList();
    }));
  });
}

function createChip(label, active, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `chip ${active ? "active" : ""}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderArticleList() {
  const filtered = getFilteredArticles();
  nodes.articleCount.textContent = String(filtered.length);
  nodes.articleList.innerHTML = "";

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "article-card muted";
    empty.textContent = "当前筛选条件下没有文章。";
    nodes.articleList.appendChild(empty);
    return;
  }

  filtered.forEach((article) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `article-card ${state.activeArticleId === article.article_id ? "selected" : ""}`;
    const usage = article.llm_usage || {};
    button.innerHTML = `
      <div class="card-eyebrow">
        <span>${escapeHtml(article.source || "unknown")}</span>
        <span>${escapeHtml(article.archive_date || "")}</span>
      </div>
      <div class="card-title">${escapeHtml(article.title || "Untitled")}</div>
      <div class="card-excerpt">${escapeHtml((article.summary || "").slice(0, 160))}</div>
      <div class="card-mini">
        <span>${compactTokens(usage.total_tokens || 0)}</span>
        <span>${compactUsd(usage.estimated_cost_usd || 0)}</span>
      </div>
    `;
    button.addEventListener("click", () => loadArticle(article.article_id));
    nodes.articleList.appendChild(button);
  });
}

function getFilteredArticles() {
  return state.library.filter((article) => {
    const dateMatch = state.selectedDate === "all" || (article.archive_date || "") === state.selectedDate;
    const topicMatch = state.selectedTopic === "all" || (article.tags || []).includes(state.selectedTopic);
    const textBlob = `${article.title || ""} ${article.summary || ""}`.toLowerCase();
    const searchMatch = !state.search || textBlob.includes(state.search.toLowerCase());
    return dateMatch && topicMatch && searchMatch;
  });
}

async function loadArticle(articleId) {
  state.activeArticleId = articleId;
  renderArticleList();

  const response = await fetch(`/api/articles/${articleId}`);
  if (!response.ok) {
    showStatus("文章加载失败。", "error");
    return;
  }
  const article = await response.json();
  renderArticle(article);
}

function renderArticle(article) {
  state.activeArticleId = article.article_id;
  renderArticleList();

  nodes.emptyState.classList.add("hidden");
  nodes.articleView.classList.remove("hidden");
  nodes.heroTitle.textContent = article.title || "Untitled";

  renderMetaBadges(article);
  renderFileActions(article);
  renderUsageInline(article.llm_usage || {});
  applyViewMode();

  nodes.summaryText.textContent = article.summary || "暂无摘要。";
  nodes.articleBody.innerHTML = article.rendered_html || "<p>暂无正文。</p>";
  renderHeadingIndex();
  renderPdfPane(article);
  renderVisualGallery(article);
}

function renderMetaBadges(article) {
  nodes.metaBadges.innerHTML = "";
  [article.source, (article.published_at || "").slice(0, 10), ...(article.tags || []).slice(0, 5)]
    .filter(Boolean)
    .forEach((value) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = value;
      nodes.metaBadges.appendChild(badge);
    });
}

function renderFileActions(article) {
  if (article.source_url) {
    nodes.sourceLink.href = article.source_url;
    nodes.sourceLink.classList.remove("hidden");
  } else {
    nodes.sourceLink.classList.add("hidden");
  }

  nodes.fileActions.innerHTML = "";
  (article.source_files || []).forEach((file) => {
    const link = document.createElement("a");
    link.className = "secondary-button";
    link.href = file.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = file.name;
    nodes.fileActions.appendChild(link);
  });
}

function renderUsageInline(usage) {
  const totalTokens = usage.total_tokens || 0;
  if (!totalTokens) {
    nodes.usageInline.innerHTML = `<span class="usage-pill muted">暂无 Token 统计</span>`;
    return;
  }

  const pricingLink = usage.pricing_reference_url
    ? `<a class="usage-pill link" href="${escapeHtml(usage.pricing_reference_url)}" target="_blank" rel="noreferrer">定价</a>`
    : "";

  nodes.usageInline.innerHTML = `
    <span class="usage-pill">输入 ${formatNumber(usage.prompt_tokens || 0)}</span>
    <span class="usage-pill">输出 ${formatNumber(usage.output_tokens || 0)}</span>
    <span class="usage-pill">总计 ${formatNumber(totalTokens)}</span>
    <span class="usage-pill cost">$${formatUsd(usage.estimated_cost_usd || 0)}</span>
    ${pricingLink}
  `;
}

function renderHeadingIndex() {
  nodes.headingIndex.innerHTML = "";
  const headings = [...nodes.articleBody.querySelectorAll("h1, h2, h3")];
  if (!headings.length) {
    nodes.headingIndex.innerHTML = `<span class="index-empty">暂无索引</span>`;
    return;
  }

  headings.forEach((heading, index) => {
    const id = heading.id || `section-${index}`;
    heading.id = id;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "index-link";
    button.textContent = heading.textContent || "Untitled";
    button.addEventListener("click", () => {
      heading.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    nodes.headingIndex.appendChild(button);
  });
}

function renderPdfPane(article) {
  const pdfUrl = article.pdf_source_url || "";
  const pageRefs = article.pdf_page_refs || [];

  nodes.pdfPageRefs.innerHTML = "";
  if (!pdfUrl) {
    nodes.pdfViewer.classList.add("hidden");
    nodes.pdfEmpty.classList.remove("hidden");
    nodes.pdfCaption.textContent = "当前文章没有可用的 PDF 原文。";
    return;
  }

  nodes.pdfViewer.classList.remove("hidden");
  nodes.pdfEmpty.classList.add("hidden");
  nodes.pdfViewer.src = buildPdfViewerUrl(pdfUrl, 1);
  nodes.pdfCaption.textContent = pageRefs.length
    ? "点击下方页码可直接跳转原始 PDF 对应位置。"
    : "当前解析暂未产出页码引用，可直接在右侧原文中对照阅读。";

  if (!pageRefs.length) {
    const empty = document.createElement("span");
    empty.className = "index-empty";
    empty.textContent = "暂无页码引用";
    nodes.pdfPageRefs.appendChild(empty);
  } else {
    pageRefs.forEach((page) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "page-ref";
      button.textContent = `P${page}`;
      button.addEventListener("click", () => openPdfAt(pdfUrl, page));
      nodes.pdfPageRefs.appendChild(button);
    });
  }

  nodes.articleBody.querySelectorAll("a.pdf-page-ref").forEach((anchor) => {
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      const page = Number(anchor.dataset.page || "1");
      openPdfAt(pdfUrl, page);
    });
  });
}

function openPdfAt(pdfUrl, page) {
  const targetUrl = buildPdfViewerUrl(pdfUrl, page);
  nodes.pdfViewer.src = "about:blank";
  window.setTimeout(() => {
    nodes.pdfViewer.src = targetUrl;
  }, 20);
  if (window.innerWidth < 1180) {
    nodes.pdfPane.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderVisualGallery(article) {
  const sourceFigures = article.source_figure_gallery || [];
  const previews = article.pdf_previews || [];
  const pdfUrl = article.pdf_source_url || "";
  nodes.pdfPreviewGallery.innerHTML = "";

  if (sourceFigures.length) {
    nodes.previewTitle.textContent = "LaTeX 原始图集";
    nodes.previewStrip.classList.remove("hidden");
    sourceFigures.forEach((figure) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preview-card figure-card";
      button.innerHTML = `
        <img src="${escapeHtml(figure.url)}" alt="${escapeHtml(figure.title || figure.source_name || "论文配图")}" />
        <span>${escapeHtml(figure.title || figure.source_name || "论文配图")}</span>
      `;
      button.addEventListener("click", () => {
        window.open(figure.url, "_blank", "noopener");
      });
      nodes.pdfPreviewGallery.appendChild(button);
    });
    return;
  }

  if (!previews.length || !pdfUrl) {
    nodes.previewStrip.classList.add("hidden");
    return;
  }

  nodes.previewTitle.textContent = "重点图表 / 表格页";
  nodes.previewStrip.classList.remove("hidden");
  previews.forEach((preview) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "preview-card";
    button.innerHTML = `
      <img src="${escapeHtml(preview.url)}" alt="PDF page ${preview.page}" />
      <span>P${preview.page}</span>
    `;
    button.addEventListener("click", () => openPdfAt(pdfUrl, preview.page));
    nodes.pdfPreviewGallery.appendChild(button);
  });
}

async function startUrlIngest() {
  const url = nodes.ingestUrlInput.value.trim();
  if (!url) {
    showStatus("请输入 arXiv 链接或普通网址。", "error");
    return;
  }

  setIntakeBusy(true);
  setProgress(3, "已提交链接", "正在创建后台任务。");
  showStatus("后台开始抓取并解析内容。", "pending");

  try {
    const response = await fetch("/api/ingest/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "链接导入失败");
    }
    nodes.ingestUrlInput.value = "";
    await pollJob(payload.job_id);
  } catch (error) {
    setIntakeBusy(false);
    showStatus(error.message || "链接导入失败。", "error");
  }
}

function uploadPdf(file) {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/ingest/pdf");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return;
      }
      const ratio = event.loaded / event.total;
      const percent = Math.max(1, Math.min(45, Math.round(ratio * 45)));
      setProgress(
        percent,
        `上传 ${file.name}`,
        `文件已上传 ${Math.round(ratio * 100)}%，上传完成后会进入后台 Gemini 解析。`
      );
      showStatus("文件正在上传。", "pending");
    };
    xhr.onload = () => {
      const payload = xhr.response || {};
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      reject(new Error(payload.detail || "PDF 导入失败"));
    };
    xhr.onerror = () => reject(new Error("PDF 上传失败，网络连接中断。"));
    xhr.send(formData);
  });
}

async function handlePdfIngest(file) {
  if (!file) {
    return;
  }
  if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
    showStatus("仅支持 PDF 文件。", "error");
    return;
  }

  setIntakeBusy(true);
  setProgress(1, "准备上传", "文件校验通过，准备上传。");

  try {
    const payload = await uploadPdf(file);
    await pollJob(payload.job_id);
    nodes.pdfUploadInput.value = "";
  } catch (error) {
    setIntakeBusy(false);
    showStatus(error.message || "PDF 导入失败。", "error");
  }
}

async function pollJob(jobId) {
  state.activeJobId = jobId;
  state.activeJobStartedAt = Date.now();

  while (true) {
    const response = await fetch(`/api/ingest/jobs/${jobId}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "任务状态查询失败");
    }

    const elapsed = Math.max(0, Math.round((Date.now() - state.activeJobStartedAt) / 1000));
    setProgress(payload.progress || 0, jobLabel(payload), `${payload.message} · 已耗时 ${elapsed}s`);
    showStatus(payload.message, payload.status === "failed" ? "error" : payload.status === "completed" ? "success" : "pending");

    if (payload.status === "completed") {
      await refreshLibrary();
      renderArticle(payload.article);
      setIntakeBusy(false);
      return;
    }

    if (payload.status === "failed") {
      setIntakeBusy(false);
      throw new Error(payload.error || payload.message || "后台任务失败");
    }

    await sleep(1200);
  }
}

function jobLabel(job) {
  if (job.kind === "pdf") {
    return "PDF 解析中";
  }
  if (job.kind === "url") {
    return "链接解析中";
  }
  return "处理中";
}

function setIntakeBusy(isBusy) {
  nodes.ingestUrlButton.disabled = isBusy;
  nodes.uploadDropzone.classList.toggle("disabled", isBusy);
}

function showStatus(message, tone) {
  if (!message) {
    nodes.ingestStatus.className = "status-banner hidden";
    nodes.ingestStatus.textContent = "";
    return;
  }
  nodes.ingestStatus.className = `status-banner ${tone || ""}`;
  nodes.ingestStatus.textContent = message;
}

function setProgress(percent, label, hint) {
  nodes.progressShell.classList.remove("hidden");
  nodes.progressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressLabel.textContent = label;
  nodes.progressPercent.textContent = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressHint.textContent = hint || "";
}

function toggleSidebar() {
  state.sidebarCollapsed = !state.sidebarCollapsed;
  nodes.appShell.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
}

function setViewMode(mode) {
  state.viewMode = mode;
  applyViewMode();
}

function applyViewMode() {
  nodes.appShell.classList.remove("view-mode-both", "view-mode-analysis", "view-mode-pdf");
  nodes.appShell.classList.add(`view-mode-${state.viewMode}`);
  [...nodes.viewToggle.querySelectorAll(".view-toggle-button")].forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.viewMode);
  });
}

function bindUploadInteractions() {
  nodes.uploadDropzone.addEventListener("click", () => {
    if (!nodes.ingestUrlButton.disabled) {
      nodes.pdfUploadInput.click();
    }
  });

  nodes.uploadDropzone.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !nodes.ingestUrlButton.disabled) {
      event.preventDefault();
      nodes.pdfUploadInput.click();
    }
  });

  nodes.uploadDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    nodes.uploadDropzone.classList.add("dragover");
  });

  nodes.uploadDropzone.addEventListener("dragleave", () => {
    nodes.uploadDropzone.classList.remove("dragover");
  });

  nodes.uploadDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    nodes.uploadDropzone.classList.remove("dragover");
    const [file] = event.dataTransfer.files || [];
    handlePdfIngest(file);
  });

  nodes.pdfUploadInput.addEventListener("change", (event) => {
    const [file] = event.target.files || [];
    handlePdfIngest(file);
  });
}

function compactTokens(value) {
  const numeric = Number(value || 0);
  if (!numeric) {
    return "0 tok";
  }
  if (numeric >= 1000) {
    return `${(numeric / 1000).toFixed(1)}k tok`;
  }
  return `${numeric} tok`;
}

function compactUsd(value) {
  return `$${Number(value || 0).toFixed(4)}`;
}

function buildPdfViewerUrl(pdfUrl, page) {
  const separator = pdfUrl.includes("?") ? "&" : "?";
  const cacheBust = `_viewer_jump=${Date.now()}`;
  return `${pdfUrl}${separator}${cacheBust}#page=${page}&zoom=page-width`;
}

function formatUsd(value) {
  return Number(value || 0).toFixed(4);
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

nodes.searchInput.addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderArticleList();
});

nodes.ingestUrlButton.addEventListener("click", startUrlIngest);
nodes.ingestUrlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    startUrlIngest();
  }
});
nodes.sidebarToggle.addEventListener("click", toggleSidebar);
nodes.viewToggle.addEventListener("click", (event) => {
  const button = event.target.closest(".view-toggle-button");
  if (!button) {
    return;
  }
  setViewMode(button.dataset.mode);
});

bindUploadInteractions();
applyViewMode();

bootstrap().catch((error) => {
  nodes.articleList.innerHTML = `<div class="article-card muted">加载失败：${escapeHtml(error.message || "unknown error")}</div>`;
});
