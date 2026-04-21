#!/usr/bin/env bash
# Fires the sample brief email to amirt12@hotmail.com in one command.
# No Python, no pip install, no repo-level setup. Just needs a Resend API key.
#
# Usage:
#     RESEND_API_KEY=re_xxxxxx ./fire_test.sh
#
# Getting a Resend key (2 minutes, no card, no domain setup needed):
#   1. https://resend.com/signup   (email + password)
#   2. Click the link Resend emails you to confirm
#   3. https://resend.com/api-keys → Create API Key → "full access" → copy
#   4. Paste into the command above
#
# The sender is onboarding@resend.dev (Resend's built-in test sender).
# Hotmail occasionally sends Resend test emails to spam — check Junk if nothing lands.

set -euo pipefail

if [ -z "${RESEND_API_KEY:-}" ]; then
    echo "RESEND_API_KEY is not set."
    echo "Get one at https://resend.com (free, 2 min), then run:"
    echo "  RESEND_API_KEY=re_xxxxxx ./fire_test.sh"
    exit 1
fi

TO="${1:-amirt12@hotmail.com}"
HTML_FILE="${2:-sample_brief_preview.html}"
SUBJECT="[TEST] Sara's Morning Brief — $(date '+%a %d %b')"

if [ ! -f "$HTML_FILE" ]; then
    echo "Can't find $HTML_FILE. Run from the repo root."
    exit 2
fi

# jq-free JSON-safe encode of the HTML body via python
HTML_JSON=$(python3 -c 'import json,sys; print(json.dumps(open(sys.argv[1]).read()))' "$HTML_FILE")

PAYLOAD=$(cat <<EOF
{
  "from": "onboarding@resend.dev",
  "to": ["$TO"],
  "subject": "$SUBJECT",
  "html": $HTML_JSON
}
EOF
)

echo "→ Sending to $TO via Resend…"
RESPONSE=$(curl -sS -w "\n__HTTP__%{http_code}" -X POST https://api.resend.com/emails \
    -H "Authorization: Bearer $RESEND_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

STATUS=$(echo "$RESPONSE" | tail -n1 | sed 's/__HTTP__//')
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$STATUS" = "200" ] || [ "$STATUS" = "202" ]; then
    echo "✓ Sent. Resend response: $BODY"
    echo "  (Check Junk if it doesn't appear in Inbox within a minute.)"
else
    echo "✗ Send failed. HTTP $STATUS"
    echo "$BODY"
    exit 3
fi
