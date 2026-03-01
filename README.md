# ResearchAgent

ResearchAgent is a local-first research assistant for RL, LLM infrastructure, and technical report deep reading.

It automates three things that usually stay fragmented:

- ingesting fresh papers, arXiv links, PDFs, and technical blogs
- turning raw sources into expert-grade Chinese analysis with Gemini
- organizing everything into a searchable local knowledge base with a dual-pane reader and chat workspace

The production code lives in [ResearchAgent/README.md](ResearchAgent/README.md).

## Why This Exists

Most paper workflows break down in the same place: you can collect PDFs, or you can chat with a model, or you can take notes, but the context is not persistent and the outputs rarely become a reusable personal library.

ResearchAgent is designed to close that gap:

- local storage by day, with source files and generated Markdown kept together
- Gemini-powered long-context reading, follow-up QA, and cost tracking
- a browser UI optimized for comparison reading, not just file upload
- Flomo export for excerpts and review snippets

## What You Get

- arXiv / GitHub / web article ingestion
- PDF-first parsing with Gemini File API
- LaTeX source figure extraction for arXiv papers when available
- dual-pane reading: analysis on the left, source PDF on the right
- per-article token and cost accounting
- per-session chat with context reuse and persistent history
- tag folders, recent-read ordering, and manual curation

## Quick Start

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r ResearchAgent/requirements.txt
playwright install chromium
venv/bin/python ResearchAgent/main.py serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Documentation

- Product and usage: [ResearchAgent/README.md](ResearchAgent/README.md)
- System architecture: [ResearchAgent/docs/architecture.md](ResearchAgent/docs/architecture.md)
- Development guide: [ResearchAgent/docs/development.md](ResearchAgent/docs/development.md)
- Competitive roadmap: [ResearchAgent/docs/scholaread-benchmark-roadmap.md](ResearchAgent/docs/scholaread-benchmark-roadmap.md)

## Repository Layout

```text
.
├── ResearchAgent/        # application code, docs, prompts, tests
└── venv/                 # local virtual environment (ignored)
```

## Release

This repository is prepared for the `v0.1` milestone: a usable end-to-end local research workflow with ingestion, reading, chat, export, and a persistent knowledge base.
