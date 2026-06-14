# The Advisory Engine — Build Plan for VMA's Talent-Consultancy Pivot

> **One line:** Today the platform originates *hiring* leads and bolts an advisory
> reading onto them. This plan makes **advisory demand a first-class lead the engine
> originates in its own right** — detected, qualified, and reasoned-about
> independently of whether anyone is hiring — and ships each one as a
> meeting-winning **Evidence Pack** routed to the right VMA owner. Same £0 running
> cost. Same Opus + Claude Code spine. A different, larger business.

This document is the canonical brief for that build. It is written to be executed by
Claude Code (Opus) sitting next to an advisory-services lead (Lucy / Sara). It reacts
to the deep-research report *"Building the Advisory Engine"*, reconciles it against
what is actually in this repository today, and turns it into a file-level plan.

---

## 0. Why this, why now

VMA's traditional motion — contingent and retained *search* — is event-triggered:
a vacancy exists, the buyer is known (the hiring manager), the cycle is short, and the
proof is "we can find the person." That motion is structurally shrinking. AI is
compressing the volume and the margin of agency-style placement work, and the buyers
who remain increasingly in-house their hiring. The growth is **upstream**: advisory —
organisation design, benchmarking, coaching, ED&I — sold *before* and *instead of* a
vacancy, on the strength of insight the client cannot get internally.

Selling advisory is a different sport, and the architecture has to reflect it:

| | **Search sale (what we do today)** | **Advisory sale (what we are building)** |
|---|---|---|
| Trigger | A vacancy exists | A **latent need** — often *no vacancy at all* |
| Buyer | Hiring manager / HR | The **function leader or their boss** (more senior) |
| Cycle | Short, single decision-maker | Longer, consensus-driven |
| Proof | "We can find the person" | **Insight** — you understand their function better than they expect |
| Entry | "You have a vacancy" | A **diagnostic reframe** — generic outreach is actively *penalised* by senior buyers |

The deep-research report's two anchoring market facts: **61% of B2B buyers prefer a
rep-free buying experience and 73% actively avoid suppliers who send irrelevant
outreach** (Gartner, 2024; the rep-free figure rising in the 2026 update). The
best-in-class advisory arms of search firms — Korn Ferry (Hay assessment), Heidrick
(culture diagnostics), Russell Reynolds (D&I diagnostics) — all originate work the
same way: **a repeatable, proprietary diagnostic instrument is the origination
engine.** VMA already owns the embryo of one (the resourcing benchmark and the Network
Rail methodology). This build productises it.

**The finish line is winning the meeting, not pitching a search.** Everything below
serves that.

---

## 1. Where we are today (current-state audit)

The platform is mature and most of what the report says to "reuse" already exists and
is good. The honest gaps are specific and small in number. This section is the audit
so the build grafts onto real seams instead of rebuilding.

### What already exists and we keep (the spine)
- **Free UK source layer** — Companies House, RNS/Investegate, the five-regulator RSS,
  GDELT/Google News, procurement, charity registers, job boards, Wayback diffing,
  Bright Data free tier. (`tool/sources/`, `README.md`.)
- **Predictor pipeline → lead engine → gate → board** — `predictor_pipeline.py` →
  `lead_engine.score_lead` → `gate.assess` → `strength_score` → `tier_for`. Tiered
  board (Call-ready / Developing / Early / Blocked), daily cap, auto-throttle.
- **The conviction-verdict architecture is already built — for hiring.** `/red-team`
  builds a six-question business case, a sceptical critic tries to kill it, a verifier
  checks every claim, and the typed verdict is written via
  `investigations.write_overlay(..., red_team=True, conviction=0-100, business_case,
  warm_opening, economic_buyer, champion_path, kill_reasons)`, which then rides onto the
  card and **outranks every additive gate rule for 21 days**
  (`tool/gate.py` step 1, `tool/investigations.py`). *This is the exact pattern the
  advisory engine clones.*
