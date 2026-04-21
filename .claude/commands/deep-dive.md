---
description: On-demand deep research on a company or person
argument-hint: "[company name or person name]"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
---

Sara has asked for a deep dive on: **$ARGUMENTS**

## What to do

Build a single-page briefing on this target, pulling from:

1. **Companies House** (if UK company) — filings, officer changes, PSC history, last 24 months. Use:
   ```bash
   cd /home/user/VMA && python3 -c "from tool.sources.companies_house import search_company, company_events; import json, sys; print(json.dumps(company_events(sys.argv[1]), indent=2, default=str))" "$ARGUMENTS"
   ```
2. **LSE RNS via Investegate** — any regulatory announcements.
3. **UK regulators** (FCA, Ofwat, Ofgem, Ofcom, ICO, CMA) — enforcement history.
4. **SEC EDGAR** — if there's a US parent.
5. **Trade press** — GDELT, PRWeek, Campaign, CorpComms for last 12 months of coverage.
6. **Public LinkedIn surface** via Bright Data free tier.
7. **WebSearch / WebFetch** — for anything ad-hoc.

## Brief format

Produce a single-page HTML or markdown brief with these sections:

- **Snapshot** — one-paragraph who they are
- **Why they're on the radar** — the specific trigger(s) Sara flagged
- **Recent changes (last 12 months)** — leadership, structure, regulatory, filings
- **Current comms team** (if identifiable) — named people, tenure, recent hires
- **Signal stack** — every relevant signal found, with source + date
- **Recommended angle** — one line on how Sara should open the call
- **Open questions** — what to verify on the call

Keep it tight. Sara will read it in 3 minutes before she dials.

Save output to `tool/state/deep_dive_$(date +%Y%m%d_%H%M%S).html` and print the full brief to the conversation.
