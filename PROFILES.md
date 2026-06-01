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
tile for each entry in `UPCOMING_PROFILES`. Today: **Comms** (live, → the
existing dashboard) and **Marketing** (coming soon). The comms landing now
lives at `/comms`; `/dashboard` is unchanged (Sara's bookmark still works).
When the marketing profile is registered, its door goes live automatically —
no template change.

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
