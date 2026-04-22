---
description: On-demand deep research on a company or person
argument-hint: "[company name or person name]"
allowed-tools: Bash, Read, Write, WebSearch, WebFetch
---

Sara wants a deep dive on: **$ARGUMENTS**

## Step 1 — Assemble the raw intelligence

Run this from the repo root (Sara's cwd when she starts Claude Code):

```bash
python3 -m tool.deep_dive "$ARGUMENTS"
```

That returns a JSON blob with:
- `target` — the name Sara gave
- `as_person` — whether we auto-detected a person vs a company (override if wrong)
- `sources.companies_house` — UK filings, officers, PSC history (companies only)
- `sources.sec_edgar` — any matching 8-Ks (US parent filings)
- `sources.rss` — today's regulator, trade press, and procurement hits mentioning the target
- `sources.gdelt` — global news coverage over last 12 months, date-sorted
- `sources.linkedin_urls` — People / Company / Jobs search URLs Sara can open manually in her Recruiter
- `counts` — summary of signal volume by source

## Step 2 — Enrich with ad-hoc research

Using the JSON as a spine, fill gaps with WebSearch + WebFetch:
- If Companies House turned up a current CEO/CFO/CHRO, who's the current Head of Corporate Affairs / Director of Communications? (WebSearch `"Head of Communications" "<company>"` and `"Corporate Affairs Director" "<company>"`)
- If the target is a person: where did they work before? Are they a past placement? (WebFetch any obvious LinkedIn URL — public profile only; if blocked, note and move on)
- If GDELT found 0 or 1 results, do a targeted WebSearch of `"<target>" news 2026` for coverage the graph missed

## Step 3 — Write the brief

Produce the brief in this exact shape. Be concrete. Cite sources inline. Don't pad.

**Snapshot** — one tight paragraph: who they are, sector, scale (revenue / headcount / listed-ness).

**Why they're on Sara's radar** — the specific trigger that likely put them in the brief or the stated reason Sara asked.

**Recent changes (last 12 months)** — leadership moves (with dates), structural changes, regulatory hits, major filings. Bullet list. Each bullet ends with `(source: Companies House | RNS | GDELT | FCA | …)` and a date.

**Current comms team** — named people where findable. Lists: Head/Director of Comms, Head of Corporate Affairs/PR, CCO, CMO if relevant. Note tenure where known. Note any vacancies implied by recent departures.

**Signal stack** — every relevant hit found, grouped by source. If Companies House showed a new officer appointment two weeks ago, list it. If RSS surfaced a regulator letter, list it. Dates + one-line summary + URL each.

**Recommended angle** — one paragraph. How Sara should open the call: the hook, the reference point, the question that gets them talking.

**Open questions** — 3–5 things Sara should verify or dig into on the call that we couldn't nail down from public data.

## Step 4 — Save + print

Save to `tool/state/deep_dive_<slug>_<YYYY-MM-DD_HHMM>.md` (slugify the target). Also print the full brief to the conversation so Sara can copy it straight out.

Keep the whole brief to one page of prose — she'll read it in 3 minutes before dialling.
