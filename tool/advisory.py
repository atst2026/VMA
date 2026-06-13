"""Advisory Services lens.

VMA Group's specialism is Executive Search / Permanent Recruitment /
**Advisory** / Communications. The detectors were built to surface
PLACEMENT leads only — half the specialism. Every high-precision signal
they already produce is *also* an Advisory entry point, and advisory
(comms-function capability review, talent & market mapping, leadership
benchmarking, succession advisory, reputation-readiness audit) is the
earlier, lower-barrier sale that frequently opens the door to the
retained search on the same account.

This module is the single place that maps an event class → its advisory
framing, so the advisory pitch language can be tuned centrally rather
than scattered across six detectors. It adds no new signal and changes
no detection — it exposes a second billable action on signals that
already passed the precision gate, fully in line with the strict
detection-engine filter.

Keys are stable predictor trigger keys (tool.predictive.patterns) plus
the standalone detector contexts ("water_sar", "contract_end",
"funding", "following") and the calendar-pulse keys.
"""
from __future__ import annotations

_DEFAULT = ("Advisory: comms-function capability review + talent/market "
            "mapping — the lower-barrier sale that opens the retained search.")

_ADVISORY: dict[str, str] = {
    # ---- predictor trigger keys ----
    "mna":
        "Advisory: integration & transition-comms operating-model review "
        "+ market map of the post-deal comms leadership.",
    "restructure":
        "Advisory: comms target-operating-model & capability review for "
        "the reorganised function.",
    "regulator_action":
        "Advisory: reputation-comms capability audit + crisis-readiness "
        "review ahead of the permanent reputation hire.",
    "regulator_probe_early":
        "Advisory: reputation-comms capability audit + crisis-readiness "
        "review for the live-investigation period.",
    "crisis_event":
        "Advisory: crisis & reputation-readiness audit; comms-function "
        "review ahead of the permanent reputation hire.",
    "profit_warning":
        "Advisory: IR & Corporate Affairs capability review + investor-"
        "narrative readiness.",
    "ceo_change":
        "Advisory: comms-function review aligned to the new CEO + a "
        "succession map of the comms bench.",
    "chair_change":
        "Advisory: board-/governance-comms review aligned to the new "
        "chair + succession map.",
    "cfo_change":
        "Advisory: IR & financial-comms capability review aligned to the "
        "new CFO.",
    "chro_change":
        "Advisory: internal-comms & change-capability review under the "
        "new people leadership.",
    "ir_director_change":
        "Advisory: IR function review + succession map for the "
        "IR/Corporate Affairs bench.",
    "ipo_listing":
        "Advisory: pre-admission IR / Corporate Affairs readiness review "
        "+ market map of the listed-co comms leadership.",
    "contract_loss":
        "Advisory: change & stakeholder-comms capability review post-"
        "loss; market map for the rebuild.",
    "ic_platform_rfp":
        "Advisory: internal-comms operating-model & channel review.",
    "press_velocity_spike":
        "Advisory: reputation-readiness review while coverage is "
        "elevated.",
    # ---- standalone detector contexts ----
    "water_sar":
        "Advisory: crisis/stakeholder-comms capability audit + comms-"
        "function org review + succession map for the permanent "
        "reputation hire.",
    "contract_end":
        "Advisory: change & transition-comms capability review + market "
        "map ahead of the recompete decision.",
    "funding":
        "Advisory: comms-function design-for-scale + benchmarking; build "
        "the senior-comms market map ahead of the ~6-month hire.",
    "following":
        "Advisory: succession & org review of the vacated comms function "
        "+ talent map of the replacement market.",
    # ---- calendar-pulse keys ----
    "fca_consumer_duty_2026":
        "Advisory: regulatory-comms capability & board-reporting "
        "readiness review + peer benchmarking ahead of 31 Jul.",
    "uk_srs_2026":
        "Advisory: sustainability / ESG-comms capability review + peer "
        "benchmarking ahead of the first mandatory reporting cycle.",
    "mog_post_sr_2026":
        "Advisory: GCS comms operating-model & transition-capability "
        "review for the reorganised department.",
    "agm_reporting_2026":
        "Advisory: IR & corporate-reporting comms capability review + "
        "AGM/governance-narrative readiness ahead of results season.",
    "gender_pay_gap_2026":
        "Advisory: internal/DEI-comms capability review + gender-pay "
        "narrative & scrutiny-response readiness.",
    "equality_pay_reporting_2026":
        "Advisory: ED&I capability & equality-action-plan readiness review "
        "+ ethnicity/disability pay narrative ahead of the new reporting "
        "duties.",
    "nhs_planning_2026":
        "Advisory: NHS comms operating-model & change-capability review "
        "for the planning/restructure round.",
    "he_clearing_2026":
        "Advisory: student-recruitment & brand-comms capability review "
        "+ market map ahead of clearing.",
}

