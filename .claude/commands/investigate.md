---
description: Run the per-trigger investigation playbook on a queued BD hypothesis — corroborate it, kill it, or schedule a recheck; the verdict feeds the presentation gate
argument-hint: "<company or pid> | next  — e.g. /investigate \"Severn Trent\"  or  /investigate next"
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch
---

Investigate the queued BD hypothesis: $ARGUMENTS

## Why this command exists (read before acting)

The presentation gate queues anything thin, fresh or unproven instead of
showing it to an Account Director. This command is how a queued
hypothesis gets resolved: you run the trigger-specific playbook against
free public sources, actively seek DISCONFIRMING evidence, and end with
exactly one verdict — confirmed (presents at High confidence), killed
(never presents, with the reason on record), or recheck (stays queued
until a date). An AD's trust is the product; a wrong "confirmed" costs
more than ten "rechecks".

## Steps

1. **Pick the target.** If $1 is `next` (or empty), list the queue and pick
   the strongest:
   ```bash
   cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -c "
   import json
   from tool import predictor_pipeline as PP, lead_engine as LE, gate, verdict_log, investigations
   from tool.profiles import active_profile
   verds, invs = verdict_log.get_all(), investigations.get_all()
   rows = []
   for e in PP.all_predictors():
       if (e.get('status') or 'active') != 'active': continue
       lead = LE.score_lead(e, 'predictor', active_profile().key)
       g = gate.assess(e, lead, verdicts=verds, investigation=invs.get(e.get('pid')))
       if not g['presented']:
           rows.append((lead.get('signal') or 0, e['pid'], e.get('company'),
                        ' | '.join(g['reasons']), g['investigate']))
   rows.sort(reverse=True)
   for s, pid, co, why, inv in rows[:15]:
       print(f\"{'INVESTIGATE ' if inv else '            '}{pid:30s} {co or '':28s} signal={s:<5} {why}\")
   "
   ```
   Otherwise resolve $1 to a pid (pids are normalised company names).

2. **Start from memory, never zero.** Read the dossier and current state:
   `tool/state/dossiers/<pid>.md` (full signal timeline, gate history,
   prior verdicts and notes), plus the entry's events in
   `tool/state/predictor_pipeline.json`.

3. **Run the playbook for the strongest trigger class** (below). Use the
   web tools to verify live; cite every fact you keep.

4. **Adversarial pass before any verdict.** Answer in writing: *Given this
   evidence, would a sceptical AD call tomorrow? What is the single
   weakest link? What innocent explanation fits all the same facts?* If
   the innocent explanation survives, the verdict is not "confirmed".

5. **Record any propensity facts you found along the way** — TA team
   spotted, agency-posted ads, a recruitment-supplier award — so the
   whole engine learns, not just this lead:
   ```bash
   python3 -c "from tool import propensity; propensity.record_finding(
       '<company>', internal_ta=<True|False|None>,
       agency_user=<True|False|None>,
       agency_scope='<comms_marketing|general|temp_staffing>',
       note='<finding>', source_url='<url>')"
   ```
   Scope the agency fact honestly: `comms_marketing` only when the fee
   was for VMA's disciplines (an agency-posted comms/marketing ad, a
   trade-press appointment crediting a search firm, a function-scoped
   award); `temp_staffing` when the only evidence is temp/interim volume
   supply (this deliberately does NOT count as a proven search
   fee-payer); `general` otherwise.

6. **Record the verdict** (this is what the gate reads — one per company):
   ```bash
   python3 -c "
   from tool import investigations, dossier
   investigations.write_overlay('<pid>', '<confirmed|killed|recheck>',
                                note='<one-line reason>', recheck_days=<int or None>)
   dossier.append_note('<pid>', '''<your full findings: what you checked,
   what confirmed, what you could not verify, the weakest link>''')
   "
   ```

7. **Report back in chat**: verdict, the three strongest facts with
   sources, the weakest link, and — if confirmed — the named buyer and
   the first move the AD should make.

## Mandatory: the incumbent check (every trigger class)

Before treating the predicted seat as real, ask the question the
pipeline cannot answer alone: **who, if anyone, already holds this
function at the company?** Search the TITLE FAMILY, never just the
predicted title — "Corporate Affairs Director" must also surface a
"Group Corporate Communications Director", an "External Affairs
Director", etc. (LinkedIn public profiles, the team/leadership page and
its Wayback history, trade-press appointment notes.) Then:

- **Incumbent found and current** — the lead is NOT dead, it is
  reframed: the trigger funds a build UNDER them and they are likely the
  economic buyer. Name them in the verdict. If their tenure is short or
  the team page is churning beneath them, say so.
- **Seat genuinely open/absent** — say what you checked; absence of a
  LinkedIn profile alone is weak evidence.
- Either way the pipeline's own `incumbent_status` on the entry is a
  lead, not a verdict — it is a single cached search; verify it.

## Playbooks (confirm / stack / kill / window)

**Leadership change** (CEO/CFO/CHRO/Chair/comms leader): Confirm identity
and exact start date via Companies House officer filings cross-checked
with press; establish whether the role owns or budgets comms/marketing.
Stack: prior-company agency use, team-page churn beneath them (Wayback),
"newly created" senior comms/marketing posts 4–12 weeks after their
start, press-velocity change. Kill: interim/caretaker cover; change at an
inaccessible subsidiary; incumbent agency lock. Window: present 4–12
weeks post-start (the gate's hold already enforces the near edge).

**Funding round**: Confirm round, amount, date and investors via at least
two independent outlets or an RNS. Stack: careers-page job growth, GTM
roles appearing, first-ever senior marketing hire language. Kill:
bridge/down round; proceeds earmarked R&D-only; team already complete.
Window: budget is genuinely deployable ~9–12 weeks post-close.

**Job-cluster growth**: Confirm 3+ related comms/marketing roles inside 30
days on the company's own boards (not recruiter reposts — check the
poster). Stack: seniority mix implying a team build; corroborating
funding/leadership in the dossier. Kill: one role reposted many times
(that's an `inhouse_search_failing` lead instead — re-route, don't
discard); roles outside VMA's disciplines. Window: 1–3 weeks; act fast.

**Team-page departure / mishire**: Confirm the person actually left
(LinkedIn public profile, press, Companies House) — not a site redesign.
Stack: no backfill posted (open seat), short tenure (failed-hire
signature), churn beneath the same leader. Kill: cosmetic page change;
planned, long-flagged retirement. Window: urgent while the seat is open.

For any other trigger class, generalise: one primary source confirming
the event, two independents corroborating the implication, one honest
attempt to kill it.

## Guardrails

- Free public sources only. Every kept fact needs a working citation.
- Never fabricate; "could not verify" is a finding, not a failure.
- One overlay per company — a new verdict replaces the old.
- Overlays expire after 21 days automatically; do not try to make a
  verdict permanent.
- UK spelling in notes.
