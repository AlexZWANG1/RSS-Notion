#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[$(date)] Starting AI Daily Digest..."

# Phase 1: Folo + Notion pipeline (optional)
if command -v node &> /dev/null && [ -f daily-digest.mjs ]; then
    echo "[$(date)] Phase 1: Running Folo + Notion pipeline..."
    node daily-digest.mjs || echo "[$(date)] WARNING: Phase 1 failed, continuing..."
else
    echo "[$(date)] Phase 1: Skipped (node or daily-digest.mjs not found)"
fi

# Phase 2: Python pipeline
echo "[$(date)] Phase 2: Running Python pipeline..."
python main.py "$@"

echo "[$(date)] Done!"