- **Compounding dossiers** — `tool/dossier.py`; every company accumulates a living file.
- **Contact resolution + guarded PECR outreach** — `tool/contacts/*`,
  `tool/email_send.py`, the test-mode-default send with suppression list and confidence
  floors. Advisory outreach reuses this unchanged.
- **Calendar pulses** — `tool/calendar_pulses.py` already carries `gender_pay_gap_2026`,
  `equality_pay_reporting_2026`, `uk_srs_2026`, `mog_post_sr_2026`, `nhs_planning_2026`,
  `agm_reporting_2026`. These are the report's `PayGapActionMandate`,
  `ESGCapabilityBuild` and `PublicSectorReorg` substrates — **already dated and already
  flowing**.
- **The service-fit lens** — `tool/advisory.py`: `SERVICES` (search / interim /
  org_design / benchmarking / coaching / edi / agency_referral / engagement_platform)
  and `service_fit_for(contexts)` maps every trigger key → a ranked service mix with a
  signal-specific reason, profile-aware, budget-strain-steered. **This is excellent and
  becomes the engine's service vocabulary** — but note what it says about itself: *"adds
  no new signal and changes no detection."*

### The gap, stated precisely
> Advisory today is an **enrichment lens on a hiring lead**: a vacancy surfaces, then
> `service_fit_for` maps it to a ranked advisory mix. **That is backwards for
> origination.** The strongest advisory opportunities frequently have **no vacancy at
> all** — a function that is *stuck, over-stretched, or misfiring* is itself the signal.

Concretely, four things do not exist yet:

1. **A separate advisory detector family.** All detection today is hiring-shaped
   (`tool/predictive/patterns.py`). There is no `advisory_signals/` module emitting
   advisory triggers that fire *independently* of job-board / ATS activity.
2. **A separate advisory qualification gate.** `tool/gate.py` qualifies on
   SEAT/BUDGET/URGENCY/BUYER — tuned for "is this a fillable role." Advisory needs a
   different gate: **PAIN / SPONSOR / MANDATE-or-BUDGET / TIMING / ACCESS / PROOF.**
3. **A productised Outside-In Function Diagnostic and an Evidence Pack.** The Pitch Pack
   (`tool/pitch_pack.py`) sells a retained search. There is no Evidence Pack engineered
   to win a *consultative* meeting, and no automated diagnostic instrument.
4. **Advisory economics.** `tool/conversion.py` is search-only (`FEE_RATE_RETAINED
   0.185` → `FEE_RATE_TOP 0.22` on salary bands). Advisory work is **project / day-rate
   priced**; there is no advisory pricing model, and no associate-routing layer.

Everything else is reuse. That is why this is a months-not-quarters build.

---

## 2. The core architectural shift: advisory as a first-class lead

