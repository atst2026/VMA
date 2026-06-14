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
from tool.advisory_board import render_board
from tool.advisory_outcomes import decision_cap
print(render_board(originate(), cap=decision_cap()))
"
```

The board groups Call-ready (PURSUE) → Developing → Killed, each row with
its conviction, owner + delivery associate, the one-line why, and the gate
scorecard chips (PAIN/SPONSOR/MANDATE/TIMING/ACCESS/PROOF).

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

## The Opus pass — Conviction Verdict + Outside-In Diagnostic

This is where Opus does the work (free here under the Claude Code
subscription — no API spend). For each **PURSUE** and strong **DEVELOP**
lead, run the advisory analogue of `/red-team`, **in a sub-agent per
lead**. Start from the grounded context the engine already assembled:

```bash
python3 -c "
from tool.advisory_diagnostic import assemble_context
from tool.advisory_signals import pay_gap as PG
from datetime import date
for s in PG.pay_gap_action_signals(today=date.today()):
    print(s.company); print(assemble_context(s))
"
```

Then reason as three roles:

1. **The Worker builds the case.** Verify the pain live — the GOV.UK
   gender-pay-gap figure, the employer's own action plan (or its absence),
   the headcount band vs the resourcing benchmark, the peer cohort. Frame
   every claim as a benchmark-anchored **hypothesis** ("functions of your
   size typically…"), never a cold assertion (ADVISORY_ENGINE.md §9). Name
   the economic buyer (CHRO/People Director + CEO sponsor — check
   `tool/state/hiring_contacts.json` then public record). Find the route
   (`tool/cascade.py`, `tool/following.py`, `tool/team_map.py`,
   `tool/propensity.py`: who at VMA already knows this buyer / shares a
   former employer). Write the single **sharpest insight** to lead with —
   it must rest on the non-public comparison, not the company's own pages.
2. **The Red-Team adviser tries to kill it.** *"This is generic — derivable
   from their homepage."* *"No reachable buyer — this is a cold approach to
   a senior person."* *"The gap is small and on-time — not a compelling
   event."* PURSUE only survives with a concrete pain, a named buyer, a
   warm route (else **nurture, don't cold-send**), and a defensible,
   novel insight.
3. **The Verifier checks every kept claim** against its GOV.UK / public
   source. Any claim that fails is removed; if the case no longer stands,
   the verdict drops to DEVELOP.

**Persist the verdict** so the gate and the Evidence Pack pick it up (it
overrides the deterministic verdict for 21 days):

```bash
python3 -c "
from tool.advisory_overlay import write
write('<company>', '<trigger>', '<PURSUE|DEVELOP|KILL>',
      conviction=<0-100>,
      named_pain='''<the evidenced pain>''',
      economic_buyer='<Name, Title>',
      recommended_service='<edi|benchmarking|org_design|coaching>',
      sharpest_insight='''<the one-line reframe to open on>''',
      diagnostic='''<the 1-page outside-in hypothesis>''',
      kill_reasons=['<if KILL/DEVELOP: what is missing>'],
      confidence='<High|Moderate|Low>')
"
```

(Unattended automation can instead call `tool.advisory_llm.run_and_persist`,
which is OFF by default — it runs the same pass via the API only when
`ADVISORY_LLM_ENABLED=1` and a key is set. The deterministic gate is always
the fallback.)

## Record the human decision (the dense feedback label)

Human-in-the-loop is also the training signal (ADVISORY_ENGINE.md §11 #1):
every PURSUE the owner approves or spikes tightens the engine. After Lucy /
Sara decide, log it — the trailing approval rate auto-throttles the PURSUE
cap so a board the humans stop trusting shrinks itself:

```bash
python3 -c "
from tool import advisory_outcomes as O
O.record('<company>', '<trigger>', '<pursue_approved|pursue_spiked|meeting_booked>',
         decided_by='<Lucy|Sara>', note='<one line>')
print('acceptance:', O.acceptance())   # rate + whether the cap is throttled
"
```

`meeting_booked` is the sparse TRUE outcome (the real finish line) — log it
when a meeting lands; it feeds /learn's longer-run recalibration.

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
