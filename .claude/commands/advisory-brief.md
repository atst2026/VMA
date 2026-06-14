---
description: Originate and qualify advisory-demand leads (Phase 1) — run the advisory lane (detect → 6-dimension MEDDPICC gate → KILL/DEVELOP/PURSUE verdict), then compose the Evidence Pack for the call-ready ones
argument-hint: "[company]  — default: today's whole advisory board"
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch, Agent
---

Run the advisory-demand pass: $ARGUMENTS

## Why this command exists (read before acting)

The hiring lane answers "who is hiring?". This lane answers the upstream
question that grows the consultancy: **"whose comms/marketing function is
stuck, over-stretched or misfiring — and what can VMA sell them?"** — even
when there is no vacancy at all. It originates advisory leads as a
first-class type, qualifies them on the consulting-adapted MEDDPICC gate,
and ships the meeting-winning **Evidence Pack** for the call-ready ones.

Phase 1 ships the deterministic spine and one detector
(`PayGapActionMandate`). The Opus **Conviction Verdict** and **Outside-In
Function Diagnostic** (ADVISORY_ENGINE.md §5) replace the deterministic
verdict and the v0 pack prose in Phase 2 — this command's shape is stable
across that change. Human-in-the-loop on every PURSUE (locked decision #1):
the engine arms the consultant; the consultant wins the meeting.

## Run the lane

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -c "
from tool.advisory_signals import originate
from tool import evidence_pack as EP
rows = originate()                       # detect -> gate -> rank -> cap
if not rows:
    print('No advisory leads today (no statutory window open, or the GPG '
          'index is not yet populated — host not on the egress allowlist).')
for r in rows:
    print(f\"{r['verdict']:7s} {r['conviction']:3d}  {r['company']:32s} \"
          f\"{r['trigger']}\")
    print('         why:', r['why'])
    q = r.get('qual') or {}
    print('         gate: PAIN%s SPONSOR%s MANDATE%s TIMING%s ACCESS%s PROOF%s '
          '(%s/12)' % (q.get('pain'), q.get('sponsor'), q.get('mandate'),
          q.get('timing'), q.get('access'), q.get('proof'), q.get('total')))
    o = r.get('owner') or {}
    line = '         owner: ' + str(o.get('owner', ''))
    if o.get('associate'):
        line += ' · delivery %s (%s)' % (o['associate']['name'],
                                         o['associate']['firm'])
    if o.get('co_owner'):
        line += ' · + ' + o['co_owner']
    print(line)
"
```

If `$1` names a company, filter to it. For each **PURSUE** lead, compose
and print the Evidence Pack so the owner can read it before the call:

```bash
python3 -c "
from tool.advisory_signals import originate, AdvisorySignal
from tool import evidence_pack as EP
for r in originate():
    if r['verdict'] != 'PURSUE': continue
    sig = AdvisorySignal(**{k: (tuple(v) if k=='window' and v else v)
                            for k,v in r['signal'].items()})
    print(EP.render_markdown(EP.compose(sig)))
    print('\n' + '='*72 + '\n')
"
```

## Per lead — when you want a deeper, verified case (optional, Opus)

For a **PURSUE** lead worth a verified write-up before outreach, do the
advisory analogue of `/red-team`, **in a sub-agent per lead**:

1. **Verify the pain** live — the GOV.UK gender-pay-gap figure, the
   employer's own action plan (or its absence), the headcount band. Quote
   every kept fact with its source; frame gaps as a benchmark-anchored
   **hypothesis** ("functions of your size typically…"), never a cold
   assertion about a named employer's failings (ADVISORY_ENGINE.md §9).
2. **Name the economic buyer** — the CHRO/People Director and the CEO
   sponsor; check `tool/state/hiring_contacts.json` then public record.
   A named, in-seat buyer is what moves the lead from DEVELOP to PURSUE.
3. **Find the route** — `tool/cascade.py`, `tool/following.py`,
   `tool/team_map.py`, `tool/propensity.py`: who at VMA already knows this
   buyer / shares a former employer / placed into their team. No warm
   route → keep it in **nurture**, do not cold-send a senior buyer.
4. **Sharpen the give-away** — the one-page peer comparison (this median
   gap and action-plan maturity vs the closest sector peers), using only
   published GOV.UK figures. This is the artefact that earns the meeting.

## Report back in chat

One line per lead: company — verdict — conviction — the evidenced pain —
the single missing piece (named buyer / warm route / second source). Then
the headline: which advisory leads the owner (Lucy / Sara, ED&I →
Antoinette / Kate) should take to a meeting this week, in order.

## Guardrails

- Free public sources only; every kept claim cites a source.
- Only published GOV.UK pay-gap figures; frame as opportunities, not
  accusations. Never assert an unverified claim about an employer's ED&I
  record.
- Facts-only: **no fee figures** until the project-scoping bands are
  signed off (locked decision #4).
- A standing gap is not a lead — the statutory window is the compelling
  event. Reward abstention: thin or registry-blind single-source signals
  stay DEVELOP, never a confident PURSUE.
- UK spelling. The case must read like an adviser wrote it.
