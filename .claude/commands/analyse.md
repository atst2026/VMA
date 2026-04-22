---
description: Cross-reference pasted Recruiter output against the morning brief
argument-hint: "[paste Recruiter results: names, companies, titles — or CSV export]"
allowed-tools: Bash, Read, Write
---

Sara has pasted Recruiter output below. Cross-reference it against today's morning brief and rank "call these 5 first".

## Sara's paste

$ARGUMENTS

## Step 1 — Parse + cross-reference

Save the paste above to a temp file and pipe it into the analyser (it reads stdin):

```bash
# Save Sara's paste (the content between the Step-1 header and this code block)
# to a file, then:
cat /tmp/sara_paste.txt | python3 -m tool.analyse
```

If Sara's paste is inline and easier to pipe directly, use a heredoc:

```bash
python3 -m tool.analyse <<'RECRUITER_PASTE'
<verbatim paste content>
RECRUITER_PASTE
```

The analyser returns JSON with each parsed row enriched with:
- `fit` — 0–1, title match against Sara's role taxonomy
- `signal_hits` — every signal in today's brief that touches this person's company
- `signal_hit_count` — rollup for sorting

Rows are pre-sorted by `fit × (1 + 0.3 × hits)` descending, so the top rows are already the strongest leads.

## Step 2 — Synthesise

Produce output in this exact shape:

### Call these 5 first

For each of the top 5 (by the analyser's rank, with your judgement applied — skip any where `fit == 0` or the row is obviously noise):

```
1. [Name] @ [Company] — [Title]
   Why: [1 line. If there's a signal_hit, lead with "their company showed up in today's brief for <X>".
         Otherwise lead with fit — "direct target role at scale target company".]
```

### Also relevant

Next 5–10 rows as a bulleted list: `Name (Title @ Company) — one line why`.

### Nothing to pursue

Rows with `fit ≤ 0.2` or no signals. Bullet list with `Name (Title @ Company) — reason`. Typical reasons: "agency-side role", "junior", "out of geography", "out of industry (duplicate of existing active client)". Don't pad — if there's nothing, say so.

## Step 3 — Save + print

Save the output to `tool/state/analyse_<YYYY-MM-DD_HHMM>.md`. Print full output to the conversation so Sara can copy it straight into her call-list.

No outreach is drafted. Sara dials.