# Marketing desk (FIRST DRAFT): the same advisory lens, marketing-flavoured,
# incl. the marketing calendar-pulse keys. Review with the marketing team.
_DEFAULT_MARKETING = (
    "Advisory: marketing-function capability review + talent/market mapping "
    "— the lower-barrier sale that opens the retained search.")
_ADVISORY_MARKETING: dict[str, str] = {
    "mna": "Advisory: brand-integration & rebrand operating-model review + "
           "market map of the post-deal marketing leadership.",
    "restructure": "Advisory: marketing target-operating-model & capability "
                   "review for the reorganised function.",
    "regulator_action": "Advisory: brand-trust & customer-marketing capability "
                        "audit ahead of the permanent hire.",
    "regulator_probe_early": "Advisory: brand-trust & customer-marketing "
                             "capability audit for the live-investigation period.",
    "crisis_event": "Advisory: brand-trust rebuild & customer-marketing "
                    "readiness review ahead of the permanent hire.",
    "profit_warning": "Advisory: demand-generation & retention capability "
                      "review + growth-narrative readiness.",
    "ceo_change": "Advisory: marketing-function review aligned to the new CEO "
                  "+ a succession map of the marketing bench.",
    "chair_change": "Advisory: brand & marketing-strategy review aligned to "
                    "the new chair + succession map.",
    "cfo_change": "Advisory: marketing-efficiency & ROI capability review "
                  "aligned to the new CFO.",
    "chro_change": "Advisory: marketing org & capability review under the new "
                   "people leadership.",
    "ir_director_change": "Advisory: brand / investor-marketing review + "
                          "succession map for the marketing bench.",
    "ipo_listing": "Advisory: pre-admission brand & investor-marketing "
                   "readiness review + market map of the listed-co marketing "
                   "leadership.",
    "contract_loss": "Advisory: demand & brand capability review post-loss; "
                     "market map for the rebuild.",
    "ic_platform_rfp": "Advisory: martech / CRM operating-model & channel review.",
    "press_velocity_spike": "Advisory: brand-reputation & share-of-voice "
                            "review while coverage is elevated.",
    "water_sar": "Advisory: brand-trust & customer-marketing capability audit "
                 "+ org review + succession map for the permanent hire.",
    "contract_end": "Advisory: bid, brand & customer-marketing capability "
                    "review + market map ahead of the recompete decision.",
    "funding": "Advisory: marketing-function design-for-scale + benchmarking; "
               "build the senior-marketing market map ahead of the ~6-month hire.",
    "following": "Advisory: succession & org review of the vacated marketing "
                 "function + talent map of the replacement market.",
    "peak_trading_2026": "Advisory: peak-trading campaign & performance-"
                         "marketing capability review + benchmarking ahead of "
                         "the Golden Quarter.",
    "marketing_budget_reset_2026": "Advisory: marketing operating-model & "
                                   "budget-allocation review + agency-roster "
                                   "benchmarking ahead of the new-year plan.",
}

def advisory_for(context: str | None) -> str:
    """Return the advisory framing for an event class. Profile-aware and
    resolved PER CALL (not at import) so the single dashboard process,
    which serves both desks per-request, returns marketing advisories on
    the marketing desk and comms advisories on the comms desk. Unknown /
    missing context falls back to the generic line (never empty)."""
    try:
        from tool.profiles import active_profile
        is_marketing = active_profile().key == "marketing"
    except Exception:
        is_marketing = False
    table = _ADVISORY_MARKETING if is_marketing else _ADVISORY
    default = _DEFAULT_MARKETING if is_marketing else _DEFAULT
    if not context:
        return default
    return table.get(str(context).strip().lower(), default)


# ===========================================================================
# Service-fit lens — the Talent-Consultancy upgrade.
#
# VMA Group sells far more than placements (Advisory Services brochure):
#   · Strategy & Organisation Design — full function review: consultation &
#     stakeholder analysis → benchmarking & design → implementation.
#   · Benchmarking — team structure, headcount and salary/remuneration vs
#     comparable organisations (the Network Rail engagement; the L'Oréal
#     "what do 10 peer comms teams look like?" report).
#   · Professional Development & Coaching — leadership / change / team
#     coaching via associates (Change Oasis, Famn).
#   · ED&I Consulting — listening sessions, training, inclusive comms and
#     neuroinclusion (RiverRoad, Where To Look Communications).
# Plus two referral lanes VMA monetises as the trusted adviser:
#   · Agency referral — a partner delivery agency (e.g. Sequel Group) when
#     the signal says workload WITHOUT headcount budget.
#   · Engagement platform — an employee-engagement / IC platform intro
#     (e.g. Workvivo by Zoom, Staffbase) when channels are the gap.
#
# This maps every trigger class → the RANKED SERVICE MIX the signal
# actually indicates, so a lead is "here is what they need and what VMA
# can sell", not just "here is a seat". Like advisory_for it adds no new
# signal and changes no detection — it is a second (and third) billable
# reading of events that already passed the precision gate. Stacked
# signals are COMBINED: each event votes for services, budget-pressure
# events steer the mix away from perm fees toward project-fee routes.
# ===========================================================================

