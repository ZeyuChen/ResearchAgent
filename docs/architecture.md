# Architecture

This document describes the main technical design of ResearchAgent.

## System Overview

ResearchAgent is split into five layers:

1. Source ingestion
2. Storage and indexing
3. LLM processing
4. Web API
5. Browser UI

## 1. Source Ingestion

### Automatic ingestion

The pipeline can pull from:

- arXiv feeds
- GitHub release metadata
- supported web sources

Automatic ingestion applies a two-stage relevance filter:

- keyword match on title + abstract
- optional Gemini-based secondary relevance check

### Manual ingestion

Manual ingestion supports:

- arXiv URLs
- uploaded PDFs
- arbitrary URLs

For URL ingestion, Playwright is used to render dynamic content before extraction.

## 2. Storage Model

### Directory layout

Each imported item is written to:

```text
data/YYYY-MM-DD/<source>-<slug>/
```

### Files per item

- `metadata.json`: UI and indexing source of truth
- `article.md`: generated analysis
- `source.pdf`: optional original PDF
- `source.html`: optional captured HTML
- additional rendered assets for figures / screenshots

### Metadata fields

Key fields include:

- `article_id`
- `title`
- `summary`
- `topic_tags`
- `imported_at`
- `last_read_at`
- `llm_usage`

This allows the UI to sort by recency, show costs, and maintain traceability.

## 3. LLM Processing

The `LLMProcessor` layer is responsible for:

- PDF reading through Gemini File API
- HTML / webpage analysis
- arXiv abstract translation
- short summary generation
- topic tag generation
- usage and cost extraction

### Processing rules

- Prefer original PDF when present
- Fall back to rendered HTML when PDF is unavailable
- Keep prompts explicit and structured
- Keep post-processing minimal unless needed for stability

## 4. Chat System

The `ChatService` layer is article-scoped and session-aware.

### Session model

Each session is bound to:

- an `article_id`
- a `model_key`
- a persistent `session_id`

State is stored locally in:

```text
data/_system/chat_state.json
```

### Context strategy

The system prefers, in order:

1. Gemini cached PDF context
2. uploaded file context
3. cached Markdown context
4. inline context

### Two-stage request flow

Chat requests are split into:

1. `prepare`: create / restore session and build or reuse context
2. `generate`: send the actual user prompt

This allows the frontend to distinguish between "preparing context" and "generating answer".

## 5. Web API

FastAPI provides the local application interface.

Key endpoints include:

- `/api/library`
- `/api/articles/{article_id}`
- `/api/search/arxiv`
- `/api/chat/options`
- `/api/chat/sessions`
- `/api/chat/session`
- `/api/chat/prepare`
- `/api/chat/messages`
- `/api/integrations/flomo/*`

The API is intentionally thin: business logic stays in the service layer, not in route handlers.

## 6. Frontend Design

The UI is optimized for three jobs:

1. Browse the library
2. Read one item deeply
3. Ask follow-up questions while keeping the source visible

### Layout model

- Left pane: library or chat workspace
- Right pane: library grid or article reader
- Reader mode supports:
  - dual-pane
  - analysis only
  - source PDF only

### Reading UX priorities

- maximize content density
- keep comparison reading stable
- reduce decorative surface area
- preserve persistent chat and reading context

## 7. Notes on Cost Accounting

ResearchAgent estimates token cost from Gemini usage metadata returned by the API.

The estimate is sufficient for operator awareness, but should be treated as an application-side approximation rather than a billing source of truth.
