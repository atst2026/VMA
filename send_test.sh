#!/usr/bin/env bash
# One-shot: send the practice-run brief to amirt12@hotmail.com.
#
# Usage:
#   ./send_test.sh            # sends the sample brief (synthetic data) — no internet calls to sources needed
#   ./send_test.sh live       # runs the real scouring + sends
#
# Prereq (one-time, 2 minutes):
#   1. Sign up at https://resend.com (free, no card). Confirm email.
#   2. Dashboard → API Keys → Create. Copy the key.
#   3. `cp .env.example .env` and paste the key into RESEND_API_KEY=
#   4. `pip3 install requests beautifulsoup4 lxml python-dateutil`
#
# Then run this script.
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

if [ -z "${RESEND_API_KEY:-}" ]; then
    echo "RESEND_API_KEY is not set. Open .env and paste your Resend key."
    echo "Sign up at https://resend.com — 2 minutes, no card."
    exit 1
fi

MODE="${1:-sample}"

export PYTHONPATH="${PYTHONPATH:-.}"

case "$MODE" in
    sample)
        python3 tool/sample_brief.py test
        ;;
    live)
        python3 tool/morning_brief.py test
        ;;
    *)
        echo "Usage: $0 [sample|live]"
        exit 1
        ;;
esac
