const state = {
  library: [],
  activeArticleId: null,
  selectedDate: "all",
  selectedTopic: "all",
  search: "",
  dates: [],
  topics: [],
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
  const dates = state.dates;
  const topics = state.topics;
  nodes.dateFilters.innerHTML = "";
  nodes.topicFilters.innerHTML = "";

  nodes.dateFilters.appendChild(createFilterChip("全部", state.selectedDate === "all", () => {
    state.selectedDate = "all";
    renderArticleList();
    renderFilters();
  }));

  dates.forEach((dateValue) => {
    nodes.dateFilters.appendChild(createFilterChip(dateValue, state.selectedDate === dateValue, () => {
      state.selectedDate = dateValue;
      renderArticleList();
      renderFilters();
    }));
  });

  nodes.topicFilters.appendChild(createFilterChip("全部", state.selectedTopic === "all", () => {
    state.selectedTopic = "all";
    renderArticleList();
    renderFilters();
  }));

  topics.forEach((topic) => {
    nodes.topicFilters.appendChild(createFilterChip(`${topic.name} (${topic.count})`, state.selectedTopic === topic.name, () => {
      state.selectedTopic = topic.name;
      renderArticleList();
      renderFilters();
    }));
  });
}

function createFilterChip(label, active, onClick) {
  const button = document.createElement("button");
  button.className = `chip ${active ? "active" : ""}`;
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderArticleList() {
  const filtered = state.library.filter((article) => {
    const dateMatch = state.selectedDate === "all" || (article.archive_date || "") === state.selectedDate;
    const topicMatch = state.selectedTopic === "all" || (article.tags || []).includes(state.selectedTopic);
    const textBlob = `${article.title} ${article.summary}`.toLowerCase();
    const searchMatch = !state.search || textBlob.includes(state.search.toLowerCase());
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
    button.innerHTML = `
      <div class="article-source">${article.source.toUpperCase()}</div>
      <div class="article-title">${escapeHtml(article.title)}</div>
      <div class="article-meta">${article.archive_date || ""} · ${(article.tags || []).slice(0, 3).join(" / ")}</div>
      <div class="article-excerpt">${escapeHtml((article.article_excerpt || article.summary || "").slice(0, 160))}</div>
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
  nodes.emptyState.classList.add("hidden");
  nodes.articleView.classList.remove("hidden");
  nodes.heroTitle.textContent = article.title;
  nodes.summaryBlock.innerHTML = `<p>${escapeHtml(article.summary || "暂无摘要")}</p>`;
  nodes.articleBody.innerHTML = article.rendered_html || "<p>暂无正文。</p>";

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
    link.className = "action-button subtle";
    link.href = file.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = file.name;
    nodes.fileActions.appendChild(link);
  });
}

async function handleUrlIngest() {
  const url = nodes.ingestUrlInput.value.trim();
  if (!url) {
    showStatus("请输入 arXiv 链接或普通网址。", "error");
    return;
  }

  setIntakeBusy(true);
  showStatus("正在抓取并交给 Gemini 解析，这一步可能需要几十秒。", "pending");

  try {
    const response = await fetch("/api/ingest/url", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "URL 导入失败");
    }
    nodes.ingestUrlInput.value = "";
    await refreshLibrary();
    state.activeArticleId = payload.article_id;
    renderArticleList();
    renderArticle(payload);
    showStatus("URL 物料已完成解析并归档。", "success");
  } catch (error) {
    showStatus(error.message || "URL 导入失败。", "error");
  } finally {
    setIntakeBusy(false);
  }
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
  showStatus(`正在上传并解析 ${file.name}，这一步可能需要几十秒。`, "pending");

  const formData = new FormData();
  formData.append("file", file, file.name);

  try {
    const response = await fetch("/api/ingest/pdf", {
      method: "POST",
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "PDF 导入失败");
    }
    nodes.pdfUploadInput.value = "";
    await refreshLibrary();
    state.activeArticleId = payload.article_id;
    renderArticleList();
    renderArticle(payload);
    showStatus("PDF 已完成上传、解析并归档。", "success");
  } catch (error) {
    showStatus(error.message || "PDF 导入失败。", "error");
  } finally {
    setIntakeBusy(false);
  }
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

nodes.searchInput.addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderArticleList();
});

nodes.ingestUrlButton.addEventListener("click", handleUrlIngest);
nodes.ingestUrlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    handleUrlIngest();
  }
});

bindUploadInteractions();

bootstrap().catch((error) => {
  nodes.articleList.innerHTML = `<div class="article-card muted">加载失败：${escapeHtml(error.message || "unknown error")}</div>`;
});