SERVICES: dict[str, dict] = {
    "search": {
        "short": "Search",
        "label": "Executive search / permanent recruitment",
        "family": "hire",
        "blurb": "Retained search and permanent recruitment — the core line.",
    },
    "interim": {
        "short": "Interim",
        "label": "Interim & contract cover",
        "family": "hire",
        "blurb": ("Senior interim placed in days for cover, surges and "
                  "transformation — a fee that needs no perm headcount "
                  "sign-off."),
    },
    "org_design": {
        "short": "Org design",
        "label": "Advisory — Strategy & Organisation Design",
        "family": "advisory",
        "blurb": ("Full function review: consultation & stakeholder analysis "
                  "→ benchmark & design → implementation (the Network Rail "
                  "engagement)."),
    },
    "benchmarking": {
        "short": "Benchmarking",
        "label": "Advisory — Benchmarking (structure / headcount / salary)",
        "family": "advisory",
        "blurb": ("Team structure, headcount and remuneration benchmarked "
                  "against comparable organisations — the L'Oréal-style "
                  "peer report."),
    },
    "coaching": {
        "short": "Coaching",
        "label": "Advisory — Professional Development & Coaching",
        "family": "advisory",
        "blurb": ("Leadership, change and team coaching via VMA associates "
                  "(Change Oasis, Famn)."),
    },
    "edi": {
        "short": "ED&I",
        "label": "Advisory — Equity, Diversity & Inclusion Consulting",
        "family": "advisory",
        "blurb": ("ED&I and neuroinclusion consulting: listening sessions, "
                  "training, inclusive-communications support (RiverRoad, "
                  "Where To Look)."),
    },
    "agency_referral": {
        "short": "Agency referral",
        "label": "Agency referral — delivery without headcount",
        "family": "referral",
        "blurb": ("Refer a trusted delivery agency (e.g. Sequel Group) when "
                  "there is work but no headcount budget — VMA stays the "
                  "adviser on the account."),
    },
    "engagement_platform": {
        "short": "Engagement platform",
        "label": "Employee-engagement platform introduction",
        "family": "referral",
        "blurb": ("Introduce an employee-engagement / IC platform (e.g. "
                  "Workvivo by Zoom, Staffbase) when channels, not "
                  "headcount, are the gap."),
    },
}

