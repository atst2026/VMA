# Sara's Morning Brief

A daily BD intelligence email for Sara Tehrani, Account Director at VMA Group.
Scours every free public source + Bright Data licensed LinkedIn surface, filters
to Sara's role taxonomy and salary floor, ranks UK-primary, and delivers a
ranked top-5 call list to `stehrani@vmagroup.com` at 08:55 Europe/London,
Monday–Friday. Monday's brief covers Sat + Sun.

Zero touch for Sara. Email arrives, she reads it, she dials.

**Sample of the format**: [sample_brief_preview.md](sample_brief_preview.md)
(renders inline on GitHub) or [rendered HTML](https://htmlpreview.github.io/?https://github.com/atst2026/VMA/blob/main/sample_brief_preview.html).

## How it runs

GitHub Actions (`.github/workflows/morning-brief.yml`) fires Mon–Fri on a UTC
cron that covers both BST and GMT. A gate step checks Europe/London time is
between 08:30 and 10:30 before letting the rest of the job run — so if
GitHub's scheduler is delayed past 10:30 (which happens on the free tier),
the brief is skipped for that day rather than arriving at lunch dressed up as
a morning brief.

Cost: £0/month. 22 runs × ~2 minutes ≈ 45 min/month — well inside GitHub's
2,000-minute free tier.

## Secrets

Set in GitHub repo → Settings → Secrets and variables → Actions:

| Secret | Purpose |
|---|---|
| `GMAIL_USER` | Sender Gmail address (e.g. `franc.laude1994@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 16-char Gmail app password (not the login password) |
| `COMPANIES_HOUSE_KEY` | Companies House developer key (free) |
| `BRIGHT_DATA_KEY` | Bright Data key for licensed LinkedIn public surface (free 5k/month) |
| `GMAIL_FROM_NAME` | *Optional*. Default: `Sara's Morning Brief` |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | *Optional*. Adds Indeed + 10 more job boards via Adzuna API (free) |
| `CRUNCHBASE_API_KEY` | *Optional*. Proactive UK funding-round detection via Crunchbase API (free tier) |

Resend (previously used) is no longer wired in — Gmail SMTP handles delivery
to any inbox with no domain verification.

## Manual dispatch

Actions → "Sara's Morning Brief" → Run workflow → pick a mode:

| Mode | Behaviour |
|---|---|
| `test` | Real scouring → emails `amirt12@hotmail.com` (practice inbox) |
| `sample` | Synthetic signals, for verifying email delivery works without hitting sources |
| `send` | Real scouring → emails `stehrani@vmagroup.com` (live) |
| `preview` | Real scouring, no email (output uploaded as artefact only) |

Manual dispatch bypasses the 08:30–10:30 window check.

## Sources scoured (all free)

| Source | Via |
|---|---|
| Companies House (UK director changes) | `/search` + `/officers` APIs |
| Companies House (financing / ownership / rebrand / tenure) | `/charges`, `/persons-with-significant-control`, `/filing-history` + officer `appointed_on`; optional Streaming API (`CH_STREAM_ENABLED`) |
| LSE RNS | Investegate RSS |
| UK regulators | FCA, Ofcom, Ofgem, Ofwat, ICO, CMA RSS |
| UK procurement | Find a Tender + devolved: Public Contracts Scotland, Sell2Wales, eTendersNI RSS |
| Charity registers | Charity Commission (E&W) API trustee-board changes (free key); OSCR / CCNI wired |
| Civil Service Jobs | RSS |
| Profession / charity / sector job boards | CIPR, PRCA, CharityComms, CharityJob, NHS Jobs, jobs.ac.uk, Guardian Jobs RSS |
| SEC EDGAR | Atom feed, 8-K filings |
| Trade press | CorpComms, PRmoment, CIPR Influence, Ragan + sector titles (Inside Housing, Utility Week, pharmaphorum) |
| Public job boards | Greenhouse, Lever, Ashby, Workable (JSON); LinkedIn Jobs public (logged-off); Adzuna (optional) |
| Wayback Machine | careers/leadership-page diffing — pre-announcement leader departures |
| Global news graph | GDELT DOC 2.0 + Google News RSS (redundant predictive lane) |
| LinkedIn public surface | Bright Data free tier (5k requests/month) |

## Scope

- **Role titles**: Internal & Change Comms · External & Corporate Comms · PR & Media Relations · Communications · Head of Corporate Comms · PR Director · Marketing & Brand
- **Geography**: UK primary (×1.0); international secondary (×0.6)
- **Salary**: £40k+ perm, £350–800/day interim
- **Industries**: all

## Ranking rules (summary)

- Title must match role taxonomy on a word boundary (so `cco` matches `CCO`, not `aCCOunt`)
- Agency / sales / client-service titles hard-excluded (Account Director, Account Executive, Technical Account, etc.)
- Trade press kept only if the title contains a news verb (appoints, departs, restructures, etc.) — editorial/thought-leadership drops
- Dedup on `(normalised-title, company)` to catch LinkedIn returning the same listing across queries
- Score = base × kind-multiplier × geo-weight × freshness × (1 + 0.25 × role-strength)
- Top 5 by score form the ranked call list; the rest appear below as a full signal set

## BD Build v2 — the demand-first upgrade

v1 answered "who is hiring?". In a quiet market that is the wrong question —
fewer companies hire, and the ones that do in-house it. v2 targets
**willingness to pay a fee** and **creates demand where none is advertised**.

New counter-cyclical detectors (zero new fetches, stack into the existing
ranker):

| Trigger | What it catches | Why it converts |
|---|---|---|
| `inhouse_search_failing` | A senior role aged 45+ days, or withdrawn-and-reposted, with no recruiter attached (`tool/predictive/inhouse_failure.py` posting ledger) | The buyer already paid the cost of the DIY route — the highest-converting call in a down market |
| `hiring_restart` | First senior posting after 6+ months of company-level silence | The freeze just ended; competitors still treat the account as dormant |
| `mishire_reversal` | A leader removed from the team page within ~18 months of joining (tenure-checked Wayback diff) | A failed hire forces an urgent, usually confidential replacement — work that cannot be done in-house |

New demand-creation commands (run in Claude Code, no API cost):

- `/mpc "<candidate>" [company] [title]` — Most Placeable Candidate
  spec-marketing: builds on `tool/reverse_match.py`, verifies each target's
  why-now live, and writes an anonymised spec-memo pack that creates roles
  where none are advertised.
- `/cfo-memo "<company>" [role]` — the internal business case the CCO/CMO
  takes into their own budget meeting: cost-of-vacancy vs fee from the
  house calculators, in-house vs retained route comparison. Wins the
  sign-off fight that kills fees in a budget-cut market.

## BD Build v3 — the investigation engine (deep-research blueprint)

v2 found the demand; v3 governs what an Account Director actually sees.
An AD must never be shown a watching brief dressed up as a lead:

- **Tiered board with Lead Strength** — every active lead shows a 0–100
  strength score (fit × signal × corroboration × timing; contradictions
  subtract) and the board groups into four sections: **Call-ready**
  (cleared the gate, capped ~7), **Developing**, **Early signals**
  (collapsed by default) and **Blocked**. Nothing is hidden; the gate
  grades instead of gatekeeping.
- **Qualification gate** (`tool/gate.py`) — Call-ready is decided the
  way an AD qualifies, on four evidenced dimensions (each 0-2): a
  live-or-imminent senior **Seat**, **Budget**/fundability,
  **Urgency**, and a reachable **Buyer** with a personal reason to
  engage. Present needs seat>=1 and >=5/8. Source-counting is demoted
  to per-fact verification: one registry-attested fact (Companies
  House / RNS / regulator) is true on its own — quiet companies with no
  press are not less qualified; only a lone non-registry source queues
  for /investigate. Hard blockers, amplifier-only and bronze-alone
  signals never present; the card shows the scorecard chips, the
  verification tag and "Why not call-ready".
- **Lead cards** carry calibrated confidence (High/Moderate), the
  evidence-independence count, **"What kills this"** (the playbook's kill
  conditions plus the live weakest link) and a **suggested first move**.
- **Acceptance plumbing (dormant)** — `tool/verdict_log.py`, the
  `/api/lead/verdict` endpoint and the gate's auto-throttle remain wired
  but the card buttons were removed by AD preference; re-adding the
  buttons re-enables the acceptance metric unchanged.
- **Window re-tool** — the flat 21-day "too fresh" hold is now per-family:
  leadership changes present in the 4–12-week window (hold 28d), funding
  keeps 21d, and a fresh event no longer re-freezes a mature stack.
- **Compounding dossiers** (`tool/dossier.py`) — every company accumulates
  a living file under `tool/state/dossiers/`: full signal timeline with
  sources, gate history, AD verdicts, investigation notes. The next look
  starts from memory, never zero.
- **`/investigate <company>|next`** — the per-trigger playbook (leadership,
  funding, job-cluster, team-page/mishire) run in Claude Code: corroborate
  or kill a queued hypothesis; the verdict overlay
  (`tool/investigations.py`) outranks every other gate rule for 21 days.

## BD Build v4 — capability-gap closers

Four gaps between the pitch and the build, closed:

- **`cmo_change` trigger** — new CMO / marketing-leader appointments and
  departures (incoming CMOs rebuild their team in the first 90 days; a
  departure opens the seat itself). Bare "CMO" is guarded against the
  Chief Medical / Manufacturing Officer senses in the detector.
- **`market_entry` trigger** — UK / European launches, first local office
  or HQ. Entrants build in-country comms / marketing capability around
  launch; bronze-tier (corroboration-grade alone), per the research tiering.
- **Agency-relationship ledger** (`tool/agency_relationships.py`) — every
  detected agency account move (PRWeek / Campaign "Pitch Update" lane) is
  folded into a per-company history: agency, discipline, appointed/ended,
  date, source. "What was their last agency relationship?" is now
  accumulated public record, not job-ad-age inference (which
  `competitor_mandates.py` still provides as the stale-brief layer).
- **Living team maps** (`tool/team_map.py`) — every leadership-page fetch
  the Wayback diff already makes now also folds the parsed (name, role)
  roster into a per-company team map: the current team, since when each
  name has been listed, and every observed joiner/leaver. Both ledgers
  render into the company dossiers.

## What the tool deliberately does not do

- Touch Sara's LinkedIn / Sales Nav / Recruiter seat
- Read or write to JobAdder
- Send outreach on her behalf
- Store personal profiles beyond a 14-day dedup cache
- Automate CRM

## Files

```
.github/workflows/morning-brief.yml   GitHub Actions scheduler + gate + run
.claude/commands/morning-brief.md     Dev slash command (not used by Sara)
tool/
  config.py                           roles, salary, geo, API keys from env
  morning_brief.py                    orchestrator
  ranking.py                          filter + rank + exclude + dedup
  render.py                           HTML + plaintext email
  email_send.py                       Gmail SMTP (+ legacy Resend fallback)
  state_store.py                      14-day dedup cache
  sources/                            per-source fetchers
    companies_house.py
    rss_feeds.py                      RNS + regulators + trade press + procurement
    jobs.py                           Greenhouse/Lever/Ashby/Adzuna/LinkedIn public
    gdelt.py
    sec_edgar.py
    bright_data.py
```

## Local testing

```bash
pip3 install requests beautifulsoup4 lxml python-dateutil
cp .env.example .env          # fill in keys
./run_brief.sh preview        # dry-run, no email, prints brief to stdout
./run_brief.sh test           # real run, emails amirt12@hotmail.com
./run_brief.sh send           # real run, emails stehrani@vmagroup.com
```
