---
description: Cross-reference pasted Recruiter output against the morning brief
argument-hint: "[paste Recruiter results: names, companies, titles — or CSV export]"
allowed-tools: Bash, Read, Write
---

Sara has pasted Recruiter output below. Cross-reference it against today's morning brief and every signal in `tool/state/`.

**Recruiter output:**
$ARGUMENTS

## What to do

1. Parse the pasted list into structured rows (name · current company · current title — best effort; if Recruiter CSV is pasted, use columns).
2. Load today's brief from `tool/state/latest_brief.html` (and the raw signal set in `tool/state/latest_signals.json`).
3. For each person in the pasted list:
   - Match their current company against every flagged company in today's brief — surface if matched
   - Match against the last 30 days of signals (leadership departures, restructures, regulator actions) — surface if matched
   - Score them on `fit × fee-value × signal-stack`:
     - **fit**: title match against `tool/config.ROLE_KEYWORDS`
     - **fee-value**: heuristic from title seniority (Head/Director/Chief → higher) and geography (UK primary)
     - **signal-stack**: how many independent signals point at them or their company
4. Rank and produce output:

```
Call these 5 first:
1. [Name] @ [Company] — [why — 1 line]
2. ...

Additional relevant matches: [list]

Nothing to pursue: [list — with one-line reason each]
```

5. Save the output to `tool/state/analyse_$(date +%Y%m%d_%H%M%S).md` and print to the conversation.

No outreach is drafted. Sara dials.