# Trigger key → ordered (service, why-this-signal-means-this-need). The
# order is the sell priority; {fn} resolves to the active desk's function
# noun per call. Keys cover every predictor trigger
# (tool.predictive.patterns + the programmatic predictors), the standalone
# detector contexts and the calendar-pulse keys — tests enforce coverage
# so a new trigger can't ship without a service mix.
_SERVICE_FIT: dict[str, tuple[tuple[str, str], ...]] = {
    # ---- predictor trigger keys ----
    "ceo_change": (
        ("benchmarking", "New CEOs ask 'how does our {fn} function compare?' "
         "in the first 100 days — the peer benchmark of size, shape and pay "
         "answers it with data."),
        ("org_design", "A strategy reset usually means a {fn} operating-model "
         "review aligned to the new agenda."),
        ("coaching", "The incumbent {fn} leader has a new boss to impress — "
         "coaching carries them through the transition."),
        ("search", "If the review opens gaps, the retained search follows on "
         "the same account."),
    ),
    "chair_change": (
        ("org_design", "A new chair resets governance and board reporting — "
         "review board-comms capability against what the chair expects."),
        ("coaching", "Board-readiness coaching for the {fn} leader presenting "
         "to a new chair."),
        ("benchmarking", "A governance-comms peer benchmark arms the function "
         "for the new board's scrutiny."),
    ),
    "chro_change": (
        ("edi", "Incoming CHROs own the ED&I agenda — listening sessions and "
         "an inclusion audit are a natural first-90-days project."),
        ("engagement_platform", "New people leadership revisits the "
         "employee-engagement stack — introduce a platform (e.g. Workvivo) "
         "before an RFP exists."),
        ("coaching", "Leadership-development programmes are bought by the "
         "CHRO — pitch the coaching bench while priorities are being set."),
        ("org_design", "Internal-comms operating-model review under the new "
         "people leadership."),
    ),
    "mna": (
        ("org_design", "Two {fn} functions must become one — the integration "
         "operating-model review is the project sale that precedes every "
         "hire."),
        ("benchmarking", "Right-size the combined function with headcount and "
         "structure data from comparable organisations."),
        ("engagement_platform", "Two workforces, two channel stacks — an "
         "engagement platform unifies internal comms through integration."),
        ("interim", "Integration workload spikes before the perm structure "
         "lands — interim cover bridges it."),
        ("search", "Retained search for the combined function's top seats."),
    ),
    "activist_stake": (
        ("interim", "Activist defence needs senior IR/comms capacity now — "
         "interim lands in days, no headcount approval needed."),
        ("agency_referral", "Defence-comms surge without a payroll line — "
         "refer a delivery agency and stay the adviser."),
        ("search", "Defence usually exposes an IR / corporate-affairs "
         "capability gap — the permanent upgrade follows."),
    ),
    "pe_acquisition": (
        ("org_design", "New PE owners demand a lean, accountable {fn} "
         "operating model in the first 100 days — sell the review."),
        ("benchmarking", "PE houses buy data: benchmark the function's cost, "
         "headcount and pay against portfolio peers."),
        ("interim", "Value-creation projects get interim or project budget "
         "long before perm headcount."),
    ),
    "regulator_action": (
        ("search", "Regulatory remediation forces a permanent reputation / "
         "regulatory-comms hire — usually confidential and retained."),
        ("org_design", "A reputation-comms capability audit shows the "
         "regulator (and board) the function is being fixed."),
        ("agency_referral", "Remediation-period comms surge — agency support "
         "delivers while the perm hire completes."),
        ("coaching", "The {fn} leader is under board scrutiny — coaching "
         "keeps them effective through it."),
    ),
    "regulator_probe_early": (
        ("org_design", "A live probe is the moment to audit crisis- and "
         "regulatory-comms readiness — before findings land."),
        ("interim", "Investigation-period comms cover without a permanent "
         "commitment."),
        ("coaching", "Coach the leadership through investigation "
         "communications."),
    ),
    "crisis_event": (
        ("interim", "Crisis comms can't wait for a hiring round — senior "
         "interim cover lands in days."),
        ("agency_referral", "Surge media-handling capacity via a partner "
         "agency while the in-house team firefights."),
        ("search", "The permanent reputation hire follows nearly every major "
         "crisis — open the retained conversation now."),
        ("org_design", "Post-crisis: the function review that answers 'are we "
         "set up for the next one?'"),
        ("coaching", "Resilience coaching for a leadership team running on "
         "fumes."),
    ),
    "profit_warning": (
        ("agency_referral", "Headcount freezes follow profit warnings but the "
         "comms workload goes UP — a partner agency delivers without a "
         "payroll line."),
        ("interim", "Project-rate interim cover passes CFO scrutiny when perm "
         "requisitions don't."),
        ("benchmarking", "Arm the {fn} leader with peer data to defend the "
         "function in the cost review — the do-more-with-less case."),
        ("org_design", "If cuts are coming, sell the redesign so the smaller "
         "function is designed, not just shrunk."),
    ),
    "restructure": (
        ("org_design", "The org-design moment: a target-operating-model "
         "review for the reorganised function — the Network Rail engagement "
         "exactly."),
        ("benchmarking", "Headcount benchmarking vs comparable organisations "
         "gives the restructure its rationale — and the leader their "
         "evidence."),
        ("coaching", "Change-leadership coaching for the leaders carrying the "
         "reorganisation."),
        ("interim", "Change-comms cover through the transition."),
    ),
    "redundancy": (
        ("interim", "Redundancy programmes need senior change-comms "
         "capability now — interim specialists carry the consultation "
         "period."),
        ("coaching", "Coach the leaders delivering the hardest messages of "
         "their careers."),
        ("edi", "Survivor engagement and a fair, inclusive process — ED&I "
         "consulting protects culture (and reputation) through cuts."),
        ("benchmarking", "Right-size with data: a structure benchmark shows "
         "what the post-redundancy function should look like."),
        ("agency_referral", "Overflow delivery via a partner agency while "
         "headcount is frozen."),
    ),
    "cfo_change": (
        ("benchmarking", "A new CFO challenges every function's cost — salary "
         "& headcount benchmarking arms the {fn} leader with the defence."),
        ("search", "IR and financial-comms upgrades typically follow within "
         "two quarters."),
        ("coaching", "Coach the {fn} leader to build the new-CFO relationship "
         "— their budget depends on it."),
    ),
    "ir_director_change": (
        ("search", "The vacated IR seat itself — retained, usually "
         "confidential."),
        ("interim", "Bridge the gap to results season with interim IR "
         "cover."),
        ("benchmarking", "Benchmark the IR function while the seat is open — "
         "re-spec before re-hiring."),
    ),
    "comms_leader_departure": (
        ("search", "The vacated senior seat — the highest-converting retained "
         "brief there is."),
        ("interim", "Bridge cover protects the function while the search runs "
         "— and often converts to the fee."),
        ("org_design", "A leadership vacancy is the cheapest moment to "
         "redesign — review the function before refilling it like-for-like."),
        ("benchmarking", "Re-spec and re-price the role against the market "
         "before the search starts."),
    ),
    "cmo_change": (
        ("search", "Incoming CMOs rebuild their team in the first 90 days — "
         "get the succession map in front of them early."),
        ("benchmarking", "New marketing leaders benchmark their inherited "
         "function — sell the data pack."),
        ("org_design", "Marketing operating-model review aligned to the new "
         "CMO's strategy."),
    ),
    "ic_platform_rfp": (
        ("engagement_platform", "A live platform decision — introduce the "
         "right engagement platform (Workvivo by Zoom, Staffbase…) and own "
         "the trusted-adviser seat."),
        ("org_design", "Channel & IC operating-model review BEFORE platform "
         "lock-in — the platform should fit the design, not define it."),
        ("search", "Platform investment predicts a Head of IC hire within "
         "~90 days — open the search conversation now."),
        ("interim", "Implementation and launch-comms cover while the platform "
         "lands."),
    ),
    "ipo_listing": (
        ("search", "Listed-co IR / corporate-affairs build — multiple "
         "retained seats around admission."),
        ("benchmarking", "Benchmark structure & pay against the listed-peer "
         "cohort before building the function."),
        ("org_design", "Pre-admission readiness review of the whole "
         "corporate-affairs function."),
        ("coaching", "Coach the leadership for listed-company scrutiny — "
         "results days, analysts, the lot."),
        ("interim", "Prospectus and roadshow surge — interim cover through "
         "admission."),
    ),
    "contract_loss": (
        ("interim", "Transition and stakeholder comms through the loss — "
         "interim lands fast while perm budgets wobble."),
        ("org_design", "The rebuild review: what should the {fn} function "
         "look like after the loss?"),
        ("agency_referral", "Keep delivery moving via a partner agency while "
         "headcount is frozen."),
    ),
    "personal_brand_velocity": (
        ("coaching", "A rising leader is a flight risk — a development "
         "programme is the retention play their employer should buy."),
        ("search", "If they move, two seats open: succession at home, "
         "build-out at the destination — map both."),
    ),
    "ned_trustee_appointment": (
        ("coaching", "Portfolio-career leaders buy board-readiness coaching — "
         "and refer it on to their exec teams."),
        ("search", "A NED seat often precedes a step back from the exec role "
         "— watch the succession."),
    ),
    "press_velocity_spike": (
        ("agency_referral", "A coverage surge outstrips in-house capacity — a "
         "partner agency absorbs it without headcount."),
        ("interim", "Senior media-handling cover at project rates while the "
         "story runs."),
        ("org_design", "Reputation-readiness review while leadership "
         "attention (and budget) is on comms."),
    ),
    "rebrand": (
        ("agency_referral", "Rebrand rollout is classic agency-delivered work "
         "— refer the partner and stay the adviser on the hires."),
        ("search", "Rebrands expose brand-leadership gaps — the senior brand "
         "hire follows."),
        ("engagement_platform", "Landing a new brand internally is an "
         "engagement-channel test — a platform introduction fits the "
         "moment."),
        ("benchmarking", "Benchmark the brand/{fn} team against peers before "
         "scaling it for the new identity."),
    ),
    "agency_account_move": (
        ("agency_referral", "They are actively buying agency services — refer "
         "the right partner (e.g. Sequel Group for employee comms) and own "
         "the introduction."),
        ("org_design", "An account move signals the in-house/agency mix is "
         "under review — sell the operating-model piece."),
        ("search", "In-housing moves mean in-house hires — the team build "
         "follows the agency decision."),
    ),
    "market_entry": (
        ("benchmarking", "Entrants ask exactly the L'Oréal question — 'what "
         "does a {fn} function look like here?' — sell the local peer "
         "benchmark."),
        ("search", "The in-country build: the first senior {fn} hire around "
         "launch."),
        ("org_design", "Design the local function from a blank sheet — before "
         "the wrong structure gets hired into."),
        ("interim", "Launch-period cover while the perm build completes."),
    ),
    "framework_award": (
        ("search", "Framework delivery needs the comms/engagement capability "
         "the bid promised — hires follow the award."),
        ("interim", "Ramp for delivery at day rates while perm requisitions "
         "process."),
    ),
    "esg_bcorp": (
        ("edi", "ESG commitments are audited on the people dimension — ED&I "
         "consulting (listening, training, inclusive comms) makes the S "
         "real."),
        ("search", "Sustainability-comms capability gets hired after the "
         "commitment goes public."),
        ("benchmarking", "Benchmark ESG-comms capability against the sector's "
         "leaders."),
    ),
    "martech_adoption": (
        ("search", "Platform spend predicts operator hires — marketing-ops "
         "and digital roles follow the stack."),
        ("org_design", "A martech investment is an operating-model question — "
         "review the team around the tools."),
        ("interim", "Implementation-period cover at project rates."),
    ),
    "leadership_tenure": (
        ("coaching", "Develop the internal successor bench — the development "
         "programme for 'current or potential future leaders', sold to the "
         "incumbent."),
        ("search", "Long tenure = succession risk; the quiet succession map "
         "is the retained conversation."),
        ("benchmarking", "A decade-old role spec needs re-benchmarking "
         "against today's market before any transition."),
    ),
    "secured_financing": (
        ("benchmarking", "New capital, new plan — benchmark the {fn} function "
         "against where the business is going, not where it has been."),
        ("search", "Funded growth converts to senior hires within about two "
         "quarters."),
        ("interim", "Project capacity for the expansion push without waiting "
         "for perm sign-off."),
    ),
    "ownership_change": (
        ("org_design", "New owners review every function — sell the {fn} "
         "operating-model review before they commission a generalist "
         "consultancy."),
        ("benchmarking", "Owners want data — function cost, structure and pay "
         "vs peers."),
        ("search", "Ownership transitions reshuffle leadership — succession "
         "work follows."),
    ),
    "inhouse_search_failing": (
        ("search", "They have already paid the cost of DIY — the retained "
         "rescue is the highest-converting pitch in the book."),
        ("benchmarking", "45+ days unfilled usually means a mis-priced or "
         "mis-specced role — a salary & role benchmark diagnoses why and "
         "resets the brief."),
        ("interim", "Bridge the still-empty seat while the rescued search "
         "runs."),
    ),
    "hiring_restart": (
        ("search", "First senior posting after a freeze — competitors still "
         "treat the account as dormant; move first."),
        ("benchmarking", "Post-freeze rebuilds start with 'what should the "
         "function look like now?' — sell the structure benchmark."),
        ("org_design", "The freeze shrank the function; the restart is the "
         "moment to redesign rather than backfill."),
    ),
    "mishire_reversal": (
        ("search", "A failed senior hire forces an urgent, confidential "
         "replacement — retained by nature."),
        ("interim", "Immediate cover while the replacement search runs "
         "discreetly."),
        ("coaching", "De-risk hire #2: onboarding & transition coaching sold "
         "alongside the placement."),
    ),
    # ---- programmatic predictors (cluster / gap / displacement lanes) ----
    "job_ad_cluster": (
        ("search", "A cluster of mid-level ads usually precedes the senior "
         "hire who will lead them — pitch the leadership search before it is "
         "advertised."),
        ("org_design", "Rapid team growth without a design — sell the "
         "structure review before the headcount lands wrong."),
        ("benchmarking", "Benchmark the growing team's shape & pay against "
         "peers while the requisitions are still open."),
    ),
    "hiring_gap": (
        ("search", "A scaling business with no {fn} function — the "
         "foundational senior hire is overdue; create the role with a spec "
         "pitch."),
        ("benchmarking", "Show the gap with data: what peers at this size "
         "already have in place."),
        ("org_design", "Design the first {fn} function from scratch — before "
         "the first wrong hire defines it."),
    ),
    "seniority_gap": (
        ("search", "A junior team with no leader — the senior hire that "
         "unlocks the function."),
        ("coaching", "Or grow one: a development programme for the strongest "
         "internal candidate."),
        ("benchmarking", "Peer data shows what seniority the function carries "
         "at comparable organisations."),
    ),
    "framework_displacement": (
        ("search", "A displaced incumbent means the winner needs delivery "
         "capability — hires follow."),
        ("interim", "Transition-period cover for the winner or the displaced "
         "team's clients."),
    ),
    # ---- standalone detector contexts ----
    "water_sar": (
        ("interim", "SAR-period stakeholder comms is immediate and intense — "
         "interim capability lands in days."),
        ("agency_referral", "Surge support via a partner agency while "
         "finances are under administration scrutiny."),
        ("search", "The permanent reputation/stakeholder hire follows the "
         "regime — position early."),
        ("org_design", "Crisis/stakeholder-comms capability audit for the "
         "administration period."),
    ),
    "contract_end": (
        ("interim", "Bid and retention comms surge ahead of the recompete."),
        ("org_design", "Capability review before the recompete decision — "
         "incumbents that look match-fit retain."),
        ("search", "Win or lose, the delivery comms team gets rebuilt — map "
         "it now."),
    ),
    "funding": (
        ("search", "Funded scale-ups make the senior {fn} hire ~6 months "
         "post-round — open the conversation before the role is written."),
        ("benchmarking", "Design-for-scale: benchmark what the function "
         "should look like at the next stage — the data pack that wins the "
         "later search."),
        ("org_design", "First proper {fn} function design while the org chart "
         "is still wet ink."),
        ("engagement_platform", "Headcount doubling? The first engagement-"
         "platform decision is coming — introduce it."),
    ),
    "following": (
        ("search", "The vacated seat at the previous employer — successor "
         "search, plus the mover's build-out at the destination."),
        ("interim", "Bridge cover for the vacated function while the "
         "successor search runs."),
        ("org_design", "The vacancy is the cheapest moment to redesign the "
         "function rather than refill like-for-like."),
    ),
    "interim_watch": (
        ("interim", "A senior interim is already in the seat — extensions and "
         "the next cover brief are live business."),
        ("search", "Interim cover converts to a perm search — be there when "
         "the conversion decision lands."),
    ),
    "follow_on": (
        ("search", "A senior mover's first 90 days produce one or two "
         "lieutenant hires — the follow-on brief."),
        ("benchmarking", "Help the new leader size their build-out with peer "
         "data."),
    ),
    "cascade": (
        ("search", "A senior move cascades: successor seat behind them, "
         "build-out ahead of them — two searches from one event."),
        ("interim", "Bridge the vacated seat while the successor search "
         "runs."),
    ),
    "stale_mandate": (
        ("search", "A competitor's brief gone stale — the rescue pitch: "
         "switch it to a retained search that actually closes."),
        ("benchmarking", "Stale usually means mis-priced — the salary "
         "benchmark resets the brief and positions VMA as the fix."),
        ("interim", "Offer cover while the failed search restarts."),
    ),
    # ---- calendar-pulse keys ----
    "fca_consumer_duty_2026": (
        ("org_design", "Board-report season: a regulatory-comms capability "
         "review evidences Consumer Duty readiness before 31 Jul."),
        ("benchmarking", "Peer-benchmark the comms function's Duty readiness "
         "— boards buy comparisons."),
        ("interim", "Deadline-surge cover for the reporting run-up."),
    ),
    "uk_srs_2026": (
        ("org_design", "First mandatory sustainability-reporting cycle — "
         "review ESG-comms capability before the disclosures land."),
        ("search", "Sustainability-comms hires precede the first reporting "
         "cycle."),
        ("benchmarking", "Benchmark ESG-comms capability against "
         "early-adopter peers."),
    ),
    "mog_post_sr_2026": (
        ("org_design", "Machinery-of-government change IS an operating-model "
         "project — the GCS review is the natural sell."),
        ("interim", "Transition-comms cover through departmental "
         "reorganisation."),
        ("coaching", "Coach comms leaders carrying their teams through the "
         "reorganisation."),
    ),
    "agm_reporting_2026": (
        ("interim", "Results-season surge: AGM and annual-report comms cover "
         "at project rates."),
        ("agency_referral", "Annual-report and AGM collateral is classic "
         "agency-delivered work — refer the partner, keep the account."),
        ("search", "IR / corporate-reporting capability gaps surface at AGM "
         "season — the hire follows."),
    ),
    "gender_pay_gap_2026": (
        ("edi", "The ED&I consulting window: gap narrative, listening "
         "sessions and inclusion training around the statutory deadline."),
        ("benchmarking", "Literal remuneration benchmarking — the salary & "
         "remuneration product, sold on a statutory clock."),
        ("coaching", "Coach the leaders fronting the numbers internally and "
         "externally."),
    ),
    "equality_pay_reporting_2026": (
        ("edi", "Equality action plans plus ethnicity/disability pay "
         "reporting are an ED&I-consulting build: systemic-barrier review, "
         "listening sessions and inclusive-comms support ahead of the duty."),
        ("benchmarking", "Pay-gap and workforce-composition reporting needs "
         "the numbers in context — remuneration and representation "
         "benchmarked against comparable organisations."),
        ("coaching", "Coach the leaders who must own the equality-action-plan "
         "narrative to the board and externally."),
    ),
    "nhs_planning_2026": (
        ("org_design", "Planning-round restructures are operating-model "
         "reviews by another name — sell the NHS comms TOM piece."),
        ("interim", "Planning-period change-comms cover at day rates."),
        ("coaching", "Develop NHS comms leaders through the restructure "
         "cycle."),
    ),
    "he_clearing_2026": (
        ("agency_referral", "Clearing campaign surge is agency-shaped work — "
         "refer the partner; HE budgets rarely fund perm hires mid-cycle."),
        ("interim", "Clearing-period campaign cover at day rates."),
        ("search", "Student-recruitment marketing leadership gaps show up in "
         "clearing results — the hire conversation follows."),
    ),
    "peak_trading_2026": (
        ("interim", "Golden Quarter campaign surge — interim and contract "
         "cover locked before autumn."),
        ("agency_referral", "Peak campaign delivery via a partner agency when "
         "perm headcount is capped."),
        ("benchmarking", "Peak-readiness benchmark vs retail peers — sold in "
         "summer, used in September."),
    ),
    "marketing_budget_reset_2026": (
        ("benchmarking", "Planning season runs on data — salary & structure "
         "benchmarks land straight into the new-year budget deck."),
        ("org_design", "New-year operating-model review: in-house/agency mix, "
         "martech, structure."),
        ("agency_referral", "Q1 agency reviews — refer the right partner and "
         "own the adviser seat."),
    ),
}

