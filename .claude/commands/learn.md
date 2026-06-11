---
description: Outcome-learning pass — study what the AD actually followed up vs dismissed, find the patterns, and propose rubric/weight/kill-criteria changes as a reviewable diff
argument-hint: "[days back, default 30]"
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

Run the outcome-learning pass over the last ${1:-30} days.

## Why this command exists (read before acting)

The AD's triage decisions are the platform's ground truth: *followed up*
≈ accepted, *dismissed/removed* ≈ rejected. This pass mines those
decisions against each lead's features and proposes changes that make
tomorrow's board sharper — as a **reviewable diff, never a silent
edit**. This is how the engine compounds instead of staying static.

## Step 1 — Assemble the evidence

```bash
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -c "
import json
from collections import Counter
from tool import predictor_pipeline as PP, bd_tombstone
from tool.state_paths import state_dir
from pathlib import Path
sd = Path(str(state_dir()))
status = json.loads((sd/'predictor_status.json').read_text()) if (sd/'predictor_status.json').exists() else {}
tombs = bd_tombstone.get_all() if hasattr(bd_tombstone,'get_all') else {}
idx = json.loads((sd/'dossiers/_index.json').read_text()) if (sd/'dossiers/_index.json').exists() else {'companies':{}}
acc, rej = Counter(), Counter()
for e in PP.all_predictors():
    pid = e.get('pid'); st = status.get(pid) or e.get('status') or 'active'
    keys = tuple(sorted({ev.get('trigger_key') for ev in e.get('events') or [] if isinstance(ev,dict)}))
    if st == 'followed_up':
        for k in keys: acc[k] += 1
    elif st == 'dismissed' or pid in (tombs or {}):
        for k in keys: rej[k] += 1
print('ACCEPTED by trigger:', dict(acc))
print('REJECTED by trigger:', dict(rej))
print('dossier companies on file:', len(idx.get('companies',{})))
"
```
Then pull the CALL-OUTCOME ladder — the strongest evidence in the
system, because it records what actually happened on the phone
(no_answer / wrong_buyer / conversation / meeting / brief / placement),
each stamped with the engine snapshot that produced the call:
```bash
python3 -c "import json; from tool import lead_outcomes;
print(json.dumps(lead_outcomes.outcome_report(), indent=1))"
```
Read it as calibration questions: do 70+ scores convert more than
45-69s (if not, the weights are mis-set — find which component lies)?
Do 'Proven agency user' leads out-convert 'Unknown' by enough to
justify the 15-point gap? Is any tier producing wrong_buyer clusters
(the buyer-mapping table is wrong for that trigger)? Propose weight
changes only where n≥5 per cell.

Also read, for the window: investigation overlays
(`tool/state/investigations/*.json` — which red-team verdicts did the AD
agree with?), the dossier index gate histories (which queue reasons keep
recurring?), and `tool/state/verdict_log.json` if present.

## Step 2 — Find the patterns (be statistically honest)

For each trigger key, source family, fee-propensity class and tier:
acceptance rate with the sample size beside it. **A pattern needs n≥5
and a clear margin before it justifies a change** — with less data,
report it as "watch, insufficient sample". Look specifically for:
- trigger keys that are consistently dismissed (candidates for weight
  cuts, the bronze set, or amplifier-only status)
- queue reasons that keep recurring on leads later followed up (the gate
  may be too strict there)
- red-team kills the AD overrode, or confirmations the AD dismissed
  (the critic's standards need adjusting)

## Step 3 — Propose changes as a diff (never apply silently)

Map each supported finding to its lever:
- trigger weights → `tool/predictive/patterns.py`
- bronze / amplifier sets, propensity points, evidence thresholds,
  cap → `tool/gate.py` constants
- kill-condition wording → `gate._KILL`
- engine taxonomy points → `tool/lead_engine.py` tables

Present: the finding (with numbers), the exact proposed edit, and the
expected effect on the board. **Wait for explicit approval in the chat**;
on approval apply the edits, run `python3 -m pytest tests/ -q` (all
green or revert), update any test constants the change legitimately
moves, commit with the findings in the message, push, and open a PR.

## Guardrails

- Never lower the truth gate (3-family evidence rule) based on
  acceptance alone — popularity does not verify facts.
- One change-set per run; small steps, measurable next month.
- Record the run's findings in `tool/state/learning_log.md` (append,
  dated) so successive passes can see what was already tried.
- UK spelling; numbers with sample sizes, always.
