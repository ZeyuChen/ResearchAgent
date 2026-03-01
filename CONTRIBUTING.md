# Contributing

Thanks for contributing to ResearchAgent.

This project is intentionally product-oriented: readability, auditability, and local workflow integrity matter more than superficial feature count.

## Ground Rules

- Keep secrets out of source control
- Preserve local-first behavior
- Prefer changes that improve research workflow clarity over decorative UI
- Do not silently change cost/accounting semantics without documenting them

## Local Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Run the app:

```bash
venv/bin/python main.py serve
```

## Before Opening a PR

Run:

```bash
python3.11 -m py_compile research_agent/services/*.py research_agent/web/api.py
node --check research_agent/web/static/app.js
venv/bin/python -m pytest tests
```

## Change Expectations

### Backend

- Keep route handlers thin
- Put business logic in `research_agent/services/`
- Extend metadata schema conservatively

### Frontend

- Optimize for reading density
- Avoid adding UI chrome that reduces usable content area
- Preserve keyboard-first interaction where possible

### Documentation

- Update the relevant README or docs page whenever behavior changes
- If a feature affects operator cost, mention it in docs

## Release Notes

For user-visible changes, include:

- what changed
- why it changed
- any migration or behavior implications

## Issue Quality Bar

Good issues include:

- exact article or source involved
- reproduction steps
- expected behavior
- actual behavior
- screenshot when the problem is visual
