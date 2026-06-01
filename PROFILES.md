# Profiles — one engine, many specialisms

The scour → filter → rank → render → deliver engine is **specialism-agnostic**.
Everything that makes it recruit for a particular field — the role taxonomy,
salary floor, competitor excludes, and delivery identity — lives in a
**profile** under `tool/profiles/`. Adding a new specialism (e.g. Marketing)
is a new profile, **not** a fork of the code.

## How it works

- `tool/profiles/base.py` — the `Profile` dataclass (the set of tunable fields).
- `tool/profiles/comms.py` — the **Comms** profile (today's live settings).
- `tool/profiles/__init__.py` — the registry + `active_profile()` resolver.

The active profile is chosen by the `VMA_PROFILE` environment variable
(default `comms`). `config.py` re-exports the active profile's values under
the same names the rest of the codebase already imports, so **switching
profile changes behaviour without touching any other module**:

```bash
VMA_PROFILE=comms      python -m tool.morning_brief    # today's behaviour
VMA_PROFILE=marketing  python -m tool.morning_brief    # once marketing is added
```

An unknown or empty `VMA_PROFILE` falls back to `comms` rather than raising,
so a typo never breaks a run.

## What is (and isn't) in a profile

**In the profile** (specialism-specific): role keywords, title excludes,
lower-seniority job titles, job-search queries, salary floor, company
excludes, and the brief's recipient / test inbox.

**Still in `config.py`** (infrastructure, identical for every specialism):
source URLs, ATS seeds, the dedup aggregator lists, geography weighting,
user-agent, the sweep window, and API-key wiring.

## State isolation (per profile)

Each profile keeps its runtime state in its own directory so two profiles
never read or overwrite each other's data. `tool/state_paths.state_root()`
resolves it:

- **comms** (default) → the legacy root `tool/state/` — Sara's tool is
  completely unaffected.
- **any other profile** → `tool/state/<key>/`.

A process serves one profile, chosen by `VMA_PROFILE`. An *unregistered*
`VMA_PROFILE` falls back to comms (so a typo never spins up an orphan state
dir); namespacing kicks in the moment the profile is registered.

## The landing chooser

`/` is the front door — a tile per **live** profile plus a "coming soon"
tile for each entry in `UPCOMING_PROFILES`. Today **Comms** and **Marketing**
are both live. The comms landing lives at `/comms`; `/dashboard` is unchanged
(Sara's bookmark still works).

Each tile links to that desk's dashboard. A process serves its **own** desk
locally; **sibling** desks are linked via `VMA_PROFILE_URLS` (a JSON map of
`{key: absolute_url}`), since each profile runs as its own instance.

## Two desks, one codebase (deployment)

`render.yaml` defines two free web services off this same repo: `vma-dashboard`
(`VMA_PROFILE=comms`) and `vma-marketing-dashboard` (`VMA_PROFILE=marketing`).
Same code, different profile → different taxonomy and a separate state
namespace (`tool/state/` vs `tool/state/marketing/`). Set each service's
`VMA_PROFILE_URLS` to the other's URL so the chooser cross-links them.

Every state-writing module resolves its directory through `state_root()`, and
`github_state` namespaces the persisted dashboard-state paths the same way, so
the two desks' working data (leads, triage, predictors, candidate watch,
dedup, calendar pipeline …) never collide. The **account universe** is shared
on purpose — `hiring_contacts.json`, the Companies House watchlist and the
LinkedIn-resolver cache stay at the comms root so both desks reuse one set of
target companies/contacts (and one Bright Data budget).

## Nightly runs

`.github/workflows/morning-brief.yml` runs the comms brief; a separate
`.github/workflows/marketing-brief.yml` runs the marketing brief
(`VMA_PROFILE=marketing`, 15 min later) into the marketing namespace. Kept as
two files so the marketing run can never affect Sara's live comms job. Both
emails are off (dashboard is the surface); the marketing job never commits the
shared contacts.

## Email

The daily morning-brief email is **off by default**
(`config.MORNING_BRIEF_EMAIL_ENABLED`): the brief still scours, ranks and
refreshes the dashboard every run, it just emails no one. The dashboard is the
surface. Set `MORNING_BRIEF_EMAIL_ENABLED=1` to resume delivery.

## Detector tuning (Phase 3)

Beyond job titles, the *signal detectors* are now profile-aware too, so
Marketing's non-job intelligence is marketing-tuned rather than comms. Each
detector keeps the live comms values untouched and adds a first-draft
marketing variant, selected by the active profile:

| Detector | What it tunes | File |
|---|---|---|
| Vacated-seat / senior-move titles | which senior departures count | `tool/cascade.py` |
| Move-detection regex | "X joins/leaves" role pattern | `tool/following.py` |
| Companies House officer classifier | which officer titles flag a leader change | `tool/sources/companies_house.py` |
| Sector-heat weights | which sectors rank hotter | `tool/peers.py` |
| Calendar pulses | the knowable placement windows | `tool/calendar_pulses.py` |

These marketing values live next to their detector (as `_MARKETING_*`
constants) rather than in `marketing.py`, because they mirror comms data that
also lives in those modules. They're all marked **FIRST DRAFT** for review.

**Still comms-only for now** (next, "Phase 3b"): the trade-press warm-call
feeds, the contact role-routing (CCO → CMO), and the framework-discovery
keywords. (Full state-namespace isolation is now done; a marketing-specific
company watchlist is intentionally left shared for now.)

## ⚠ Marketing is a first draft

`tool/profiles/marketing.py` is seeded from general marketing-recruitment
knowledge so the desk works today. Its job titles, search queries, competitor
excludes and (later) target companies / trade press are the things to review
with the marketing team and tune — editing that one file re-tunes the whole
Marketing desk.

## Adding the Marketing profile (later phases)

1. Create `tool/profiles/marketing.py` with a `Profile(key="marketing", …)`
   carrying marketing job titles, search queries, target watchlist,
   competitor excludes and recipient.
2. Register it in `tool/profiles/__init__.py` (one line in `_REGISTRY`).

That single registration is all it takes for `VMA_PROFILE=marketing` — and,
once Phase 1 lands, the **"Marketing" door on the landing page** — to light
up. Profile fields will grow over later phases as the remaining
comms-specific pieces (sector weights, role-title regexes, trade-press feed
selection, contact routing, calendar windows) migrate in alongside their
marketing values.
