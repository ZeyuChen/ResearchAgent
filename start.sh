#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "=== ResearchAgent Cloud Startup ==="

# Ensure data and logs directories exist
mkdir -p data logs

# Start the web server (binds to 0.0.0.0 for external access)
echo "Starting server on ${RESEARCH_AGENT_HOST:-0.0.0.0}:${RESEARCH_AGENT_PORT:-8000} ..."
exec python main.py serve
