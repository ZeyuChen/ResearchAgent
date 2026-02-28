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
};

const nodes = {
  articleList: document.getElementById("articleList"),
  dateFilters: document.getElementById("dateFilters"),
  topicFilters: document.getElementById("topicFilters"),
  searchInput: document.getElementById("searchInput"),
  emptyState: document.getElementById("emptyState"),
  articleView: document.getElementById("articleView"),
  heroTitle: document.getElementById("heroTitle"),
  metaBadges: document.getElementById("metaBadges"),
  summaryBlock: document.getElementById("summaryBlock"),
  articleBody: document.getElementById("articleBody"),
  fileActions: document.getElementById("fileActions"),
  sourceLink: document.getElementById("sourceLink"),
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

  nodes.dateFilters.appendChild(createFilterChip("全部", state.selectedDate === "all", () => {
    state.selectedDate = "all";
    renderFilters();
    renderArticleList();
  }));

  state.dates.forEach((dateValue) => {
    nodes.dateFilters.appendChild(createFilterChip(dateValue, state.selectedDate === dateValue, () => {
      state.selectedDate = dateValue;
      renderFilters();
      renderArticleList();
    }));
  });

  nodes.topicFilters.appendChild(createFilterChip("全部", state.selectedTopic === "all", () => {
    state.selectedTopic = "all";
    renderFilters();
    renderArticleList();
  }));

  state.topics.forEach((topic) => {
    nodes.topicFilters.appendChild(createFilterChip(`${topic.name} (${topic.count})`, state.selectedTopic === topic.name, () => {
      state.selectedTopic = topic.name;
      renderFilters();
      renderArticleList();
    }));
  });
}

