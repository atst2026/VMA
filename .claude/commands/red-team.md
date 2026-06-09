---
description: Conviction pass on the board's top leads — build the six-question business case per lead, have a sceptical Red-Team AD try to kill it, verify every claim, and write the typed verdict the gate and cards consume
argument-hint: "[N or company]  — default: every Call-ready lead + top Developing, max 5"
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch, Agent
---

Run the red-team conviction pass: $ARGUMENTS

## Why this command exists (read before acting)

The gate answers "is this true and will they pay?". This pass answers the
question an Account Director actually asks: **"would I stake an hour of
my day on this?"** Each lead gets a written business case, then a
sceptical critic tries to kill it, then a verifier checks every factual
claim. Survivors reach the board marked RED-TEAMED with the case and a
warm opening attached; failures are killed with the reason on record.
Volume down, conviction up — a wrong "confirmed" costs more than ten
"rechecks", and "insufficient evidence" is a finding, not a failure.

## Targets

If $1 is a company, run on that lead only. Otherwise select up to 5:
```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -c "
from tool import predictor_pipeline as PP, lead_engine as LE, gate, propensity, investigations
from tool.profiles import active_profile
invs = investigations.get_all()
rows = []
for e in PP.all_predictors():
    if (e.get('status') or 'active') != 'active': continue
    propensity.annotate(e)
    lead = LE.score_lead(e, 'predictor', active_profile().key)
    g = gate.assess(e, lead, investigation=invs.get(e.get('pid')))
    s = gate.strength_score(lead, g, e)
    t = gate.tier_for(lead, g, s)
    if t in ('ready','dev') and not (invs.get(e.get('pid')) or {}).get('red_team'):
        rows.append((t!='ready', -s, e['pid'], e.get('company'), t, s))
rows.sort()
for _,_, pid, co, t, s in rows[:5]:
    print(f'{pid:32s} {co or \"\":30s} tier={t} score={s}')
"
```
Run the per-lead work below **in parallel sub-agents** (one per lead)
where possible; each returns only its typed verdict.

## Per lead — three roles, in order

**1. The Worker builds the case.** Start from memory: the dossier
(`tool/state/dossiers/<pid>.md`) and the entry's events. Then answer the
six AD questions, verifying live on the web, citing every fact kept:
1. Is the seat genuinely open or about to be? (Companies House officers,
   RNS, team page vs its Wayback archive, the company's own careers board.)
2. Does budget plausibly exist? (Listed/funded/profitable vs cutting;
   a fine's size relative to revenue; the fee-propensity line.)
3. Who is the economic buyer — named person, current title, verified in
   seat? (`tool/state/hiring_contacts.json` first, then public record.)
4. Is there a champion path? (Check `tool/state/propensity_seeds.json`
   notes and dossier history. If none: write "none known — cold open".
   Never invent relationships.)
5. What is the trigger-to-hire causal chain FOR THIS COMPANY — not the
   abstract rule, the specific dated story?
6. Draft: a business case (≤120 words, every clause evidenced) and a
   warm opening (1–2 sentences the AD could say verbatim).

**2. The Red-Team AD tries to kill it.** Adopt the persona fully: an
elite, sceptical AD who has rejected thousands of leads and is judged
only on kills. Attack the case: *"This is still a cold call because…"*,
*"The innocent explanation is routine retirement, not a rebuild"*,
*"You have not named who signs."* Write the kill reasons. The case is
CALL-READY only if it survives with a live-or-imminent seat, plausible
budget, a named economic buyer, an honest champion-path statement, and
an opening that references something specific and verified.

**3. The Verifier checks the survivor.** Re-check each factual claim in
the business case and opening against its cited source. Any claim that
fails verification is removed; if the case no longer stands, the verdict
drops to recheck. No unverified fact ever reaches a card.

## Record the verdict (one per lead — this is what the gate reads)

```bash
python3 -c "
from tool import investigations, dossier
investigations.write_overlay(
    '<pid>', '<confirmed|recheck|killed>',
    note='<one line>', recheck_days=<int or None>,
    red_team=True, conviction=<0-100>,
    business_case='''<the verified case>''',
    warm_opening='''<the opening>''',
    economic_buyer='<Name, Title>',
    champion_path='<path or: none known - cold open>',
    kill_reasons=['<reason>', ...])
dossier.append_note('<pid>', '''<full reasoning: case, attacks,
what survived verification, the weakest link>''')
"
```
Verdict mapping: survives = `confirmed` (presents at High, card shows the
case) · plausible but unproven = `recheck` (stays queued with a date) ·
killed = `killed` (never presents; reasons shown). Conviction is your
calibrated 0–100 — be honest, not generous; overlays expire in 21 days.

## Report back in chat

One line per lead: company — verdict — conviction — the single
strongest fact — the weakest link. Then the day's headline: which leads
an AD should actually call this morning, in order.

## Guardrails

- Free public sources only; every kept claim cites a source.
- Reward abstention: thin evidence → recheck, never a confident guess.
- Never invent names, relationships, dates or figures. "Could not
  verify" goes in the dossier note, not on the card.
- UK spelling. The business case must read like an AD wrote it — no
  hedging boilerplate, no AI voice.
