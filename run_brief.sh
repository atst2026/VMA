#!/usr/bin/env bash
# Wrapper script used by cron and GitHub Actions.
# Sources env vars from .env if present, then runs the brief.
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a
    . ./.env
    set +a
fi

MODE="${1:-send}"   # default to live send when cron fires; pass "test" for the practice inbox
exec python3 tool/morning_brief.py "${MODE}"