function createFilterChip(label, active, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `chip ${active ? "active" : ""}`;
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderArticleList() {
  const filtered = state.library.filter((article) => {
    const dateMatch = state.selectedDate === "all" || (article.archive_date || "") === state.selectedDate;
    const topicMatch = state.selectedTopic === "all" || (article.tags || []).includes(state.selectedTopic);
    const searchable = `${article.title || ""} ${article.summary || ""}`.toLowerCase();
    const searchMatch = !state.search || searchable.includes(state.search.toLowerCase());
    return dateMatch && topicMatch && searchMatch;
  });

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
      <div class="card-topline">
        <span class="card-source">${escapeHtml(article.source || "unknown")}</span>
        <span class="card-date">${escapeHtml(article.archive_date || "")}</span>
      </div>
      <div class="card-title">${escapeHtml(article.title || "Untitled")}</div>
      <div class="card-excerpt">${escapeHtml((article.summary || article.article_excerpt || "").slice(0, 160))}</div>
      <div class="card-bottomline">
        <span>${formatTokenCompact(usage.total_tokens || 0)}</span>
        <span>${formatUsdCompact(usage.estimated_cost_usd || 0)}</span>
      </div>
    `;
    button.addEventListener("click", () => loadArticle(article.article_id));
    nodes.articleList.appendChild(button);
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

  nodes.metaBadges.innerHTML = "";
  [
    article.source,
    (article.published_at || "").slice(0, 10),
    ...(article.tags || []).slice(0, 4),
  ]
    .filter(Boolean)
    .forEach((tag) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = tag;
      nodes.metaBadges.appendChild(badge);
    });

  if (article.source_url) {
    nodes.sourceLink.href = article.source_url;
    nodes.sourceLink.classList.remove("hidden");
  } else {
    nodes.sourceLink.classList.add("hidden");
  }

  nodes.fileActions.innerHTML = "";
  (article.source_files || []).forEach((file) => {
    const link = document.createElement("a");
    link.className = "primary-button subtle";
    link.href = file.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = file.name;
    nodes.fileActions.appendChild(link);
  });

  nodes.summaryBlock.innerHTML = `
    <div class="summary-grid">
      <section class="summary-card">
        <div class="mini-label">内容摘要</div>
        <p>${escapeHtml(article.summary || "暂无摘要")}</p>
      </section>
      <section class="summary-card usage-card">
        ${renderUsageMarkup(article.llm_usage || {})}
      </section>
    </div>
  `;
  nodes.articleBody.innerHTML = article.rendered_html || "<p>暂无正文。</p>";
}

function renderUsageMarkup(usage) {
  const totalTokens = usage.total_tokens || 0;
  if (!totalTokens) {
    return `
      <div class="mini-label">Gemini Token / 成本</div>
      <p class="muted-copy">当前文章没有可用的 Token 统计信息。</p>
    `;
  }

  const pricingLink = usage.pricing_reference_url
    ? `<a class="inline-link" href="${escapeHtml(usage.pricing_reference_url)}" target="_blank" rel="noreferrer">Google 定价</a>`
    : "";

  return `
    <div class="mini-label">Gemini Token / 成本</div>
    <div class="usage-grid">
      <div class="usage-metric">
        <span class="usage-name">输入</span>
        <strong>${formatNumber(usage.prompt_tokens || 0)}</strong>
      </div>
      <div class="usage-metric">
        <span class="usage-name">输出</span>
        <strong>${formatNumber(usage.output_tokens || 0)}</strong>
      </div>
      <div class="usage-metric">
        <span class="usage-name">总计</span>
        <strong>${formatNumber(totalTokens)}</strong>
      </div>
      <div class="usage-metric accent">
        <span class="usage-name">估算费用</span>
        <strong>$${formatUsd(usage.estimated_cost_usd || 0)}</strong>
      </div>
    </div>
    <div class="usage-footnote">
      模型：${escapeHtml(usage.model || "gemini-3-flash-preview")} · 输入 $${formatUsd(usage.input_cost_usd || 0)} · 输出 $${formatUsd(usage.output_cost_usd || 0)}
      ${pricingLink ? ` · ${pricingLink}` : ""}
    </div>
  `;
}

async function startUrlIngest() {
  const url = nodes.ingestUrlInput.value.trim();
  if (!url) {
    showStatus("请输入 arXiv 链接或普通网址。", "error");
    return;
  }

  setIntakeBusy(true);
  setProgress(3, "已提交链接", "正在创建后台解析任务。");
  showStatus("后台已接管任务，准备抓取并解析内容。", "pending");

  try {
    const response = await fetch("/api/ingest/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "URL 导入失败");
    }
    nodes.ingestUrlInput.value = "";
    await pollJob(payload.job_id);
  } catch (error) {
    finishProgress();
    setIntakeBusy(false);
    showStatus(error.message || "URL 导入失败。", "error");
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
      const percent = Math.max(1, Math.min(45, Math.round((event.loaded / event.total) * 45)));
      setProgress(percent, `正在上传 ${file.name}`, `已上传 ${Math.round((event.loaded / event.total) * 100)}%，上传完成后会进入 Gemini 解析。`);
      showStatus("文件正在上传到后端。", "pending");
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
    showStatus("仅支持上传 PDF 文件。", "error");
    return;
  }

  setIntakeBusy(true);
  setProgress(1, "准备上传 PDF", "文件校验通过，马上开始上传。");

  try {
    const payload = await uploadPdf(file);
    await pollJob(payload.job_id);
    nodes.pdfUploadInput.value = "";
  } catch (error) {
    finishProgress();
    setIntakeBusy(false);
    showStatus(error.message || "PDF 导入失败。", "error");
  }
}

async function pollJob(jobId) {
  state.activeJobId = jobId;
  state.activeJobStartedAt = Date.now();

  while (true) {
    const response = await fetch(`/api/ingest/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) {
      throw new Error(job.detail || "任务状态查询失败");
    }

    const elapsedSeconds = Math.max(0, Math.round((Date.now() - state.activeJobStartedAt) / 1000));
    const hint = elapsedSeconds > 0 ? `${job.message} · 已耗时 ${elapsedSeconds}s` : job.message;
    const tone = job.status === "failed" ? "error" : job.status === "completed" ? "success" : "pending";
    setProgress(job.progress || 0, statusLabel(job), hint);
    showStatus(job.message, tone);

    if (job.status === "completed") {
      await refreshLibrary();
      renderArticle(job.article);
      showStatus("解析完成，已自动打开新文章。", "success");
      finishProgress(100, "任务完成", "你现在可以直接查看正文、Token 与估算费用。");
      setIntakeBusy(false);
      return;
    }

    if (job.status === "failed") {
      finishProgress(job.progress || 0, "任务失败", job.error || job.message || "后台处理失败。");
      setIntakeBusy(false);
      throw new Error(job.error || job.message || "后台处理失败");
    }

    await sleep(1200);
  }
}

function statusLabel(job) {
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
    nodes.ingestStatus.className = "ingest-status hidden";
    nodes.ingestStatus.textContent = "";
    return;
  }
  nodes.ingestStatus.className = `ingest-status ${tone || ""}`;
  nodes.ingestStatus.textContent = message;
}

function setProgress(percent, label, hint) {
  nodes.progressShell.classList.remove("hidden");
  nodes.progressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressLabel.textContent = label;
  nodes.progressPercent.textContent = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressHint.textContent = hint || "";
}

function finishProgress(percent = 100, label = "完成", hint = "") {
  setProgress(percent, label, hint);
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

function formatUsd(value) {
  return Number(value || 0).toFixed(4);
}

function formatUsdCompact(value) {
  const numeric = Number(value || 0);
  if (!numeric) {
    return "$0.0000";
  }
  return `$${numeric.toFixed(4)}`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function formatTokenCompact(value) {
  const numeric = Number(value || 0);
  if (!numeric) {
    return "0 tokens";
  }
  return `${formatNumber(numeric)} tokens`;
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

bindUploadInteractions();

bootstrap().catch((error) => {
  nodes.articleList.innerHTML = `<div class="article-card muted">加载失败：${escapeHtml(error.message || "unknown error")}</div>`;
});