Three clean separations (the report's spine, and the right call):

- **Separate detection.** Advisory triggers fire independently of vacancies.
  → new `tool/advisory_signals/` module family.
- **Separate qualification.** A consulting-adapted MEDDPICC gate, distinct from the
  hiring gate. → new `tool/advisory_gate.py`.
- **Separate deliverable.** The Pitch Pack sells a search; the **Evidence Pack** sells a
  meeting. → new `tool/evidence_pack.py`.

These run as a **parallel lane through the same plumbing**: same dossiers, same contact
resolver, same guarded outreach, same board UI (a new "Advisory" lane and tier),
same `/red-team`-style conviction overlay. One company can carry both a hiring lead and
an advisory lead; the unified console (Phase 3) shows the whole account.

```
                 ┌─────────────────────── shared free-source layer ───────────────────────┐
                 │ Companies House · RNS · regulators · GDELT · GPG dataset · trade-press   │
                 │ RSS (NEW) · procurement · Wayback · Bright Data · calendar pulses        │
                 └───────────────┬───────────────────────────────────┬─────────────────────┘
                                 │                                   │
                   HIRING LANE (today)                   ADVISORY LANE (this build)
                   patterns.py detectors                 advisory_signals/ detectors  (§3)
                          │                                       │
                   lead_engine.score_lead                 AdvisoryScore inputs          (§4)
                          │                                       │
                   gate.assess  (SEAT/BUDGET/             advisory_gate  (PAIN/SPONSOR/  (§4)
                    URGENCY/BUYER)                          MANDATE/TIMING/ACCESS/PROOF)
                          │                                       │
                   /red-team conviction                  Advisory Conviction Verdict     (§5)
                    (confirmed/recheck/killed)             (KILL / DEVELOP / PURSUE)
                          │                                       │
                   Pitch Pack                             Evidence Pack                  (§6)
                          │                                       │
                          └──────────────► UNIFIED CONSOLE ◄──────┘  + associate routing (§7)
                                          one account, one queue
```

---

## 3. The Advisory-Demand Signal Taxonomy

Implement as `tool/advisory_signals/` — a module family parallel to the existing
trigger library, each detector emitting a typed `AdvisorySignal` (trigger type,
evidence URL(s), recency, source-independence count, predicted service mix, confidence).
Reuse the existing recency-decay and source-independence machinery (`tool/gate.py`
`source_evidence`). **Most of these reuse signals the engine already ingests — the new
work is detection logic and routing, not new fetches.**

| # | Detector (`AdvisorySignal` subclass) | What it catches | Predicts (service) | UK free source | Build |
|---|---|---|---|---|---|
| **A** | `NewFunctionLeaderWindow` | New comms/corp-affairs/marketing **function leader**, months 0–6 (the 90–180-day org-design window) — *the highest-yield trigger, and invisible to RNS/Companies House (board-only)* | org design + benchmarking + coaching | **Trade-press RSS (NEW, §3.1)** + company newsrooms; board-level CCOs also RNS | **NEW source + detector** |
| **B** | `PostMergerIntegration` | M&A/PE creating duplicated comms/marketing functions; PMI failure data makes the pain quantifiable | function design + change-comms + coaching | RNS, CMA merger RSS *(ingested)*, GDELT, trade-press | Reuse `mna`/`pe_acquisition`, **route differently** |
| **C** | `PayGapActionMandate` | Widening GPG year-on-year + missing/weak action plans; GEAPs (voluntary 6 Apr 2026 → mandatory 250+ from Spring 2027, first plans Apr 2028); ethnicity/disability pay on the horizon | ED&I consulting + inclusive-language + remuneration benchmarking | **GOV.UK GPG dataset** *(ingested)* + `equality_pay_reporting_2026` pulse | Extend pulse → **company-specific, calendar-pulsed lead** |
| **D** | `RestructureRedundancy` | Restructures/redundancies → acute change-comms + coaching demand ("61% of companies have no formal change-management approach") | change-comms + leadership coaching | RNS *(ingested)*, regulators, GDELT, trade-press | Reuse `restructure`/`redundancy`, add advisory routing |
| **E** | `ESGCapabilityBuild` | CSRD/UK SRS obligations + B-Corp certification → sustainability-narrative capability demand | strategy & org design + reporting | B Lab UK directory *(ingested)*, `uk_srs_2026` pulse, RNS/annual reports | Reuse `esg_bcorp`, promote to first-class |
| **F** | `LeadershipChurnCluster` | Multiple senior departures in one function in a short window → instability → coaching/redesign | coaching + development + org design | Cluster across RNS directorate changes + trade-press moves + Wayback careers diffing *(all ingested)* | **NEW aggregation** over existing signals |
| **G** | `PublicSectorReorg` | English LGR (unitary transitions), NHS ICB consolidation, machinery-of-government — each new entity **builds a comms function from scratch** | org design + change-comms + benchmarking | MHCLG, House of Commons Library briefings, Find a Tender / PCS / Sell2Wales *(ingested)*, `mog_post_sr_2026`/`nhs_planning_2026` pulses | Promote pulses → origination leads |
| **H** | `EmployeeSentimentDeterioration` | Falling Glassdoor scores around communication/leadership → internal-comms capability gap | culture + internal-comms review + coaching | Glassdoor public pages (handle carefully), GDELT tone | **NEW**, ship **hypothesis-only**, never quoted at a named org in outreach |
| **I** | `ThoughtLeadershipVelocity` | A leader suddenly speaking/publishing → ambition + openness to external partners | development + benchmarking | CIPR/PRCA event pages, conference bios, trade-press | **NEW**, a *receptivity multiplier*, not a standalone lead |
| **J** | `SkillsGapDisclosure` | Annual-report/strategic disclosures admitting comms/marketing capability or transformation gaps | strategy & org design + development | Companies House filings, annual reports, RNS | **NEW**, Opus long-context scan |

### 3.1 The one genuinely new source — trade-press people-moves (build A first)
The single highest-value advisory trigger (a new in-house function leader) is invisible
to the board-only registries. It needs dedicated trade-press detection. Prioritised,
free:
1. **PRWeek UK RSS (FeedBurner)** — top priority; "Movers & Shakers"/"People Moves"
   across FTSE, public and voluntary sectors. Wire the City & corporate, public-sector,
   voluntary and general feeds.
2. **Campaign UK RSS** — best for CMO/brand/marketing-director appointments PRWeek
   under-covers.
3. **PRovoke Media "EMEA People News"** — large-cap/EMEA in-house CCO/comms-director
   appointments; no clean RSS, scrape the people-news tag.
4. **Company-newsroom polling** (Presspage / Prezly / Mynewsdesk / PR Newswire expose
   RSS) — a watchlist of priority orgs; primary-source reliability.
5. **PRmoment** — cheap supplementary net; lower precision (agency-skewed).
6. **Communicate Magazine "Updates"** — successor niche after CorpComms ceased (2025).
   *Do not code against CorpComms.*

**Implementation:** `feedparser` → keyword/NER filter → appointment verbs
(`appoints|names|hires|joins|promotes|appointed as`) × function titles
(`chief communications officer|CCO|communications director|director of communications|
corporate affairs|head of (internal )?comms|CMO|chief marketing officer|brand director|
marketing director`) → dedupe across feeds (the majors echo each other) → timestamp to
start the 90–180-day window clock. **Explicitly avoid:** LinkedIn job-change scraping
(no viable free API, ToS-violating); CIPR/PRCA newsroom feeds (about the bodies, not
moves); Gorkana/Cision (journalist moves). Human analyst confirmation of a move is fine;
automated LinkedIn scraping is not.

> **Precision discipline:** ship A behind a 4-week precision measurement. Threshold to
> expand to PRovoke scraping + newsroom polling: **>60% of flagged appointments are
> genuine in-scope function-head hires.** Trade-press RSS truncates — verify the role
> before triggering.

---

## 4. The Advisory Qualification Gate (consulting-adapted MEDDPICC)

New module `tool/advisory_gate.py`, analogous to but distinct from
`gate.qualification`. Six dimensions, each 0–2, computed deterministically from collated
company data. **Needs > 4/6 to qualify as a lead worth an Evidence Pack.**

| Dim | Question | MEDDPICC | Evidenced from |
|---|---|---|---|
| **PAIN** | A concrete, evidenced functional pain? (structure mismatch, pay-gap exposure, post-merger duplication, capability gap) | Implicate the Pain | detector type + dossier + GPG delta + headcount-vs-benchmark |
| **SPONSOR** | An identifiable function leader who would own/champion the work? | Economic Buyer + Champion | `team_map.py`, contacts store, trade-press move |
| **MANDATE-or-BUDGET** | A plausible mandate or budget route? *Advisory budgets are less visible than headcount — "mandate" (regulatory deadline, board pressure, new-leader remit, transformation programme) is the realistic proxy* | Economic Buyer + Metrics | calendar pulse, trigger type, propensity store |
| **TIMING** | Inside a live window? (action-plan deadline, new-leader 100 days, integration phase) | Decision Process / compelling event | window-clock maths (deterministic) |
| **ACCESS** | Can a named VMA person reach the buyer? (relationship, advisory-board member, warm intro, conference) | gates outreach quality | contacts store, `agency_relationships`, warm-route flag |
| **PROOF** | A *defensible* outside-in hypothesis + a relevant benchmark/case anchor (Network Rail) to teach with? | Challenger Commercial Insight readiness | resourcing benchmark + diagnostic output (§5) |

**Scoring inputs feed a reasoned verdict, not an additive score.** Keep an
`AdvisoryScore` *input bundle* — Function-Fit (is this a comms/marketing/corp-affairs
function VMA can credibly go pro on — **not** general management consulting),
Signal-Strength, Trigger-Service Match, Window-Slope (early in the 90–180-day clock =
hotter), Access/Proximity, Opportunity-Size — but these are **inputs to the conviction
verdict (§5)**, consistent with the platform's existing move away from additive scoring.

**Hard scope rail:** Function-Fit is a gate, not a weight. If the work is not
comms / corporate-affairs / marketing, the engine routes it to a referral lane and
**never** pushes VMA toward general management consulting.

---

## 5. The Opus reasoning stack — five advisory passes

Mirrors the existing five hiring passes, advisory-tuned. Deterministic pre-filters keep
model calls off unqualified leads (the £0-discipline); Opus is reserved for judgement.

1. **Advisory Semantic Scan** — *Sonnet, low/medium effort, high volume.* Classify
   whether a raw item is an advisory signal and which trigger class. (Mirrors the
   existing `semantic_scan.py`.)
2. **Outside-In Function Diagnostic** — *Opus, extended thinking, extra-high effort.*
   **This is the proprietary instrument — invest the most Opus effort here.** Given
   everything in the dossier (filings, GPG data, headcount, sector, leadership), produce
   a *reasoned hypothesis* about the shape and likely weaknesses of the target's
   comms/marketing function vs the resourcing benchmark and comparable orgs. This is the
   productised version of the Network Rail Consultation→Benchmarking→Design methodology —
   VMA's analogue to Korn Ferry's Hay assessment and Heidrick's culture profile. **Anchor
   every diagnostic to the resourcing benchmark and the Network Rail case so it is
   defensible.**
3. **Advisory Conviction Verdict** — *Opus, extended thinking.* **Replaces additive
   scoring.** Ingest the `AdvisoryScore` inputs + the gate + the diagnostic, return a
   reasoned **KILL / DEVELOP / PURSUE** with named pain, named buyer, recommended
   service, the single sharpest insight to lead with, and a calibrated confidence that
   *acknowledges uncertainty rather than confabulating*. Persisted exactly like the
   hiring verdict via an advisory overlay (clone `investigations.write_overlay`).
4. **Evidence Pack Composer** — *Opus, long-context.* Assemble the Evidence Pack (§6)
   with the credibility guardrails (§9) hard-wired into the system prompt.
5. **Associate-Match & Routing** — *Sonnet.* Map the verdict's recommended service to
   the right owner (§7).

**Division of labour (protect the cost philosophy):** keep zero-model deterministic work
for window-clock maths, GPG year-on-year delta, benchmark maths, calendar pulses, dedupe.
Reserve Opus for the diagnostic hypothesis, the conviction verdict, and the Evidence Pack
prose. Run cheap deterministic filters first; escalate only survivors to Opus.

---

## 6. The Evidence Pack — the meeting-winning deliverable

The advisory analogue of the Pitch Pack (`tool/evidence_pack.py`). Operationalises
Challenger (Teach → Tailor → Take-Control) + insight-led ABM. Seven parts:

1. **The Reframe** — one sharp Commercial Insight that disrupts the status quo, e.g.
   *"Functions of your size and sector typically run ~N comms professionals; your public
   footprint suggests you carry materially fewer, which usually shows up as [specific
   consequence]."* Must be **benchmark-anchored, never an insulting cold assertion.**
2. **The Outside-In Function Diagnostic** (hypothesis, clearly labelled) — the 1-page
   reasoned hypothesis from Opus pass #2.
3. **The Benchmarking Teaser** — a partial, credible benchmark (structure / headcount /
   salary band vs comparable orgs) — enough to prove the data asset and create a "give me
   the full picture" pull.
4. **Named Economic Buyer + Inferred Pain** — the function leader (or their boss), pain
   *tailored* to role (the CEO hears cost/risk; the comms leader hears
   credibility/capability).
5. **The Value Give-Away** — a genuine, free, useful artefact (a peer data point, an
   action-plan checklist tied to the GPG/equality deadline) — the ABM "high-value offer"
   that justifies the meeting.
6. **The Recommended Service + Proof Anchor** — the mapped VMA service and the relevant
   case study (Network Rail for org design/benchmarking).
7. **The Take-Control Ask** — a specific, low-friction next step (a 30-minute "show you
   the full benchmark" call), not "let me know if you're interested."

---

## 7. Associate routing & the unified consultancy console (the dream build)

The Conviction Verdict's recommended service determines the owner. Map from the brochure:

| Recommended service | Owner | Associate / partner |
|---|---|---|
| Org design / benchmarking | **Lucy Cairncross** (MD, Advisory) + Sara | VMA Advisory Services team |
| Coaching / development | Associate-led | **Joss Mathieson** (Change Oasis) · **Famn** (Molly & Roger Taylor) |
| ED&I consulting | Associate-led | **Antoinette Willcocks** (RiverRoad) · **Kate Isichei** (neuroinclusion / Where To Look) |
| Anything with a search component | **Sara** (BD/search) | — |
| Out-of-scope (non comms/marketing) | Referral lane | partner agency / engagement platform |

**Routing logic:** Sara owns the BD motion and any lead with a search component; Lucy
owns advisory leads and the diagnostic relationship; the associate is attached when the
service is coaching (Joss/Famn) or ED&I (Antoinette/Kate). Two referral lanes remain for
out-of-scope work — **never let the engine push VMA into general management
consulting.**

**The console (north star):** Sara (search BD) and Lucy + associates (advisory) work
from **one platform, one lead queue**, each lead tagged with its lane(s) and routed by
service-fit, every account showing its *whole* picture (hiring leads + advisory leads +
dossier history + the Evidence Pack). One company, one view, the right owner armed with a
meeting-winning, evidenced case. **This is how the company closes as many accounts as
possible across the full service catalogue** — not by chasing more vacancies, but by
giving every owner a reason to call every qualified account with something the client
can't get internally.

---

## 8. Advisory economics — the conversion-layer dependency

`tool/conversion.py` prices search only (`FEE_RATE_RETAINED 0.185`–`FEE_RATE_TOP 0.22`
on salary bands; `deal_profile`). Advisory work is **project / day-rate priced.** Add an
**advisory pricing model**: project-scoping bands per service (org-design review,
benchmarking report, coaching programme, ED&I engagement) with day-rate × duration
estimates, so an advisory lead carries a credible "what's it worth" chip the way a search
lead does. This is a named build dependency — flag it before the conversion layer can
render advisory deal value. (Bands to be set with Lucy; the engine should never invent
fee figures — same discipline as the search side.)

---

## 9. Credibility & compliance guardrails (non-negotiable, wire before go-live)

The reputational downside of one insulting or inaccurate pay-gap assertion outweighs many
won meetings. Hard-wire into the Evidence Pack Composer's system prompt:

- **Defensible hypothesis, never insulting assertion.** Every outside-in claim is framed
  as a benchmark-anchored *hypothesis* ("functions of your size typically…"), never a
  cold factual assertion about a named employer's failings. Use only published GOV.UK
  figures; frame gaps as **opportunities, not accusations**. Never assert an unverified
  claim about an employer's ED&I record or pay gap.
- **PECR/GDPR.** Advisory outreach to named senior individuals is still electronic
  marketing under PECR — route through the existing guarded send (test-mode default,
  suppression list, confidence floors, opt-out footer). Same discipline as search.
- **Source reliability flags.** Trade-press RSS truncates (verify role before
  triggering); PRmoment carries PR-submitted content; Glassdoor is noisy and
  ethically sensitive (**hypothesis-only, never quoted at a named org**); CorpComms is
  defunct (2025) and excluded.
- **Regulatory items are planning triggers, not facts in force.** Mandatory GEAPs (Spring
  2027 / Apr 2028) and proposed ethnicity/disability pay reporting are
  *announced/legislated-but-not-yet-fully-in-force* — treat dated deadlines as planning
  triggers and monitor for slippage. Cite the BCG PMI-failure figures (30–40%) carefully;
  don't conflate with the Fortune "70–75% of acquisitions fail" figure (it measures
  something different).
- **Human-in-the-loop on PURSUE and all outreach.** The engine keeps the consultant; the
  consultant wins the meeting. Revisit autonomy only after a sustained track record of
  accurate diagnostics and zero credibility incidents.

---

## 10. Opus + Claude Code at full power

- **Reasoned conviction over additive scoring** — Opus extended thinking weighs the
  signal mosaic to KILL/DEVELOP/PURSUE with named rationale; calibration ("acknowledges
  uncertainty") makes it reliable enough for an autonomous pass with human-in-the-loop on
  PURSUE.
- **Effort as a cost lever** — Sonnet at low/medium for the semantic scan over volume;
  escalate only survivors to Opus at high/extra-high for the diagnostic and verdict.
  Reserve `max` effort for genuinely hard targets (it can otherwise overthink).
- **Long-context document analysis** — read full annual reports/filings for skills-gap
  disclosures in one pass.
- **Claude Code surface:**
  - **Slash-commands / skills** for the repeatable operations — clone the `/red-team`
    pattern: `/advisory-brief <company|next>` (run the advisory conviction pass),
    `/diagnostic <company>` (the Outside-In Function Diagnostic), `/evidence-pack
    <company>` (compose the pack). Each writes a typed overlay + dossier note, returns one
    line in chat.
  - **Subagents** — one per priority account for parallel per-target investigation.
  - **Scheduled GitHub Actions** — a morning advisory pulse (mirrors the morning brief)
    and the GPG/equality-action calendar cron. `--max-budget-usd` caps every CI
    invocation; default to Sonnet for routine passes, reserve Opus for
    diagnostic/verdict/pack.
  - **MCP** — expose the dossier store and the benchmark dataset as tools.
  - **Cost discipline** — prompt-cache the shared benchmark corpus and the VMA service
    vocabulary across passes; deterministic pre-filters keep model calls off unqualified
    leads. **£0 running cost holds** (Anthropic API + Hunter for email verification
    remain the only paid lines, exactly as today).

---

## 11. Phased roadmap (file-level)

### Phase 1 — Reclassify advisory as a lead *(quick wins, ~1–2 weeks)*
Reuse signals already flowing; make advisory a visible lane, not just an enrichment line.
- Add an **advisory lane + tier** to the board/console (extend `tier_for`/the console so
  advisory leads render as their own section). *No new detection yet.*
- Promote the existing calendar pulses (`equality_pay_reporting_2026`,
  `gender_pay_gap_2026`, `uk_srs_2026`, `mog_post_sr_2026`, `nhs_planning_2026`) into
  **first-class advisory leads** with the new gate as a deterministic checklist.
- Build `PayGapActionMandate` **company-specific** off the GOV.UK GPG dataset already
  ingested: detect *widening* gaps year-on-year and missing/weak action plans → dated
  ED&I leads routed to Antoinette/Kate.
- Stand up `tool/advisory_gate.py` as a deterministic 6-dimension checklist.
- Ship a **v0 Evidence Pack** from a manual-assembly template (`tool/evidence_pack.py`
  with a template renderer; Opus prose comes in Phase 2).
- **Impact:** immediately surfaces advisory leads already hiding in data we already pull.

### Phase 2 — The advisory lead engine *(~1–2 months)*
- **Build `NewFunctionLeaderWindow` first** (§3.1 trade-press RSS detector) — the
  highest-yield single addition. Ship behind a 4-week precision gate.
- Implement the `tool/advisory_signals/` detector classes (B–J), most reusing ingested
  signals.
- Build the `AdvisoryScore` input bundle and the **Opus Conviction Verdict** pass
  (KILL/DEVELOP/PURSUE) + the advisory overlay store (clone `investigations`).
- Build the **Outside-In Function Diagnostic** pass — the proprietary instrument.
- Wire `/advisory-brief`, `/diagnostic`, `/evidence-pack` slash-commands + a scheduled
  advisory-pulse Action.
- **Impact:** autonomous advisory-lead origination with reasoned verdicts.

### Phase 3 — Unified consultancy console + Evidence Pack + routing *(~1–2 months)*
- Ship the full Opus **Evidence Pack Composer** with guardrails (§9) hard-wired.
- Ship **associate-matching/routing** (§7).
- Build the **unified console** — Sara + Lucy + associates on one queue, every account
  showing hiring + advisory leads + dossier + pack.
- Add the **advisory pricing model** to `conversion.py` (§8).
- **Impact:** one consultancy platform; every owner armed with a meeting-winning evidenced
  case across the full catalogue.

---

## 12. North star & how we measure "close as many accounts as possible"

The dream build is a **single talent-consultancy origination machine**: it watches every
UK comms/marketing/corporate-affairs function through free public data, forms a defensible
outside-in view of each one, and — the moment a real, evidenced advisory need appears —
hands the right VMA owner a meeting-winning case with the buyer named and the insight
written. Measured by:

- **Advisory leads originated / week** (with no vacancy attached) — the upstream metric
  agency work can't touch.
- **Evidence-Pack → meeting conversion** — the real finish line.
- **Accounts with ≥1 qualified advisory lead** — coverage of the closable universe.
- **Diagnostic accuracy & zero credibility incidents** — the gate to more autonomy.
- **Cross-sell rate** — accounts buying more than one service (the talent-consultancy
  thesis proven).

All on the same £0 running cost.

---

## 13. Decisions for you (the few genuine forks)

The plan is otherwise self-contained; these are business calls only VMA can make, and
they shape Phase 1 sequencing:

1. **Autonomy on PURSUE/outreach** — keep human-in-the-loop on every advisory PURSUE and
   send (recommended), or allow auto-DEVELOP nurture?
2. **Desk scope for advisory v1** — comms/corporate-affairs only first (recommended), or
   comms **and** marketing from day one?
3. **Sequencing** — `NewFunctionLeaderWindow` (trade-press, highest yield, new source)
   first, or `PayGapActionMandate` (fastest credible pipeline, reuses data we hold)
   first? (Recommended: ship `PayGapActionMandate` in Phase 1 because it's pure reuse,
   start `NewFunctionLeaderWindow` measurement in parallel.)
4. **Advisory pricing bands** — who sets the project-scoping bands with Lucy, and may the
   engine show ranges, or facts-only (no fee figures) until bands are signed off?

---

*Built to the platform's founding constraint: £0 running cost beyond the Anthropic API
and Hunter email verification. Free UK public sources only. Every kept claim cites a
source. The engine keeps the consultant; the consultant wins the meeting.*
