const state = {
  library: [],
  topics: [],
  activeArticleId: null,
  selectedFolder: "all",
  search: "",
  activeJobId: null,
  activeJobStartedAt: 0,
  ingestSuggestions: [],
  flomoDraft: null,
  selectionDraft: null,
  sidebarCollapsed: false,
  workspace: "library",
  viewMode: "both",
  articleHasPdf: false,
  chatOptions: { available: false, default_model_key: "flash", models: [] },
  chatModelKey: "flash",
  chatArticleId: null,
  chatSessionId: null,
  chatMessages: [],
  chatPending: false,
  chatCache: null,
  pendingChatPlaceholder: null,
  chatForceNewSession: false,
};

const SIDEBAR_WIDTH_STORAGE_KEY = "research-agent-sidebar-width";
const DEFAULT_SIDEBAR_RATIO = 0.3;
const MAX_SIDEBAR_RATIO = 0.6;
const MIN_SIDEBAR_WIDTH = 300;

const nodes = {
  appShell: document.getElementById("appShell"),
  layout: document.querySelector(".layout"),
  layoutDivider: document.getElementById("layoutDivider"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  openIngestModal: document.getElementById("openIngestModal"),
  librarySidebar: document.getElementById("librarySidebar"),
  chatSidebar: document.getElementById("chatSidebar"),
  libraryBrowser: document.getElementById("libraryBrowser"),
  libraryGrid: document.getElementById("libraryGrid"),
  articleList: document.getElementById("articleList"),
  articleCount: document.getElementById("articleCount"),
  folderTree: document.getElementById("folderTree"),
  searchInput: document.getElementById("searchInput"),
  emptyState: document.getElementById("emptyState"),
  articleView: document.getElementById("articleView"),
  libraryHeader: document.getElementById("libraryHeader"),
  heroTitle: document.getElementById("heroTitle"),
  metaBadges: document.getElementById("metaBadges"),
  viewToggle: document.getElementById("viewToggle"),
  saveSummaryToFlomo: document.getElementById("saveSummaryToFlomo"),
  summaryText: document.getElementById("summaryText"),
  usageInline: document.getElementById("usageInline"),
  articleBody: document.getElementById("articleBody"),
  fileActions: document.getElementById("fileActions"),
  sourceLink: document.getElementById("sourceLink"),
  previewStrip: document.getElementById("previewStrip"),
  previewTitle: document.getElementById("previewTitle"),
  pdfPreviewGallery: document.getElementById("pdfPreviewGallery"),
  imageLightbox: document.getElementById("imageLightbox"),
  lightboxImage: document.getElementById("lightboxImage"),
  lightboxTitle: document.getElementById("lightboxTitle"),
  lightboxClose: document.getElementById("lightboxClose"),
  pdfPane: document.getElementById("pdfPane"),
  pdfViewer: document.getElementById("pdfViewer"),
  pdfEmpty: document.getElementById("pdfEmpty"),
  pdfCaption: document.getElementById("pdfCaption"),
  ingestUrlInput: document.getElementById("ingestUrlInput"),
  ingestUrlButton: document.getElementById("ingestUrlButton"),
  ingestSuggestions: document.getElementById("ingestSuggestions"),
  uploadDropzone: document.getElementById("uploadDropzone"),
  pdfUploadInput: document.getElementById("pdfUploadInput"),
  ingestStatus: document.getElementById("ingestStatus"),
  progressShell: document.getElementById("progressShell"),
  progressFill: document.getElementById("progressFill"),
  progressLabel: document.getElementById("progressLabel"),
  progressPercent: document.getElementById("progressPercent"),
  progressHint: document.getElementById("progressHint"),
  ingestModal: document.getElementById("ingestModal"),
  closeIngestModal: document.getElementById("closeIngestModal"),
  selectionFlomoButton: document.getElementById("selectionFlomoButton"),
  toastNotice: document.getElementById("toastNotice"),
  flomoModal: document.getElementById("flomoModal"),
  closeFlomoModal: document.getElementById("closeFlomoModal"),
  flomoPreviewInput: document.getElementById("flomoPreviewInput"),
  cancelFlomoSave: document.getElementById("cancelFlomoSave"),
  confirmFlomoSave: document.getElementById("confirmFlomoSave"),
  chatArticleSelect: document.getElementById("chatArticleSelect"),
  chatModelSelect: document.getElementById("chatModelSelect"),
  chatContextHint: document.getElementById("chatContextHint"),
  chatMessages: document.getElementById("chatMessages"),
  chatComposer: document.getElementById("chatComposer"),
  chatInput: document.getElementById("chatInput"),
  chatStatus: document.getElementById("chatStatus"),
  chatSendButton: document.getElementById("chatSendButton"),
  chatResetButton: document.getElementById("chatResetButton"),
};

let ingestSuggestionTimer = null;
let ingestSuggestionRequestId = 0;
let toastTimer = null;
let toastFadeTimer = null;

async function bootstrap() {
  loadSidebarWidthPreference();
  await Promise.all([refreshLibrary(), refreshChatOptions()]);
  await loadPersistedChatSession();
  renderWorkspace();
}

async function refreshLibrary() {
  const response = await fetch("/api/library");
  const payload = await response.json();
  state.library = payload.articles || [];
  state.topics = payload.topics || [];
  if (!state.chatArticleId && state.library.length) {
    state.chatArticleId = state.activeArticleId || state.library[0].article_id;
  }
  renderFolders();
  renderArticleList();
  renderLibraryBrowser();
  renderChatSidebar();
  renderChatView();
}

async function refreshChatOptions() {
  try {
    const response = await fetch("/api/chat/options");
    const payload = await response.json();
    if (response.ok) {
      state.chatOptions = payload;
      state.chatModelKey = payload.default_model_key || "flash";
    }
  } catch (error) {
    state.chatOptions = { available: false, default_model_key: "flash", models: [] };
  }
  renderChatSidebar();
  renderChatView();
}

function renderWorkspace() {
  hideSelectionFlomoButton();
  document.querySelectorAll(".workspace-button").forEach((button) => {
    const isActive = button.dataset.workspace === "library"
      ? state.workspace === "library" || state.workspace === "reader"
      : button.dataset.workspace === state.workspace;
    button.classList.toggle("active", isActive);
  });

  const showChatSidebar = state.workspace === "chat";
  const showReader = state.workspace === "reader" || (state.workspace === "chat" && Boolean(state.activeArticleId));
  const showLibraryBrowser = !showReader;
  nodes.librarySidebar.classList.toggle("hidden", showChatSidebar);
  nodes.chatSidebar.classList.toggle("hidden", !showChatSidebar);
  nodes.libraryBrowser.classList.toggle("hidden", !showLibraryBrowser);
  nodes.libraryHeader.classList.toggle("hidden", !showReader);
  nodes.emptyState.classList.add("hidden");
  nodes.articleView.classList.toggle("hidden", !showReader || !state.activeArticleId);
  renderChatView();
}

function setWorkspace(mode) {
  if (mode === "chat") {
    state.workspace = "chat";
    ensureChatSidebarWidth();
    const targetArticleId = state.chatArticleId || state.activeArticleId || (state.library[0] && state.library[0].article_id) || null;
    renderWorkspace();
    if (targetArticleId && targetArticleId !== state.activeArticleId) {
      void loadArticle(targetArticleId, "chat");
      return;
    }
    if (targetArticleId) {
      void loadPersistedChatSession();
    }
    return;
  }
  state.workspace = "library";
  renderWorkspace();
}

function renderFolders() {
  nodes.folderTree.innerHTML = "";

  const typeSection = document.createElement("div");
  typeSection.className = "folder-section";
  const typeTitle = document.createElement("div");
  typeTitle.className = "folder-section-title";
  typeTitle.textContent = "资料类型";
  typeSection.appendChild(typeTitle);

  buildPrimaryFolders().forEach((entry) => {
    typeSection.appendChild(createFolderButton(entry));
  });
  nodes.folderTree.appendChild(typeSection);

  if (!state.topics.length) {
    return;
  }

  const topicSection = document.createElement("div");
  topicSection.className = "folder-section";
  const topicTitle = document.createElement("div");
  topicTitle.className = "folder-section-title";
  topicTitle.textContent = "主题文件夹";
  topicSection.appendChild(topicTitle);

  state.topics.forEach((topic) => {
    topicSection.appendChild(createFolderButton({
      key: `topic:${topic.name}`,
      label: topic.name,
      count: topic.count,
      kind: "topic",
    }));
  });
  nodes.folderTree.appendChild(topicSection);
}

function buildPrimaryFolders() {
  const paperCount = state.library.filter((article) => isPdfBackedArticle(article)).length;
  const webCount = state.library.filter((article) => isHtmlOnlyArticle(article)).length;
  const arxivCount = state.library.filter((article) => String(article.source || "").includes("arxiv")).length;
  return [
    { key: "all", label: "全部文库", count: state.library.length, kind: "root" },
    { key: "papers", label: "PDF 论文", count: paperCount, kind: "paper" },
    { key: "web", label: "网页 / HTML", count: webCount, kind: "web" },
    { key: "arxiv", label: "arXiv", count: arxivCount, kind: "arxiv" },
  ];
}

function createFolderButton(entry) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `folder-button ${state.selectedFolder === entry.key ? "active" : ""}`;
  button.innerHTML = `
    <span class="folder-button-main">
      <span class="folder-glyph">${folderGlyph(entry.kind)}</span>
      <span class="folder-label">${escapeHtml(entry.label)}</span>
    </span>
    <span class="folder-count">${entry.count}</span>
  `;
  button.addEventListener("click", () => {
    state.selectedFolder = entry.key;
    renderFolders();
    renderArticleList();
    renderLibraryBrowser();
  });
  return button;
}

