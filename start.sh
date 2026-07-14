#!/bin/sh

set -e

echo "🚀 Starting server..."
uv run uvicorn app.main:app --host 0.0.0.0 --port 8004 --loop uvloop --access-log
