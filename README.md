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
| Companies House (UK director/PSC changes) | `/search` + `/officers` APIs |
| LSE RNS | Investegate RSS |
| UK regulators | FCA, Ofcom, Ofgem, Ofwat, ICO, CMA RSS |
| UK procurement | Contracts Finder, Find a Tender RSS |
| Civil Service Jobs | RSS |
| SEC EDGAR | Atom feed, 8-K filings |
| Trade press | PRWeek UK/US/Asia, Campaign, CorpComms, HR Magazine, People Management, Ragan, Provoke/Holmes |
| Public job boards | Greenhouse, Lever, Ashby, Workable (JSON); LinkedIn Jobs public (logged-off); Adzuna (optional) |
| Global news graph | GDELT DOC 2.0 |
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