function folderGlyph(kind) {
  if (kind === "paper") {
    return "▣";
  }
  if (kind === "web") {
    return "◫";
  }
  if (kind === "arxiv") {
    return "◇";
  }
  if (kind === "topic") {
    return "▾";
  }
  return "▤";
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
    const pathLabel = buildArticleFolderPath(article);
    const arxivId = getArticleArxivId(article);
    button.innerHTML = `
      <div class="card-title">${escapeHtml(article.title || "Untitled")}</div>
      <div class="card-excerpt">${escapeHtml((article.summary || "").slice(0, 168))}</div>
      <div class="card-path">${escapeHtml(pathLabel)}</div>
      ${arxivId ? `<div class="card-arxiv-id">arXiv ${escapeHtml(arxivId)}</div>` : ""}
      <div class="card-mini">
        <span>${compactTokens(usage.total_tokens || 0)}</span>
        <span>${compactUsd(usage.estimated_cost_usd || 0)}</span>
      </div>
    `;
    button.addEventListener("click", () => loadArticle(article.article_id));
    nodes.articleList.appendChild(button);
  });
}

function renderLibraryBrowser() {
  const filtered = getFilteredArticles();
  nodes.libraryGrid.innerHTML = "";

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "library-browser-empty";
    empty.textContent = "当前筛选条件下没有可展示的论文。";
    nodes.libraryGrid.appendChild(empty);
    return;
  }

  filtered.forEach((article) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `library-browser-card ${state.activeArticleId === article.article_id ? "selected" : ""}`;
    const usage = article.llm_usage || {};
    const pathLabel = buildArticleFolderPath(article);
    const arxivId = getArticleArxivId(article);
    const tags = (article.display_tags || []).slice(0, 4)
      .map((tag) => `<span class="card-tag">${escapeHtml(tag)}</span>`)
      .join("");
    button.innerHTML = `
      <div class="library-browser-title">${escapeHtml(article.title || "Untitled")}</div>
      <div class="library-browser-summary">${escapeHtml((article.summary || "").slice(0, 220))}</div>
      <div class="library-browser-path">${escapeHtml(pathLabel)}</div>
      ${arxivId ? `<div class="library-browser-arxiv">arXiv ${escapeHtml(arxivId)}</div>` : ""}
      ${tags ? `<div class="card-tags">${tags}</div>` : ""}
      <div class="library-browser-footer">
        <span>${compactTokens(usage.total_tokens || 0)} · ${compactUsd(usage.estimated_cost_usd || 0)}</span>
        <span>进入阅读</span>
      </div>
    `;
    button.addEventListener("click", () => loadArticle(article.article_id, "reader"));
    nodes.libraryGrid.appendChild(button);
  });
}

