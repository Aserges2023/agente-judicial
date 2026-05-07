#!/bin/bash
set -euo pipefail

# Only run in the Claude Code on the web remote environment
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install Python dependencies used by scripts/pipeline_judicial.py
# cffi is required so the system cryptography package (used by pdfminer/pdfplumber) can load
pip install --quiet --disable-pip-version-check \
  cffi \
  pdfplumber \
  openai \
  ruff \
  pytest
