---
description: Sara's daily ranked call list from every scoured public source
argument-hint: "[optional: 'preview' to print to console, 'send' to email, default: preview]"
allowed-tools: Bash, Read, Write
---

Run Sara's morning brief end-to-end.

## What this command does

1. Scours every free public source defined in `tool/config.py` (Companies House, RNS, UK + EU regulators, SEC EDGAR, trade press, public job boards, procurement portals, Charity Commission, GDELT, Bluesky, Substack, speaker bureaus).
2. Enriches via Bright Data free tier (logged-off licensed LinkedIn surface) — never touches Sara's account.
3. Filters by role titles + salary floor (£40k+ perm / £350–800/day interim).
4. Ranks by fee-value × signal strength. UK-primary boost.
5. Produces a ranked top-5 call list with opening angle and one-line reason; full signal detail below the fold.
6. If invoked with `send` (or scheduled), delivers via Resend to `stehrani@vmagroup.com` (or `amirt12@hotmail.com` for practice runs).

## Mode

Mode requested: $1

If `$1` == "send" or "test": run scouring, build the brief, email it (test mode → amirt12@hotmail.com, live → stehrani@vmagroup.com).
Else: run scouring, build the brief, print HTML to console + save to `tool/state/latest_brief.html`.

## Execution

Run:
```bash
cd /home/user/VMA && python3 tool/morning_brief.py "${1:-preview}"
```

Then read `tool/state/latest_brief.html` and summarise the top 5 calls in one short paragraph for Sara — name, company, why now, recommended angle. Flag anything unusual or high-urgency.
