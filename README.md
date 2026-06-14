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
| `ANTHROPIC_API_KEY` | Model passes: semantic scan, auto-investigate, universe expansion, outreach contact research + drafts. Every module no-ops without it |
| `HUNTER_API_KEY` | *Optional*. SEND OUTREACH email find+verify (free tier: 25/50 a month). Without it: published addresses only |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | *Optional*. Adds Indeed + 10 more job boards via Adzuna API (free) |
| `CRUNCHBASE_API_KEY` | *Optional*. Proactive UK funding-round detection via Crunchbase API (free tier) |

For the SEND OUTREACH button itself, the **Render** service additionally
needs `GMAIL_USER`, `GMAIL_APP_PASSWORD` and (to go live) `OUTREACH_TEST_MODE=0`
in its Environment — see `render.yaml`.

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

## BD Build v8 — the Advisory Engine (Phase 1): advisory as a first-class lead

The talent-consultancy pivot. Until now advisory was an *enrichment lens*
on a hiring lead (`tool/advisory.py: service_fit_for` — "adds no new signal
and changes no detection"). That is backwards for origination: the
strongest advisory opportunities — a comms/marketing function that is
stuck, over-stretched or misfiring — frequently have **no vacancy at
all**. v8 makes advisory demand a lead the engine **originates in its own
right**, on a parallel lane through the same plumbing. Full plan:
[`ADVISORY_ENGINE.md`](ADVISORY_ENGINE.md).

- **`tool/advisory_signals/`** — the detector family. Each emits a typed
  `AdvisorySignal` that fires independently of job-board / ATS activity:
  - **`PayGapActionMandate`** (`pay_gap.py`) — reuses the GOV.UK
    gender-pay-gap dataset, zero new fetches. The discipline that keeps it
    out of the generic-noise trap: a standing gap is **not** a lead; the
    COMPELLING EVENT is the statutory reporting / equality-action-plan
    window being open (the dated "why now" the calendar pulses track).
  - **`from_predictors.py`** — origination reused from the predictor
    pipeline (§3 B/D/E): M&A/PE → **PostMergerIntegration**,
    restructure/redundancy → **RestructureRedundancy**, ESG/B-Corp →
    **ESGCapabilityBuild**. Each is a dated compelling event the engine
    already detects, routed to advisory (function design, change-comms,
    capability build) rather than just "a seat" — no new fetches. Only the
    curated, genuinely-advisory triggers map across; a hiring-only signal
    (a job-ad cluster) never becomes an advisory lead.
- **`tool/advisory_gate.py`** — a consulting-adapted **MEDDPICC** gate
  (PAIN / SPONSOR / MANDATE / TIMING / ACCESS / PROOF, each 0–2), distinct
  from the hiring gate's SEAT/BUDGET/URGENCY/BUYER. The dimensions are
  inputs to a reasoned **KILL / DEVELOP / PURSUE** verdict (deterministic
  in Phase 1; the Opus Conviction Verdict replaces it in Phase 2). Carries
  the three failure-mode defences: a hard **PURSUE cap** (scarcity forces
  ranking), **source-independence as a gate** (a registry-blind advisory
  signal needs ≥2 independent sources to pursue), and **amplifier/bronze
  tiering** (low-precision detectors never pursue alone). A raw, verified
  pay-gap signal with no reachable buyer correctly stays **DEVELOP** until
  the contact layer names and routes the CHRO/CEO sponsor.
- **`tool/advisory_diagnostic.py`** — the **Outside-In Function
  Diagnostic**, VMA's proprietary instrument (the analogue of Korn Ferry's
  Hay assessment / Heidrick's culture profile): a defensible, outside-in
  *hypothesis* about the shape and likely gaps of the target's comms
  function, anchored to the resourcing benchmark and a peer cohort the
  buyer can't self-serve. Two §11-#2 guardrails are wired in: **variable
  structure** (it leads with the single sharpest anomaly for THIS company —
  pay-gap exposure / governance / under-resourcing — not a fixed script)
  and a **novelty gate** (the insight must rest on the non-public
  comparison, not the company's own published figures). Deterministic v0;
  the Opus pass swaps in the prose in Phase 2. The corpus it draws on (the
  peer + benchmark comparison) is the moat — output quality is bounded by
  its depth.
- **`tool/evidence_pack.py`** — the meeting-winning deliverable (the
  advisory analogue of the Pitch Pack): the seven Challenger parts
  (Reframe → Outside-In Diagnostic hypothesis → Benchmarking Teaser →
  Named Buyer + Inferred Pain → Value Give-Away → Recommended Service +
  Network Rail proof anchor → Take-Control Ask). v0 is deterministic and
  **facts-only — no fee figures** until the project-scoping bands are
  signed off (Opus prose + the novelty gate come in Phase 2).
- **`tool/advisory_routing.py`** — associate routing: the verdict's
  recommended service → the relationship owner and delivery bench from the
  brochure. Lucy Cairncross (MD, Advisory) owns the advisory relationship;
  Sara owns the BD/search motion and the referral lanes; the associate is
  attached by service — coaching → Joss Mathieson (Change Oasis) / Famn,
  ED&I → Antoinette Willcocks (RiverRoad) / Kate Isichei (neuroinclusion).
  Threaded through every gate row and rendered Evidence Pack.
- **The Opus layer** — the reasoned passes that replace the deterministic
  verdict + v0 diagnostic (§5), without touching the £0 nightly pipeline:
  - **`tool/advisory_overlay.py`** — the store an Opus run writes to (one
    JSON per lead, 21-day TTL). The gate reads it and the reasoned
    **KILL/DEVELOP/PURSUE** verdict *overrides* the deterministic one
    (mirroring how `investigations` overlays outrank the hiring gate); the
    Evidence Pack uses the Opus Reframe + Diagnostic prose. The
    deterministic call always still runs as the fallback and the chips.
  - **`/advisory-brief`'s Opus deep pass** — the primary, **zero-spend**
    path: Claude Code itself (free under the subscription) runs the
    Conviction Verdict + Outside-In Diagnostic (Worker → Red-Team adviser →
    Verifier, the §9 guardrails enforced) and persists the overlay.
  - **`tool/advisory_llm.py`** — an OPTIONAL API path for unattended
    automation, **off by default** (needs `ADVISORY_LLM_ENABLED=1` + a key
    + the spend policy). Same `claude-opus-4-8` shape as `semantic_scan`;
    a strict no-op otherwise, so £0 holds and the deterministic gate is
    always the fallback.
- **`tool/advisory_outcomes.py`** — the feedback loop that makes the engine
  selective by **measurement**, not just design (§11 #1). Every advisory
  PURSUE the owner approves or spikes is logged; the trailing approval rate
  **auto-throttles the PURSUE cap** (mirrors the hiring board's acceptance
  throttle), so a board the humans stop trusting shrinks itself.
  `meeting_booked` is the sparse true outcome for longer-run `/learn`
  recalibration. Human-in-the-loop is thus also the training signal.
- **`/advisory` — the live console** (`tool/advisory_board.py:render_board_html`
  + an additive Flask route). The visible advisory lane in the render site,
  reachable from the main nav: Call-ready → Developing → Killed, each card
  with its conviction, owner + delivery associate, the one-line why, the
  gate chips, and an inline Evidence Pack for the call-ready leads. A
  self-contained page — it does not touch the hiring console — and degrades
  to an explanatory empty state until a detector fires.
- **`/advisory-brief [company]`** — the Claude Code driver: run the lane
  (detect → gate → rank → throttled cap), route to the owner, compose the
  Evidence Pack for the call-ready leads, and record the human decision.
  Human-in-the-loop on every PURSUE.

Locked Phase-1 decisions (ADVISORY_ENGINE.md §14): human-in-the-loop on
every PURSUE/send; comms/corporate-affairs desk first; `PayGapActionMandate`
ships first (pure reuse); deal value is facts-only until bands are agreed.
Same £0 running cost. Tests: `tests/test_advisory_engine.py`.

## BD Build v7 — roster-free contacts + the AD-grade account thesis

Two ceilings raised at once:

**Universal contact resolution (no manual roster).** The contacts store
is now a research CACHE, not a hand-seeded list — any company the board
or the job feed surfaces gets resolved automatically, in layers:

- the FREE chain first (Companies House officers → RNS appointments →
  leadership pages → LinkedIn via Bright Data free tier), then
- **model + live-web-search research** for whatever the free chain
  couldn't name — the same engine for live jobs
  (`OUTREACH_RESEARCH_MAX_JOBS`, default 40/run, misses retried in 3
  days) and now for BD-board companies too
  (`tool/contacts/job_researcher.research_company_owner`, budgeted by
  `BD_POC_RESEARCH_MAX`), with trigger evidence as search anchors;
- the POC card falls back to NAMED function-family people observed on
  the company's own leadership page (`tool/team_map`) — never generic
  role rows (AD decision stands);
- the email layer is verification-first: a free format-inferred guess
  is VERIFIED (½ a Hunter credit) before a finder search (1 credit) is
  ever spent — the same monthly budget closes roughly twice the leads;
- nothing fails silently any more: every un-named live job carries a
  research-diagnosis chip, and a dashboard banner shows missing keys
  and exhausted budgets (`tool/contacts/measure.contact_capabilities`).

Paid requirements, stated plainly: **Anthropic API** (powers all
research) and **Hunter** for email verification only — the one
capability that can't be self-built safely (SMTP verification needs
clean dedicated IPs; DIY attempts get the sending domain blacklisted).
Free tier works for a trial; Starter (~$49/mo) for scale. Nothing else.

**Advisory Gap Research (`tool/advisory_research.py`).** For the
Ready/Developing leads, a nightly model pass with live web search reads
EVERYTHING the engine has accumulated (dossier timeline, living team
map, agency ledger, peer activity, investigation verdict, the static
service mix as a hypothesis) and works the account like an AD the night
before a first meeting: what the comms/marketing function actually
looks like today, the GENUINE evidence-cited gaps VMA can plug across
the full service catalogue (services schema-locked to
`tool/advisory.SERVICES` — the model grounds the mix, it can't invent
product lines), the hiring needs, and the specific meeting hook.
Theses are 21-day overlays (re-run early when the lead's event set
changes), render as the **Account thesis + Meeting hook** on the
engine-page portfolio and the BD radar (outranking the static
service-fit block), and as the dossier's lead section. Budget:
`ADVISORY_RESEARCH_MAX` (default 8/run).

## BD Build v6 — the Talent-Consultancy lens (service fit)

VMA is moving from recruitment-only toward a talent consultancy: the
Advisory Services brochure sells **Strategy & Organisation Design**
(consultation & stakeholder analysis → benchmarking & design →
implementation — the Network Rail engagement), **Benchmarking** of
structure / headcount / salary against comparable organisations (the
L'Oréal "what do 10 peer comms teams look like?" report),
**Professional Development & Coaching** (Change Oasis, Famn) and
**ED&I Consulting** (RiverRoad, neuroinclusion). On top sit two referral
lanes: a **partner delivery agency** (e.g. Sequel Group) when there's
work but no headcount budget, and an **employee-engagement platform**
introduction (e.g. Workvivo by Zoom, Staffbase) when channels are the
gap.

v6 reads every signal the engine already trusts through that catalogue
(`tool/advisory.py: service_fit_for`) — no new fetches, no detection
changes:

- **Per-trigger service mix** — every trigger key (all predictor
  triggers, the programmatic predictors, the standalone detectors and
  every calendar pulse — coverage is test-enforced) maps to a ranked
  service mix with a signal-specific reason. A funding round isn't just
  "senior hire in ~6 months": it's *search + design-for-scale benchmark +
  function design + the first engagement-platform decision*. An IC
  platform RFP leads with the platform introduction; gender-pay-gap
  season leads with ED&I consulting and literal remuneration
  benchmarking.
- **Stacks combine** — every event in a stack votes, so CEO change +
  restructure surfaces org design and benchmarking above the bare hire.
- **Budget-strain steer** — money-is-tight triggers (profit warning,
  redundancy, restructure, contract loss, water SAR…) force a
  project-fee route (interim / agency referral) into the mix and stamp
  the card: perm headcount may be frozen, lead with fees that don't need
  a requisition.
- **Profile-aware** — the same lens re-tunes for the Marketing desk per
  request, like the rest of the advisory layer.
- **Surfaces** — "WHAT VMA CAN SELL" on the engine-page lead portfolio
  and the BD radar dossier; a service-fit block on the legacy predictor
  and funding cards; compact "Sell: …" lines on calendar pulses and the
  specialist panels; a "Service fit" section in every company dossier
  (voted across the company's full accumulated signal history); and a
  per-stack "Sell:" line in the emailed pre-advert section.

## BD Build v5 — SEND OUTREACH (the button on Live Jobs)

Every live job now carries the full chain from vacancy to a sent,
personalised first-touch email — with the AD previewing and owning every
send:

- **Per-job contact research** (`tool/contacts/job_researcher.py`) — the
  model + live web search answer "who, today, owns THIS hire at THIS
  employer": right legal entity (KPMG UK, not KPMG International), the
  seat's title family (not one exact title), dated evidence, an active
  departed-check, and a calibrated confidence. Answers land in the same
  contacts store as every other source (Sara's flags, freshness windows
  and the re-verify queue all apply), accepted only at >=0.7 confidence
  with evidence under a year old, capped below registry grade, 10 jobs/run.
- **The ad's own contact first** (`tool/contacts/ad_contact.py`) — NHS,
  charity, public-sector and many corporate ads print the hiring contact
  in the advert ("for an informal discussion contact Jane Smith …
  jane.smith@…"). That person was attached to THIS vacancy by the
  employer — extracted deterministically on every render, instantly
  sendable as `published` with the ad as the citation. Application
  inboxes (jobs@/recruitment@) and agency-posted ads are excluded.
- **Work-email layer** (`tool/contacts/email_resolver.py`) — published
  sources first (the RNS enquiries blocks the tool already archives are
  parsed for citable addresses, in-house domains outranking the issuer's
  PR agency), then Hunter (`HUNTER_API_KEY`) — find+verify for named
  people, and domain-search as last-resort named-contact fill (one
  credit buys up to 10 senior comms/marketing people with addresses,
  gated on the resolver's own title patterns). A persistent monthly
  ledger (`state/hunter_ledger.json`, `HUNTER_MONTHLY_*_BUDGET`) keeps
  spend inside the free tier. Statuses: `verified` / `published` may be
  one-click sent; `pattern` guesses are stored for the human but NEVER
  sendable — unverified guesses bounce 10–30% and poison the sending
  mailbox.
- **Personalised drafts** — the brief writes a per-lead draft from the
  job ad + contact facts only (no invented claims), falling back to the
  AD-approved fixed template wherever the budgeted pass didn't reach.
- **Preview-before-send modal** — contact, email + status chip,
  confidence, source link, editable subject/body, "Flag wrong contact"
  and a permanent "Don't contact" opt-out. SEND is enabled only when
  every gate passes; otherwise the modal says exactly why not.
- **The guarded send** (`/api/outreach/send`) — recipient re-derived
  server-side from the lead id; gates re-checked (sendable email status,
  0.70 confidence floor, suppression list, per-lead and per-address
  30-day duplicate guards); every message identifies VMA Group and
  carries a reply-to-opt-out footer (PECR corporate-subscriber basis);
  append-only log in `outreach_log.jsonl`; the lead flips to followed-up.
- **Test-first**: `OUTREACH_TEST_MODE` defaults ON — every send reroutes
  to the practice inbox with the would-be recipient stamped on it. Going
  live is `OUTREACH_TEST_MODE=0` + `GMAIL_USER`/`GMAIL_APP_PASSWORD` on
  Render (see render.yaml).

## What the tool deliberately does not do

- Touch Sara's LinkedIn / Sales Nav / Recruiter seat
- Read or write to JobAdder
- Send outreach without the AD: every send is previewed and clicked by a
  human, gated on verified contacts, and test-rerouted until the live
  switch is flipped (v5 changed this line — it previously read "send
  outreach on her behalf", and the no-silent-sending principle survives)
- Store personal profiles beyond a 14-day dedup cache (the curated
  hiring-contacts roster, now including published/verified work emails,
  is the deliberate exception it always was)
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