# Marketing desk (FIRST DRAFT, review with the marketing team): the {fn}
# noun already re-tunes most reasons; this table overrides only triggers
# whose service MIX itself differs on the marketing desk. Unlisted keys
# fall back to the base table.
_SERVICE_FIT_MARKETING: dict[str, tuple[tuple[str, str], ...]] = {
    "mna": (
        ("org_design", "Two marketing functions and two brand architectures "
         "must become one — the integration operating-model review precedes "
         "every hire."),
        ("benchmarking", "Right-size the combined marketing function with "
         "headcount and structure data from comparable organisations."),
        ("agency_referral", "Brand-integration rollout is agency-shaped "
         "delivery — refer the partner and stay the adviser."),
        ("interim", "Integration workload spikes before the perm structure "
         "lands — interim cover bridges it."),
        ("search", "Retained search for the combined function's top seats."),
    ),
}

# When unknown / missing context, the lead still deserves a service mix —
# the generic Talent-Consultancy ladder (mirrors _DEFAULT's framing).
_DEFAULT_FIT: tuple[tuple[str, str], ...] = (
    ("benchmarking", "Every conversation carries the benchmark: how the {fn} "
     "function compares with peers on size, shape and pay."),
    ("org_design", "Function review — the lower-barrier project sale that "
     "opens the retained search."),
    ("search", "The retained search once the review shows the gaps."),
)

