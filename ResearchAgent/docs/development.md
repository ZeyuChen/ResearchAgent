# Development Guide

This guide is for contributors who want to run, inspect, and extend ResearchAgent locally.

## Environment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r ResearchAgent/requirements.txt
playwright install chromium
```

## Local Configuration

Use local environment variables or an untracked `.env` file.

Required:

- `GEMINI_API_KEY`

Optional but supported:

- `GITHUB_TOKEN`
- `FLOMO_WEBHOOK_URL`
- scheduler-related settings

Do not commit real secrets.

## Common Commands

### Start the UI

```bash
venv/bin/python ResearchAgent/main.py serve
```

### Run one ingestion pass

```bash
venv/bin/python ResearchAgent/main.py run --limit 5
```

### Run tests

```bash
venv/bin/python -m pytest ResearchAgent/tests
```

### Static checks

```bash
python3.11 -m py_compile ResearchAgent/research_agent/services/*.py ResearchAgent/research_agent/web/api.py
node --check ResearchAgent/research_agent/web/static/app.js
```

## Code Organization

### Backend services

- `research_agent/services/data_fetcher.py`
- `research_agent/services/manual_ingest.py`
- `research_agent/services/storage_manager.py`
- `research_agent/services/llm_processor.py`
- `research_agent/services/chat_service.py`

### Web layer

- `research_agent/web/api.py`
- `research_agent/web/static/index.html`
- `research_agent/web/static/app.js`
- `research_agent/web/static/styles.css`

## Implementation Rules

- Prefer minimal business logic in the frontend; keep source of truth in metadata / API
- Prefer `apply_patch` for file edits
- Avoid committing generated data or local secrets
- Preserve existing user data under `ResearchAgent/data/`

## Safe Release Checklist

Before cutting a tag:

1. Run unit tests
2. Run Python syntax checks
3. Run JS syntax checks
4. Verify the app loads at `http://127.0.0.1:8000`
5. Verify at least one chat request succeeds
6. Confirm `.env` and data archives remain untracked

## Suggested Next Steps for Contributors

- strengthen usage accounting for cached context
- add richer library sorting / filtering modes
- improve session summaries with lightweight LLM titling
- add export / import for library metadata backups