function getFilteredArticles() {
  return state.library.filter((article) => {
    const folderMatch = matchesFolder(article, state.selectedFolder);
    const textBlob = `${article.title || ""} ${article.summary || ""}`.toLowerCase();
    const searchMatch = !state.search || textBlob.includes(state.search.toLowerCase());
    return folderMatch && searchMatch;
  });
}

function matchesFolder(article, folderKey) {
  if (!folderKey || folderKey === "all") {
    return true;
  }
  if (folderKey === "papers") {
    return isPdfBackedArticle(article);
  }
  if (folderKey === "web") {
    return isHtmlOnlyArticle(article);
  }
  if (folderKey === "arxiv") {
    return String(article.source || "").includes("arxiv");
  }
  if (folderKey.startsWith("topic:")) {
    const topicName = folderKey.slice("topic:".length);
    return (article.display_tags || []).includes(topicName);
  }
  return true;
}

function isPdfBackedArticle(article) {
  return (article.source_files || []).some((entry) => entry.name === "source.pdf");
}

function isHtmlOnlyArticle(article) {
  const hasPdf = isPdfBackedArticle(article);
  const hasHtml = (article.source_files || []).some((entry) => entry.name === "source.html");
  return !hasPdf && hasHtml;
}

function buildArticleFolderPath(article) {
  const segments = [isPdfBackedArticle(article) ? "PDF 论文" : "网页 / HTML"];
  const topic = (article.display_tags || [])[0];
  if (topic) {
    segments.push(topic);
  }
  return segments.join(" / ");
}

function getArticleArxivId(article) {
  const direct = String(article.arxiv_id || "").trim();
  if (direct) {
    return direct;
  }
  const candidates = [
    article.identifier || "",
    article.source_url || "",
    (article.meta && article.meta.arxiv_id) || "",
  ];
  for (const candidate of candidates) {
    const match = String(candidate).match(/(\d{4}\.\d{4,5}(?:v\d+)?)/);
    if (match) {
      return match[1];
    }
  }
  return "";
}

async function loadArticle(articleId, nextWorkspace = "reader") {
  state.activeArticleId = articleId;
  state.chatArticleId = articleId;
  renderArticleList();
  renderLibraryBrowser();
  renderChatSidebar();

  const response = await fetch(`/api/articles/${articleId}`);
  if (!response.ok) {
    showStatus("文章加载失败。", "error");
    return;
  }
  const article = await response.json();
  state.workspace = nextWorkspace;
  renderArticle(article);
  renderWorkspace();
}

function renderArticle(article) {
  hideSelectionFlomoButton();
  state.activeArticleId = article.article_id;
  state.chatArticleId = article.article_id;
  state.articleHasPdf = Boolean(article.pdf_source_url);
  if (!state.articleHasPdf && state.viewMode !== "analysis") {
    state.viewMode = "analysis";
  }
  renderArticleList();
  renderLibraryBrowser();
  renderChatSidebar();

  nodes.emptyState.classList.add("hidden");
  nodes.articleView.classList.remove("hidden");
  nodes.heroTitle.textContent = article.title || "Untitled";

  renderMetaBadges(article);
  renderFileActions(article);
  renderUsageInline(article.llm_usage || {});
  applyViewMode();

  nodes.summaryText.textContent = article.summary || "暂无摘要。";
  nodes.articleBody.innerHTML = article.rendered_html || "<p>暂无正文。</p>";
  renderPdfPane(article);
  renderVisualGallery(article);
  if (state.workspace === "chat") {
    renderChatView();
  }
}

