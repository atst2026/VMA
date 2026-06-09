---
description: MPC spec-marketing — turn one outstanding available candidate into 8–12 demand-creating spec memos for companies with no advertised role
argument-hint: "<candidate name> [current company] [current title]  — e.g. /mpc \"Rebecca Torres\" Vodafone \"Head of Internal Communications\""
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch
---

Run the Most Placeable Candidate (MPC) spec-marketing play for: $ARGUMENTS

## Why this command exists (read before acting)

In a quiet market the constraint is demand, not candidates. The MPC play
inverts the funnel: instead of waiting for a vacancy, we market one
outstanding available candidate to companies that have NO advertised role
but a visible structural reason to want them. A spec placement creates a
fee that did not exist. The deterministic engine supplies recall; your job
is precision, verification and the sellable artifact.

## Steps

1. **Resolve the candidate.** Read `tool/state/candidate_watch.json` and
   find the candidate by name (fuzzy match on $1). Pull their current
   company, title, tenure and any drift/availability signals. If they are
   not on the roster, use the company/title given in $2/$3; if neither is
   available, stop and ask for a three-line profile.

2. **Deterministic target list.** Run the existing reverse-match engine:
   ```bash
   cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && python3 -m tool.reverse_match "<name>" "<current company>" "<current title>"
   ```
   This yields 10–15 ranked targets with HOT/WARM/COLD priority and a
   suggested contact each. Treat it as recall, not truth.

3. **Fable enrichment — this is the value-add.** For each of the top 10
   targets, in parallel where possible:
   - Cross-reference `tool/state/predictor_pipeline.json`,
     `tool/state/latest_signals.json`, `tool/state/latest_funding.json`
     and `tool/state/posting_ledger.json` for the target's live triggers —
     especially the v2 demand triggers (`inhouse_search_failing`,
     `hiring_restart`, `mishire_reversal`): a company already failing to
     fill, or just unfrozen, is the perfect spec-memo recipient.
   - Verify the trigger still holds with a quick web check (the cited
     person is still in seat, the event is real, no newer contradicting
     news). Kill any target whose why-now does not survive verification.
   - Establish the one named buyer (check `tool/state/hiring_contacts.json`
     first; otherwise the trigger's who-to-call title).
   - Drop any target that is a VMA competitor, the candidate's current
     employer, or excluded in `tool/config.py`.

4. **Write the spec-memo pack** to
   `tool/state/outbox/mpc_<candidate-slug>_<YYYY-MM-DD>.md` (create the
   directory if needed). Structure:
   - **Cover sheet**: candidate's ANONYMISED profile (never the name —
     e.g. "a Communications Director currently leading a 12-person team at
     a FTSE-100 insurer"), availability/notice, three proof points, and
     the target list ranked by fee probability.
   - **One page per target** (8–12 targets): named buyer and title; the
     verified why-now (two or three sentences citing the specific signal,
     with source URL); why this candidate maps to it (specific, no
     generic praise); the ask ("worth one conversation before this person
     is placed elsewhere"); suggested first line for the call or email.

5. **Report back in chat**: the top 5 targets in one short paragraph each —
   buyer, why-now, angle — plus anything you killed at verification and why.

## Guardrails

- Public data only. Never invent facts about the candidate or any target;
  every why-now must cite a real collected signal or verified source, or
  the target is dropped.
- Anonymise the candidate everywhere in the artifact.
- UK spelling. No em dashes in the memo body (house style — see
  `tool/pitch_pack.py` sanitiser).
- Do not email anything; the memo pack is for the Account Director to send.
