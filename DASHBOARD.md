# VMA Dashboard — developer guide

A single-file Flask app that renders the internal BD/recruitment dashboard.

## Run it

```bash
PYTHONPATH=. python3 -m tool.dashboard
# → http://localhost:8765   (auth gate is OFF when run locally)
```

Deployed copy: `vma-dashboard.onrender.com` (redeploys from `main`).

## Architecture

Everything user-facing lives in **`tool/dashboard.py`**:

- A Flask app whose main route `GET /dashboard` (`index()`) renders one big
  embedded HTML/CSS/JS string, `TEMPLATE = r"""…"""`, via
  `render_template_string(TEMPLATE, …)`.
- `index()` builds the server-rendered context: `leads`, `predictors` +
  `funding_events` interleaved into `premarket_rows`, `framework_events`, and
  the various `*_count` filter tallies.
- Everything dynamic after first paint is fetched by inline JS from `/api/*`
  endpoints (also defined in `dashboard.py`).

### The three pages (one slim left icon-rail switches them, CSS show/hide)

1. **Market Intelligence Radar** (`#leads`, default) — two panels:
   *Live Jobs* (leads) and *BD Leads* (`premarket_rows` = predictors +
   funding). Filter pills (Active / New today / Followed up / Dismissed / All)
   toggle rows client-side by `data-status`. Daily Refresh pulls a fresh brief.
2. **Executive Assistant** (`#agent`) — a Claude-style composer pill. Outline
   chips (Pitch Pack / Reverse Match / Pre-meeting / Sweep) morph the pill into
   the matching form; submitting hits `/api/dispatch/*` and opens the report in
   a popup (the submit arrow is a native `type=submit` so the popup keeps the
   user gesture — do **not** swap it for `requestSubmit()`).
3. **BD Calendar** (`#cal`) — a card menu. Each card opens a modal
   (`#bd-modal`) that **relocates a real panel** out of the hidden host
   `#cal-host` and returns it on close (so the panel keeps its own AJAX
   loader / filters / dismiss). Current cards:
   - **Placement Windows** — window-pane list, loaded from `/api/pulses`.
   - **Events & Networking** — comms awards / conferences, loaded from
     `/api/industry-events`.
   - **Framework Eligibility** — server-rendered from `framework_events`.

## Data sources (the `/api/*` reads pull from these)

- `tool/calendar_pulses.py` — placement-window "pulses" (statutory/regulatory
  hiring windows). Also holds `INDUSTRY_EVENTS` (see note below).
- `tool/framework_status.py` / `framework_watch` — public-sector frameworks.
- `tool/funding_round.py`, `tool/cascade.py`, `tool/predictor_pipeline.py` —
  pre-market signals feeding `premarket_rows`.
- `tool/pulse_dismiss.py` — per-finding dismissals (shared keyspace; the
  `✕` on a row POSTs to `/api/pulses/dismiss`).

## Events & Networking — data note

The "Events & Networking" card serves `INDUSTRY_EVENTS` from
`tool/calendar_pulses.py`. ⚠️ This is a **curated static list**, not a live
feed: each entry's date and source link was verified against the organiser's
own 2026 listing (May 2026), and events without a firm public 2026 date were
omitted rather than guessed. It therefore needs periodic re-verification (or
replacing with a real feed) as organisers publish/update their calendars.

## Conventions / gotchas

- The colour/halo palette matches the landing page (`LANDING_TEMPLATE` in the
  same file); the dashboard `body` carries the verbatim Gemini halo.
- The composer pill is the exact Claude spec (content-box 672px, radius 20,
  dual `0 4px 20px / 0 0 0 .5px` shadow, inner 14 / gap 12, scroll 48→384px).
- JS is global event-delegation + `DOMContentLoaded` AJAX loaders — it is not
  position-dependent, so panels can be relocated (as the BD modal does).
- Runtime state lives under `tool/state/*.json`; don't commit changes to those.