function renderMetaBadges(article) {
  nodes.metaBadges.innerHTML = "";
  (article.display_tags || []).slice(0, 6).forEach((value) => {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `#${value}`;
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
  (article.display_source_files || []).forEach((file) => {
    const link = document.createElement("a");
    link.className = "subtle-link";
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

function renderPdfPane(article) {
  const pdfUrl = article.pdf_source_url || "";
  if (!pdfUrl) {
    nodes.pdfViewer.classList.add("hidden");
    nodes.pdfEmpty.classList.remove("hidden");
    nodes.pdfCaption.textContent = "当前文章没有可用的 PDF 原文。";
    return;
  }

  nodes.pdfViewer.classList.remove("hidden");
  nodes.pdfEmpty.classList.add("hidden");
  nodes.pdfViewer.src = buildPdfViewerUrl(pdfUrl, 1);
  nodes.pdfCaption.textContent = "正文中的页码标记可直接跳转到原始 PDF。";

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
      button.addEventListener("click", () => openLightbox(figure.url, figure.title || figure.source_name || "论文配图"));
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

function renderChatSidebar() {
  renderChatArticleOptions();
  renderChatModelOptions();
  updateChatContextHint();
}

function renderChatArticleOptions() {
  if (!nodes.chatArticleSelect) {
    return;
  }
  const selected = state.chatArticleId || state.activeArticleId || (state.library[0] && state.library[0].article_id) || "";
  if (selected) {
    state.chatArticleId = selected;
  }
  nodes.chatArticleSelect.innerHTML = "";
  state.library.forEach((article) => {
    const option = document.createElement("option");
    option.value = article.article_id;
    option.textContent = article.title || "Untitled";
    option.selected = article.article_id === state.chatArticleId;
    nodes.chatArticleSelect.appendChild(option);
  });
  nodes.chatArticleSelect.disabled = !state.library.length;
}

function renderChatModelOptions() {
  if (!nodes.chatModelSelect) {
    return;
  }
  const options = state.chatOptions.models || [];
  nodes.chatModelSelect.innerHTML = "";
  options.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.key;
    option.textContent = model.label;
    option.selected = model.key === state.chatModelKey;
    nodes.chatModelSelect.appendChild(option);
  });
}

function renderChatView() {
  const currentArticle = getChatArticle();
  nodes.chatComposer.classList.toggle("disabled", !currentArticle || !state.chatOptions.available);
  nodes.chatInput.disabled = !currentArticle || !state.chatOptions.available || state.chatPending;
  nodes.chatSendButton.disabled = !currentArticle || !state.chatOptions.available || state.chatPending;

  if (!state.chatOptions.available) {
    nodes.chatStatus.textContent = "Gemini API 尚未配置";
  } else if (state.chatPending) {
    nodes.chatStatus.textContent = "Gemini 正在回答，首次会准备上下文缓存";
  } else {
    nodes.chatStatus.textContent = currentArticle ? "就绪" : "请选择一篇论文";
  }

  renderChatMessages();
}

function renderChatMessages() {
  nodes.chatMessages.innerHTML = "";
  const visibleMessages = state.pendingChatPlaceholder
    ? [...state.chatMessages, state.pendingChatPlaceholder]
    : state.chatMessages;

  if (!visibleMessages.length) {
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.textContent = state.chatArticleId
      ? "可以直接追问算法细节、关键实验、工程实现，系统会优先引用原文上下文。"
      : "先选择一篇论文，再开始提问。";
    nodes.chatMessages.appendChild(empty);
    return;
  }

  visibleMessages.forEach((message) => {
    const row = document.createElement("div");
    row.className = `chat-message ${message.role}${message.pending ? " pending" : ""}${message.error ? " error" : ""}`;
    const meta = buildChatMessageMeta(message);
    if (message.pending) {
      row.innerHTML = `
        <div class="chat-bubble">
          <div class="chat-role">ResearchAgent</div>
          <div class="typing-indicator" aria-label="Gemini 正在思考">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
          </div>
        </div>
      `;
    } else {
      const textContent = message.role === "assistant" && message.rendered_html
        ? `<div class="chat-text markdown">${message.rendered_html}</div>`
        : `<div class="chat-text">${escapeHtml(message.text || "")}</div>`;
      row.innerHTML = `
        <div class="chat-bubble">
          <div class="chat-role">${message.role === "user" ? "你" : "ResearchAgent"}</div>
          ${textContent}
          ${meta ? `<div class="chat-bubble-meta">${meta}</div>` : ""}
        </div>
      `;
    }
    nodes.chatMessages.appendChild(row);
  });
  nodes.chatMessages.scrollTop = nodes.chatMessages.scrollHeight;
}

function buildChatMessageMeta(message) {
  if (message.role !== "assistant" || !message.usage) {
    return "";
  }
  const usage = message.usage;
  const pieces = [];
  const modelLabel = describeUsageModel(usage.model);
  if (modelLabel) {
    pieces.push(modelLabel);
  }
  if (Number(usage.estimated_cost_usd || 0) > 0) {
    pieces.push(`本轮 $${formatUsd(usage.estimated_cost_usd)}`);
  }
  if (usage.prompt_tokens) {
    pieces.push(`输入 ${formatNumber(usage.prompt_tokens)}`);
  }
  if (usage.output_tokens) {
    pieces.push(`输出 ${formatNumber(usage.output_tokens)}`);
  }
  return pieces.join(" · ");
}

function describeUsageModel(modelName) {
  const match = (state.chatOptions.models || []).find((entry) => entry.api_name === modelName);
  if (match) {
    return match.label;
  }
  return modelName || "";
}

function updateChatContextHint() {
  const article = getChatArticle();
  if (!article) {
    nodes.chatContextHint.textContent = "对 arXiv / PDF 条目会优先准备原文缓存上下文。";
    return;
  }
  const hasPdf = (article.source_files || []).some((entry) => entry.name === "source.pdf");
  nodes.chatContextHint.textContent = hasPdf
    ? "当前条目包含原始 PDF，聊天会优先用 Gemini cache 固定原文上下文。"
    : "当前条目没有原始 PDF，聊天会优先使用已生成的解析正文作为上下文。";
}

function getChatArticle() {
  return state.library.find((article) => article.article_id === state.chatArticleId) || null;
}

function getChatModel() {
  return (state.chatOptions.models || []).find((model) => model.key === state.chatModelKey) || null;
}

function describeCache(cache) {
  if (!cache) {
    return "等待提问后建立上下文";
  }
  if (cache.status === "ready" && cache.kind === "pdf") {
    return "PDF cache 已就绪";
  }
  if (cache.status === "ready" && cache.kind === "article") {
    return "解析正文 cache 已就绪";
  }
  if (cache.status === "uploaded-file") {
    return "已上传 PDF，上下文复用中";
  }
  return "使用内联上下文";
}

function resetChatSession() {
  state.chatSessionId = null;
  state.chatMessages = [];
  state.chatCache = null;
  state.pendingChatPlaceholder = null;
  state.chatForceNewSession = false;
}

function startFreshChatSession() {
  resetChatSession();
  state.chatForceNewSession = true;
}

async function loadPersistedChatSession() {
  const article = getChatArticle();
  if (!article) {
    resetChatSession();
    renderChatView();
    return;
  }

  try {
    const params = new URLSearchParams({
      article_id: article.article_id,
      model: state.chatModelKey,
    });
    const response = await fetch(`/api/chat/session?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "会话加载失败");
    }
    state.chatSessionId = payload.session_id || null;
    state.chatMessages = payload.messages || [];
    state.chatCache = payload.cache || null;
    state.pendingChatPlaceholder = null;
    state.chatForceNewSession = false;
  } catch (error) {
    resetChatSession();
  }
  renderChatView();
}

async function sendChatMessage() {
  const article = getChatArticle();
  if (!article) {
    renderChatView();
    return;
  }
  const message = nodes.chatInput.value.trim();
  if (!message) {
    nodes.chatStatus.textContent = "请输入问题";
    return;
  }

  const previousSessionId = state.chatSessionId;
  const previousCache = state.chatCache;
  const previousForceNewSession = state.chatForceNewSession;
  const draft = message;
  const userMessage = {
    role: "user",
    text: draft,
    created_at: new Date().toISOString(),
  };
  state.chatPending = true;
  state.pendingChatPlaceholder = {
    role: "assistant",
    pending: true,
  };
  state.chatMessages = [
    ...state.chatMessages,
    userMessage,
  ];
  nodes.chatInput.value = "";
  renderChatView();

  try {
    const response = await fetch("/api/chat/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        article_id: article.article_id,
        message: draft,
        model: state.chatModelKey,
        session_id: state.chatSessionId,
        new_session: state.chatForceNewSession,
      }),
    });
    const payload = await readJsonPayload(response);
    if (!response.ok) {
      throw new Error(payload.detail || "聊天请求失败");
    }
    state.chatSessionId = payload.session_id;
    state.chatMessages = payload.messages || [];
    state.chatCache = payload.cache || null;
    state.pendingChatPlaceholder = null;
    state.chatForceNewSession = false;
  } catch (error) {
    state.chatMessages = previousMessages;
    state.chatSessionId = previousSessionId;
    state.chatCache = previousCache;
    state.pendingChatPlaceholder = null;
    state.chatForceNewSession = previousForceNewSession;
    state.chatMessages = [
      ...state.chatMessages,
      {
        role: "assistant",
        text: buildChatFailureMessage(error, state.chatModelKey),
        created_at: new Date().toISOString(),
        error: true,
      },
    ];
  } finally {
    state.chatPending = false;
    renderChatView();
  }
}

async function readJsonPayload(response) {
  const rawText = await response.text();
  if (!rawText) {
    return {};
  }
  try {
    return JSON.parse(rawText);
  } catch (_error) {
    return {
      detail: response.ok ? "" : "服务返回了无法解析的响应，请稍后重试。",
    };
  }
}

function buildChatFailureMessage(error, modelKey) {
  const model = (state.chatOptions.models || []).find((entry) => entry.key === modelKey);
  const modelLabel = model ? model.label : "Gemini";
  const raw = String(error && error.message ? error.message : "").trim();
  if (raw) {
    return raw;
  }
  return `${modelLabel} 暂时没有返回结果，请稍后重试，或先切换到 Flash。`;
}

async function startUrlIngest() {
  const rawInput = nodes.ingestUrlInput.value.trim();
  if (!rawInput) {
    showStatus("请输入 arXiv 链接或普通网址。", "error");
    return;
  }
  let url = rawInput;
  if (!isLikelyUrl(rawInput) && state.ingestSuggestions.length) {
    url = state.ingestSuggestions[0].abs_url;
    nodes.ingestUrlInput.value = url;
  }
  if (!isLikelyUrl(url)) {
    showStatus("请输入有效 URL，或从下拉建议中选择一篇 arXiv 论文。", "error");
    return;
  }

  setIntakeBusy(true);
  clearIngestSuggestions();
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
    closeIngestModal();
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
    closeIngestModal();
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
      setWorkspace("library");
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

function openIngestModal() {
  nodes.ingestModal.classList.remove("hidden");
  syncOverlayState();
  nodes.ingestUrlInput.focus();
}

function closeIngestModal() {
  nodes.ingestModal.classList.add("hidden");
  syncOverlayState();
  clearIngestSuggestions();
  if (nodes.ingestUrlButton.disabled) {
    return;
  }
  showStatus("", "");
  nodes.progressShell.classList.add("hidden");
}

function openLightbox(imageUrl, title) {
  nodes.lightboxImage.src = imageUrl;
  nodes.lightboxImage.alt = title || "图像预览";
  nodes.lightboxTitle.textContent = title || "图像预览";
  nodes.imageLightbox.classList.remove("hidden");
  syncOverlayState();
}

function closeLightbox() {
  nodes.imageLightbox.classList.add("hidden");
  nodes.lightboxImage.src = "";
  syncOverlayState();
}

function syncOverlayState() {
  const hasOverlay = !nodes.imageLightbox.classList.contains("hidden")
    || !nodes.ingestModal.classList.contains("hidden")
    || !nodes.flomoModal.classList.contains("hidden");
  document.body.classList.toggle("lightbox-open", hasOverlay);
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

function showToast(message) {
  if (!message) {
    return;
  }
  if (toastTimer) {
    window.clearTimeout(toastTimer);
    toastTimer = null;
  }
  if (toastFadeTimer) {
    window.clearTimeout(toastFadeTimer);
    toastFadeTimer = null;
  }
  nodes.toastNotice.textContent = message;
  nodes.toastNotice.classList.remove("hidden", "fading");
  toastFadeTimer = window.setTimeout(() => {
    nodes.toastNotice.classList.add("fading");
  }, 1800);
  toastTimer = window.setTimeout(() => {
    nodes.toastNotice.classList.add("hidden");
    nodes.toastNotice.classList.remove("fading");
  }, 2150);
}

function setProgress(percent, label, hint) {
  nodes.progressShell.classList.remove("hidden");
  nodes.progressFill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressLabel.textContent = label;
  nodes.progressPercent.textContent = `${Math.max(0, Math.min(100, percent))}%`;
  nodes.progressHint.textContent = hint || "";
}

async function buildFlomoPreview(content, sourceKind, articleId = null) {
  const text = sourceKind === "selection"
    ? sanitizeFlomoSelectionText(content)
    : String(content || "").trim();
  if (!text) {
    throw new Error("没有可保存的内容");
  }
  const response = await fetch("/api/integrations/flomo/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: text,
      article_id: articleId || state.activeArticleId || state.chatArticleId || null,
      source_kind: sourceKind,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "生成 Flomo 预览失败");
  }
  return payload.content || "";
}

function sanitizeFlomoSelectionText(content) {
  let text = String(content || "");
  text = text.replace(/\[\s*P\d{1,4}(?:\s*,\s*P\d{1,4})*\s*\]/g, " ");
  text = text.replace(/(^|[\s([{\u3000])P\d{1,4}(?=$|[\s)\]}，。；：、,.!?！？])/g, "$1");
  text = text.replace(/\s+([，。；：、,.!?！？])/g, "$1");
  text = text.replace(/[ \t]+\n/g, "\n");
  text = text.replace(/\n{3,}/g, "\n\n");
  return text.trim();
}

function openFlomoModal(content, sourceKind) {
  state.flomoDraft = {
    articleId: state.activeArticleId || state.chatArticleId || null,
    sourceKind,
  };
  nodes.flomoPreviewInput.value = content;
  nodes.flomoModal.classList.remove("hidden");
  syncOverlayState();
  nodes.flomoPreviewInput.focus();
  nodes.flomoPreviewInput.setSelectionRange(nodes.flomoPreviewInput.value.length, nodes.flomoPreviewInput.value.length);
}

function closeFlomoModal() {
  state.flomoDraft = null;
  nodes.flomoModal.classList.add("hidden");
  syncOverlayState();
}

async function saveSnippetToFlomo(content, sourceKind, formatted = false, articleId = null) {
  const text = String(content || "").trim();
  if (!text) {
    throw new Error("没有可保存的内容");
  }
  try {
    const response = await fetch("/api/integrations/flomo/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text,
        article_id: articleId || state.activeArticleId || state.chatArticleId || null,
        source_kind: sourceKind,
        formatted,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "保存到 Flomo 失败");
    }
    return payload;
  } catch (error) {
    throw error;
  }
}

function hideSelectionFlomoButton() {
  state.selectionDraft = null;
  nodes.selectionFlomoButton.classList.add("hidden");
  nodes.selectionFlomoButton.textContent = "保存至 Flomo";
}

function selectionSourceKind(element) {
  if (element.closest("#chatMessages")) {
    return "chat";
  }
  if (element.closest(".summary-stack")) {
    return "summary";
  }
  return "selection";
}

function allowedSelectionRoot(node) {
  if (!(node instanceof Node)) {
    return null;
  }
  const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  if (!(element instanceof HTMLElement)) {
    return null;
  }
  return element.closest("#articleBody, #summaryText, #chatMessages");
}

function updateSelectionFlomoButton() {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    hideSelectionFlomoButton();
    return;
  }

  const root = allowedSelectionRoot(selection.anchorNode);
  const focusRoot = allowedSelectionRoot(selection.focusNode);
  if (!root || !focusRoot || root !== focusRoot) {
    hideSelectionFlomoButton();
    return;
  }

  const text = selection.toString().replace(/\r\n/g, "\n").trim();
  if (text.length < 8) {
    hideSelectionFlomoButton();
    return;
  }

  const rect = selection.getRangeAt(0).getBoundingClientRect();
  if (!rect.width && !rect.height) {
    hideSelectionFlomoButton();
    return;
  }

  state.selectionDraft = {
    text,
    sourceKind: selectionSourceKind(root),
  };

  const top = Math.max(12, rect.top + window.scrollY - 46);
  const left = Math.min(
    window.scrollX + window.innerWidth - 144,
    Math.max(12, rect.left + window.scrollX + rect.width / 2 - 56)
  );
  nodes.selectionFlomoButton.style.top = `${top}px`;
  nodes.selectionFlomoButton.style.left = `${left}px`;
  nodes.selectionFlomoButton.classList.remove("hidden");
}

function clearIngestSuggestions() {
  state.ingestSuggestions = [];
  nodes.ingestSuggestions.innerHTML = "";
  nodes.ingestSuggestions.classList.add("hidden");
}

function renderIngestSuggestions() {
  nodes.ingestSuggestions.innerHTML = "";
  if (!state.ingestSuggestions.length) {
    nodes.ingestSuggestions.classList.add("hidden");
    return;
  }

  state.ingestSuggestions.forEach((entry) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-card";
    const published = formatSuggestionDate(entry.published_at);
    button.innerHTML = `
      <div class="suggestion-title">${escapeHtml(entry.title || "Untitled")}</div>
      <div class="suggestion-meta">arXiv ${escapeHtml(entry.arxiv_id || "")}${published ? ` · ${escapeHtml(published)}` : ""}</div>
      <div class="suggestion-summary">${escapeHtml((entry.summary || "").slice(0, 180))}</div>
    `;
    button.addEventListener("click", () => {
      nodes.ingestUrlInput.value = entry.abs_url;
      state.ingestSuggestions = [entry];
      nodes.ingestSuggestions.innerHTML = "";
      nodes.ingestSuggestions.classList.add("hidden");
      showStatus(`已选中 arXiv ${entry.arxiv_id}，将优先导入 PDF 与源码。`, "pending");
    });
    nodes.ingestSuggestions.appendChild(button);
  });
  nodes.ingestSuggestions.classList.remove("hidden");
}

function formatSuggestionDate(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  return text.slice(0, 10);
}

function isLikelyUrl(value) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

async function fetchArxivSuggestions(query) {
  const requestId = ++ingestSuggestionRequestId;
  try {
    const response = await fetch(`/api/search/arxiv?q=${encodeURIComponent(query)}`);
    const payload = await response.json();
    if (requestId !== ingestSuggestionRequestId) {
      return;
    }
    if (!response.ok) {
      throw new Error(payload.detail || "arXiv 搜索失败");
    }
    state.ingestSuggestions = payload.results || [];
    renderIngestSuggestions();
  } catch (error) {
    if (requestId !== ingestSuggestionRequestId) {
      return;
    }
    clearIngestSuggestions();
  }
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
  if (!state.articleHasPdf && state.viewMode !== "analysis") {
    state.viewMode = "analysis";
  }
  nodes.appShell.classList.remove("view-mode-both", "view-mode-analysis", "view-mode-pdf");
  nodes.appShell.classList.add(`view-mode-${state.viewMode}`);
  [...nodes.viewToggle.querySelectorAll(".view-toggle-button")].forEach((button) => {
    const disabled = !state.articleHasPdf && button.dataset.mode !== "analysis";
    button.disabled = disabled;
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

function bindLightboxInteractions() {
  nodes.lightboxClose.addEventListener("click", closeLightbox);
  nodes.imageLightbox.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.closeLightbox === "true") {
      closeLightbox();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !nodes.imageLightbox.classList.contains("hidden")) {
      closeLightbox();
    }
    if (event.key === "Escape" && !nodes.ingestModal.classList.contains("hidden") && !nodes.ingestUrlButton.disabled) {
      closeIngestModal();
    }
    if (event.key === "Escape" && !nodes.flomoModal.classList.contains("hidden")) {
      closeFlomoModal();
    }
  });
}

function bindIngestModalInteractions() {
  nodes.openIngestModal.addEventListener("click", openIngestModal);
  nodes.closeIngestModal.addEventListener("click", closeIngestModal);
  nodes.ingestModal.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.closeIngest === "true" && !nodes.ingestUrlButton.disabled) {
      closeIngestModal();
    }
  });
}

function bindFlomoInteractions() {
  nodes.saveSummaryToFlomo.addEventListener("click", async () => {
    try {
      const preview = await buildFlomoPreview(
        nodes.summaryText.textContent || "",
        "summary",
        state.activeArticleId || state.chatArticleId || null,
      );
      openFlomoModal(preview, "summary");
    } catch (error) {
      nodes.chatStatus.textContent = error.message || "生成 Flomo 预览失败";
    }
  });

  nodes.selectionFlomoButton.addEventListener("click", async (event) => {
    event.preventDefault();
    if (!state.selectionDraft) {
      hideSelectionFlomoButton();
      return;
    }
    const draft = state.selectionDraft;
    try {
      const preview = await buildFlomoPreview(
        draft.text,
        draft.sourceKind,
        state.activeArticleId || state.chatArticleId || null,
      );
      openFlomoModal(preview, draft.sourceKind);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      hideSelectionFlomoButton();
    } catch (error) {
      nodes.chatStatus.textContent = error.message || "生成 Flomo 预览失败";
    }
  });

  nodes.closeFlomoModal.addEventListener("click", closeFlomoModal);
  nodes.cancelFlomoSave.addEventListener("click", closeFlomoModal);
  nodes.flomoModal.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.dataset.closeFlomo === "true") {
      closeFlomoModal();
    }
  });
  nodes.confirmFlomoSave.addEventListener("click", async () => {
    if (!state.flomoDraft) {
      closeFlomoModal();
      return;
    }
    try {
      await saveSnippetToFlomo(
        nodes.flomoPreviewInput.value,
        state.flomoDraft.sourceKind,
        true,
        state.flomoDraft.articleId,
      );
      closeFlomoModal();
      showToast("已保存到 Flomo");
    } catch (error) {
      nodes.chatStatus.textContent = error.message || "保存到 Flomo 失败";
    }
  });

  document.addEventListener("selectionchange", () => {
    window.setTimeout(updateSelectionFlomoButton, 0);
  });

  document.addEventListener("scroll", hideSelectionFlomoButton, true);
  window.addEventListener("resize", hideSelectionFlomoButton);
  document.addEventListener("mousedown", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target.closest("#selectionFlomoButton")) {
      return;
    }
    if (target.closest("#flomoModal")) {
      return;
    }
    if (!target.closest("#articleBody, #summaryText, #chatMessages")) {
      hideSelectionFlomoButton();
    }
  });
}

function bindLayoutResizeInteractions() {
  if (!nodes.layout || !nodes.layoutDivider) {
    return;
  }

  let dragging = false;
  let activePointerId = null;

  const finishDrag = () => {
    if (!dragging) {
      return;
    }
    dragging = false;
    activePointerId = null;
    document.body.classList.remove("layout-resizing");
    nodes.appShell.classList.remove("resizing");
    applySidebarWidth(getCurrentSidebarWidth(), true);
  };

  nodes.layoutDivider.addEventListener("pointerdown", (event) => {
    if (state.sidebarCollapsed || window.innerWidth <= 960) {
      return;
    }
    dragging = true;
    activePointerId = event.pointerId;
    nodes.layoutDivider.setPointerCapture(event.pointerId);
    document.body.classList.add("layout-resizing");
    nodes.appShell.classList.add("resizing");
    event.preventDefault();
  });

  nodes.layoutDivider.addEventListener("pointermove", (event) => {
    if (!dragging) {
      return;
    }
    const layoutRect = nodes.layout.getBoundingClientRect();
    const nextWidth = event.clientX - layoutRect.left;
    applySidebarWidth(nextWidth);
  });

  const releasePointer = () => {
    if (activePointerId !== null && nodes.layoutDivider.hasPointerCapture(activePointerId)) {
      nodes.layoutDivider.releasePointerCapture(activePointerId);
    }
    finishDrag();
  };

  nodes.layoutDivider.addEventListener("pointerup", releasePointer);
  nodes.layoutDivider.addEventListener("pointercancel", releasePointer);

  window.addEventListener("resize", () => {
    applySidebarWidth(getCurrentSidebarWidth(), true);
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

function clampSidebarWidth(width) {
  const ceiling = Math.max(MIN_SIDEBAR_WIDTH, Math.floor(window.innerWidth * MAX_SIDEBAR_RATIO));
  return Math.max(MIN_SIDEBAR_WIDTH, Math.min(width, ceiling));
}

function getCurrentSidebarWidth() {
  const raw = getComputedStyle(nodes.appShell).getPropertyValue("--sidebar-width").trim();
  const numeric = Number.parseFloat(raw);
  return Number.isFinite(numeric) ? numeric : getDefaultSidebarWidth();
}

function getDefaultSidebarWidth() {
  return clampSidebarWidth(window.innerWidth * DEFAULT_SIDEBAR_RATIO);
}

function applySidebarWidth(width, persist = false) {
  if (!nodes.appShell) {
    return;
  }
  const nextWidth = clampSidebarWidth(Number(width) || getDefaultSidebarWidth());
  nodes.appShell.style.setProperty("--sidebar-width", `${nextWidth}px`);
  if (persist) {
    window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(nextWidth));
  }
}

function loadSidebarWidthPreference() {
  const stored = Number.parseFloat(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY) || "");
  applySidebarWidth(Number.isFinite(stored) ? stored : getDefaultSidebarWidth());
}

function ensureChatSidebarWidth() {
  if (window.innerWidth <= 960) {
    return;
  }
  const current = getCurrentSidebarWidth();
  const recommended = getDefaultSidebarWidth();
  if (current < recommended) {
    applySidebarWidth(recommended, true);
  }
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
  renderLibraryBrowser();
});

nodes.ingestUrlButton.addEventListener("click", startUrlIngest);
nodes.ingestUrlInput.addEventListener("input", (event) => {
  const value = event.target.value.trim();
  if (ingestSuggestionTimer) {
    window.clearTimeout(ingestSuggestionTimer);
  }
  if (!value || isLikelyUrl(value) || value.length < 4) {
    ingestSuggestionRequestId += 1;
    clearIngestSuggestions();
    return;
  }
  ingestSuggestionTimer = window.setTimeout(() => {
    void fetchArxivSuggestions(value);
  }, 220);
});
nodes.ingestUrlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    startUrlIngest();
  }
});

nodes.sidebarToggle.addEventListener("click", toggleSidebar);
document.addEventListener("click", (event) => {
  const button = event.target.closest(".workspace-button");
  if (!button) {
    return;
  }
  setWorkspace(button.dataset.workspace);
});

nodes.viewToggle.addEventListener("click", (event) => {
  const button = event.target.closest(".view-toggle-button");
  if (!button) {
    return;
  }
  setViewMode(button.dataset.mode);
});

nodes.chatArticleSelect.addEventListener("change", (event) => {
  const nextArticleId = event.target.value;
  state.chatArticleId = nextArticleId;
  resetChatSession();
  void loadArticle(nextArticleId, "chat");
  renderChatSidebar();
  renderChatView();
});

nodes.chatModelSelect.addEventListener("change", (event) => {
  state.chatModelKey = event.target.value;
  resetChatSession();
  void loadPersistedChatSession();
  renderChatSidebar();
  renderChatView();
});

nodes.chatResetButton.addEventListener("click", () => {
  startFreshChatSession();
  renderChatView();
});

nodes.chatComposer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.chatPending) {
    return;
  }
  await sendChatMessage();
});

nodes.chatInput.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter") {
    return;
  }
  if (!event.metaKey) {
    return;
  }
  event.preventDefault();
  if (state.chatPending) {
    return;
  }
  await sendChatMessage();
});

bindUploadInteractions();
bindLightboxInteractions();
bindIngestModalInteractions();
bindFlomoInteractions();
bindLayoutResizeInteractions();
applyViewMode();

bootstrap().catch((error) => {
  nodes.articleList.innerHTML = `<div class="article-card muted">加载失败：${escapeHtml(error.message || "unknown error")}</div>`;
});