# Triggers that say "money is tight": perm requisitions are likely frozen
# even though the workload is rising, so the mix must carry at least one
# project-fee route (interim / agency referral) and the card says why.
_BUDGET_STRAINED: frozenset[str] = frozenset({
    "profit_warning", "redundancy", "restructure", "contract_loss",
    "activist_stake", "water_sar", "he_clearing_2026", "mog_post_sr_2026",
})

_BUDGET_NOTE = ("Budget-pressure signal in this stack: perm headcount may be "
                "frozen — lead with project-fee routes (advisory, interim, "
                "agency referral) and bank the retained search for when the "
                "freeze lifts.")

_STRAIN_REFERRAL_REASON = ("Workload is rising while headcount is frozen — a "
                           "partner agency (e.g. Sequel Group) delivers "
                           "without a payroll line; VMA refers and stays the "
                           "adviser.")


def service_fit_for(contexts, max_services: int = 3) -> dict:
    """The ranked VMA service mix a signal stack indicates.

    `contexts` is any iterable of trigger keys (one key for a standalone
    detector row; every stacked event's key for a predictor — stacking is
    the point: each event votes for services, so a CEO change + restructure
    stack surfaces org design & benchmarking above the bare hire). Profile-
    aware per call like advisory_for. Never raises, never returns an empty
    mix.

    Returns {"services": [{key, short, label, family, reason}…],
             "headline": "Benchmarking · Org design · Interim",
             "budget_note": str | None} — JSON-serialisable for row dicts.
    """
    try:
        from tool.profiles import active_profile
        is_marketing = active_profile().key == "marketing"
    except Exception:
        is_marketing = False
    fn = "marketing" if is_marketing else "comms"

    keys: list[str] = []
    for c in contexts or []:
        k = str(c or "").strip().lower()
        if k and k not in keys:
            keys.append(k)

    # Aggregate votes across the stack: more events naming a service rank
    # it higher; ties break on the best (earliest) position any event gave
    # it, then on event order for determinism.
    agg: dict[str, dict] = {}
    matched = False
    for order, k in enumerate(keys):
        fit = (_SERVICE_FIT_MARKETING.get(k) if is_marketing else None) \
            or _SERVICE_FIT.get(k)
        if not fit:
            continue
        matched = True
        for pos, (svc, reason) in enumerate(fit):
            cur = agg.get(svc)
            if cur is None:
                agg[svc] = {"votes": 1, "pos": pos, "order": order,
                            "reason": reason}
            else:
                cur["votes"] += 1
                if pos < cur["pos"]:
                    cur.update(pos=pos, order=order, reason=reason)
    if not matched:
        for pos, (svc, reason) in enumerate(_DEFAULT_FIT):
            agg[svc] = {"votes": 1, "pos": pos, "order": 0, "reason": reason}

    ranked = sorted(agg, key=lambda s: (-agg[s]["votes"], agg[s]["pos"],
                                        agg[s]["order"]))
    top = ranked[:max(1, int(max_services))]

    # Budget-strain steer: money-is-tight stacks must offer a route that
    # doesn't need a perm requisition.
    strained = any(k in _BUDGET_STRAINED for k in keys)
    if strained and not any(s in ("interim", "agency_referral") for s in top):
        alt = next((s for s in ranked if s in ("interim", "agency_referral")),
                   None)
        if alt is None:
            agg["agency_referral"] = {"reason": _STRAIN_REFERRAL_REASON}
            alt = "agency_referral"
        if len(top) >= max_services:
            top[-1] = alt
        else:
            top.append(alt)

    services = []
    for s in top:
        cat = SERVICES.get(s, {})
        services.append({
            "key": s,
            "short": cat.get("short", s),
            "label": cat.get("label", s),
            "family": cat.get("family", "advisory"),
            "reason": str(agg[s].get("reason") or "").replace("{fn}", fn),
        })
    return {
        "services": services,
        "headline": " · ".join(sv["short"] for sv in services),
        "budget_note": _BUDGET_NOTE if strained else None,
    }


def service_fit_line(contexts) -> str:
    """Compact one-line form for dense surfaces (email, list rows):
    'Sell: Benchmarking · Org design · Interim'. Never raises."""
    try:
        fit = service_fit_for(contexts)
        return f"Sell: {fit['headline']}" if fit.get("headline") else ""
    except Exception:
        return ""
