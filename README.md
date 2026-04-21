# Sara's Commission Engine

A daily intelligence tool for Sara Tehrani, Account Director at VMA Group.
Surfaces commission opportunities she'd otherwise miss. She does all the human
work (calls, meetings, pitches, closing). Claude finds and prepares.

## The tool

Three commands on Claude Max, zero external cost beyond the Claude Max seat:

- **`/morning-brief`** — runs every weekday at 08:55 Europe/London (Mon includes
  Sat + Sun). Scours every free public source + Bright Data free tier, filters
  on Sara's role taxonomy + £40k+ / £350–800/day floor, ranks UK-primary, emails
  `stehrani@vmagroup.com`.
- **`/deep-dive [company or person]`** — on-demand deep research on a lead.
- **`/analyse [paste]`** — cross-references pasted Recruiter output against the
  morning brief and ranks "call these 5 first".

**See the sample brief format**: [sample_brief_preview.md](sample_brief_preview.md)
(renders inline on GitHub) or [rendered HTML preview](https://htmlpreview.github.io/?https://github.com/atst2026/VMA/blob/claude/review-and-plan-tool-jdyFu/sample_brief_preview.html).

Sara's LinkedIn account is never touched. Bright Data's licensed logged-off
dataset sits separately; Sara uses her Recruiter seat manually for the 1–2
leads/day worth a deep dive.

## Fire the practice-run email (pick one — all take ~2 min)

**Option 1 — one shell command** (fastest):
```bash
# After cloning this branch on any machine with internet:
RESEND_API_KEY=re_xxxxxx ./fire_test.sh
```
That sends `sample_brief_preview.html` to `amirt12@hotmail.com` via Resend.
Get a free Resend key at https://resend.com (no card, no domain setup — uses
`onboarding@resend.dev` as sender).

**Option 2 — GitHub Actions (no laptop needed)**:
1. Push this branch to GitHub (already done) → Settings → Secrets & variables → Actions
2. Add secret `RESEND_API_KEY` (value: your Resend key)
3. Actions tab → "Sara's Morning Brief" workflow → Run workflow → mode = `test`

**Option 3 — full install** (needed for scheduled live runs):
```bash
pip3 install requests beautifulsoup4 lxml python-dateutil
cp .env.example .env           # paste RESEND_API_KEY into .env
./run_brief.sh preview         # dry-run, no email
./run_brief.sh test            # real scouring + email to amirt12@hotmail.com
./run_brief.sh send            # real scouring + email to stehrani@vmagroup.com
```

## Scheduling (Mon–Fri 08:55 London)

Two options; pick whichever suits Sara's setup.

### Option A — local cron (simplest)

```bash
crontab -e
# paste (edit the path):
55 8 * * 1-5  cd /ABSOLUTE/PATH/TO/VMA && ./run_brief.sh >> /tmp/vma-brief.log 2>&1
```

Laptop must be awake at 08:55, or use a small always-on host (Raspberry Pi,
small VPS). See `crontab.example`.

### Option B — GitHub Actions (no hardware needed)

1. Push this repo to GitHub (private).
2. Add the keys under Settings → Secrets and variables → Actions:
   - `COMPANIES_HOUSE_KEY`
   - `BRIGHT_DATA_KEY`
   - `RESEND_API_KEY`
   - optional: `RESEND_FROM`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`
3. The workflow in `.github/workflows/morning-brief.yml` fires at 08:55 London
   Mon–Fri automatically.

GitHub's free tier covers 2,000 Actions minutes/month — this job uses ~2
min/day × 22 days ≈ 44 min/month. Free.

## Slash commands (Claude Code)

Files in `.claude/commands/`:

- `morning-brief.md` — invoke to run the brief interactively and (optionally) email.
- `deep-dive.md` — on-demand deep research on a named company or person.
- `analyse.md` — paste Recruiter output → ranked "call these 5" synthesis.

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
- **Geography**: UK primary; international secondary (weighted × 0.6)
- **Salary**: £40k+ perm, £350–800/day interim
- **Industries**: all

## What the tool deliberately does not do

- Touch Sara's LinkedIn / Sales Nav / Recruiter
- Read or write to JobAdder
- Send outreach on her behalf
- Store personal profiles beyond dedup state
- Automate CRM

## Files

```
.claude/commands/          slash commands (morning-brief, deep-dive, analyse)
.github/workflows/         GitHub Actions scheduler
tool/
  config.py                roles, salary, geo, API keys from env
  morning_brief.py         orchestrator
  ranking.py               filter + rank signals
  render.py                HTML + plaintext email
  email_send.py            Resend integration
  state_store.py           dedup state (14-day TTL)
  sources/
    _http.py               shared HTTP + RSS helpers
    companies_house.py
    rss_feeds.py           RNS + regulators + trade press + procurement
    jobs.py                Adzuna + Greenhouse + Lever + Ashby + LinkedIn public
    gdelt.py               global news graph
    sec_edgar.py           8-K filings
    bright_data.py         licensed LinkedIn via Bright Data free tier
crontab.example            local scheduler
run_brief.sh               wrapper (cron + Actions use this)
.env.example               keys template
```
