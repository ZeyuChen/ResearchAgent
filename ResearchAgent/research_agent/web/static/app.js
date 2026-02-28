const state = {
  library: [],
  activeArticleId: null,
  selectedDate: "all",
  selectedTopic: "all",
  search: "",
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
};

async function bootstrap() {
  const response = await fetch("/api/library");
  const payload = await response.json();
  state.library = payload.articles || [];
  renderFilters(payload.dates || [], payload.topics || []);
  renderArticleList();
}

function renderFilters(dates, topics) {
  nodes.dateFilters.innerHTML = "";
  nodes.topicFilters.innerHTML = "";

  nodes.dateFilters.appendChild(createFilterChip("全部", state.selectedDate === "all", () => {
    state.selectedDate = "all";
    renderArticleList();
    renderFilters(dates, topics);
  }));

  dates.forEach((dateValue) => {
    nodes.dateFilters.appendChild(createFilterChip(dateValue, state.selectedDate === dateValue, () => {
      state.selectedDate = dateValue;
      renderArticleList();
      renderFilters(dates, topics);
    }));
  });

  nodes.topicFilters.appendChild(createFilterChip("全部", state.selectedTopic === "all", () => {
    state.selectedTopic = "all";
    renderArticleList();
    renderFilters(dates, topics);
  }));

  topics.forEach((topic) => {
    nodes.topicFilters.appendChild(createFilterChip(`${topic.name} (${topic.count})`, state.selectedTopic === topic.name, () => {
      state.selectedTopic = topic.name;
      renderArticleList();
      renderFilters(dates, topics);
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
    return;
  }
  const article = await response.json();

  nodes.emptyState.classList.add("hidden");
  nodes.articleView.classList.remove("hidden");
  nodes.heroTitle.textContent = article.title;
  nodes.summaryBlock.innerHTML = `
    <p>${escapeHtml(article.summary || "暂无摘要")}</p>
  `;
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

bootstrap().catch((error) => {
  nodes.articleList.innerHTML = `<div class="article-card muted">加载失败：${escapeHtml(error.message || "unknown error")}</div>`;
});
