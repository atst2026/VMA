---
description: CFO-proof internal business case — arm the comms/marketing champion to win the fee sign-off fight inside their own company
argument-hint: "<company> [role title]  — e.g. /cfo-memo \"Severn Trent\" \"Head of Corporate Affairs\""
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch
---

Build the internal business-case memo for: $ARGUMENTS

## Why this command exists (read before acting)

In a budget-cut market the deal is usually lost INSIDE the target company:
the CCO/CMO wants the search, their CFO says no to the fee. This memo is
not a pitch to the buyer — it is the one-pager the buyer takes into their
own budget meeting. It must read like it was written by their side:
sober, numbers-first, every assumption labelled.

## Steps

1. **Inputs.** $1 = company (required). $2 = role title (default
   "Communications Director" on the comms desk, "Marketing Director" on
   the marketing desk — check `VMA_PROFILE`).

2. **House numbers — never invent salary or cost figures.** Pull them from
   the repo's own calculators:
   ```bash
   cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -c "
   from tool.pitch_pack import _salary_band, estimate_total_comp, cost_of_vacancy
   lo, hi, matched = _salary_band('<role>')
   mid = (lo + hi) // 2
   print('band:', lo, hi, 'matched:', matched)
   print('total comp:', estimate_total_comp(mid))
   print(cost_of_vacancy('<role>', mid))
   "
   ```

3. **Live context.** Pull the company's current triggers from
   `tool/state/predictor_pipeline.json` and `tool/state/latest_signals.json`
   (and `tool/state/posting_ledger.json` — an aged or reposted role at this
   company is the strongest possible exhibit: their in-house route is
   already failing and every week is costed). Verify the lead trigger with
   one quick web check. If the company has a live hiring freeze or
   administration signal, stop and say so — this memo would be tone-deaf.

4. **Write the memo** to
   `tool/state/outbox/cfo_memo_<company-slug>_<YYYY-MM-DD>.md`:
   - **Framing line**: from the champion's desk to their CFO — "the cost
     question is not the fee, it is the empty seat".
   - **Cost of the vacant seat**: monthly figure from the house calculator,
     with the assumption basis stated in one line.
   - **Route comparison table**: in-house/direct (typical 4–6 month senior
     comms time-to-hire, internal time cost, mishire risk with no
     guarantee) vs retained search (6-week milestone methodology from
     `tool/pitch_pack.py`, replacement guarantee, fee as % of salary).
   - **Net position**: vacancy cost avoided minus fee, stated conservatively.
   - **Why now**: one paragraph tied to the verified live trigger.
   - **Risk of waiting**: what the next quarter of an empty seat costs,
     using the same house numbers.
   - Footer: "All assumptions shown; figures are planning estimates, not
     quotes."

5. **Report back in chat**: the net-position number, the lead trigger you
   anchored on, and anything that weakens the case (say so honestly —
   a weak case sent anyway burns the relationship).

## Guardrails

- Every figure traces to the house calculators or a cited public source;
  label every assumption. Never fabricate a salary, a timeline or a cost.
- Conservative framing throughout — this document will be challenged by a
  sceptical CFO; one inflated number kills all of them.
- UK spelling. No em dashes in the memo body (house style).
- Do not email anything; the memo is for the Account Director to hand over.
