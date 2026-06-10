# Communications Radar v2 — shared design brief

Ten standalone interactive mockups reimagining the **Communications Radar**
page (the BD-leads console in `tool/dashboard.py`, deployed at
vma-dashboard.onrender.com). Audience: **an account director (Sara) deciding
who to call this morning.** The page must make chasing a lead feel
irresistible — and never overwhelming.

## The product idea each mockup must express

1. **Minimalist at rest.** The collapsed view shows only what's needed to
   *choose*: company, predicted seat, one-line hook, lead-strength score,
   window. Nothing else. Generous whitespace. No data dumps.
2. **Complete on demand.** Interacting with a lead reveals the **call file**
   — the full BD portfolio, exactly what an AD needs before dialling:
   - **Why now** — the demand thesis (`whyNow`), with the `FEE EVENT` badge
     + tooltip where present, and the Q4 budget-flush badge where present.
   - **Who to call** — economic buyer (`buyer`) + warm path (`champion`).
   - **The opening** — what to say in the first 20 seconds (`opening`), plus
     a **Draft opener** action that types out `opener` with a streaming/
     typewriter effect (the AI moment).
   - **What kills it** — the honest kill criteria (`kill`).
   - **Proof** — verification tag (`ver`: reg → "Registry-attested",
     multi → "2+ sources", single → "Single source"), red-team chip when
     `rt` (`RED-TEAMED ✓ {conviction}`), confidence, and the source chips
     (`stack`: label + domain + age in days).
   - **The numbers** — score (0–100), the four qualification dimensions
     (Seat / Budget / Urgency / Buyer, each 0–2 → ✗ / ✓ / ✓✓ with the
     `*_why` as tooltip or microcopy, plus `total`/8), fee propensity
     (`prop` + `propWhy`), window.
   - **Business case** (`bizCase`) where present.
   - **Actions**: `Generate pitch` (toast: "Pitch pack queued for {co} —
     a few minutes"), `Draft opener` (typewriter), `View sources` (toast),
     `✓ Followed up` and `✕ Dismiss` (row animates out / state changes,
     with a toast + undo where natural).
3. **Honest tiers.** `tier` = ready / dev / early / blocked. Non-ready leads
   show `gateWhy` ("Why not call-ready") **instead of** buyer/opening/kill —
   they have no call file yet. Blocked (`conflict`) must read as *do not
   call*. Concepts may de-emphasise, group, fold away or tuck non-ready
   leads — but they must be reachable (drawer, tab, dimmed section…).
4. **AI-native voice.** The intelligence should feel alive: the morning
   synthesis line (`meta.synthesis`), typed/streamed text, "why this ranks
   #1" explainers, a suggested call order (`meta.planOrder`), conviction
   verdicts. Brand the intelligence as **VMA Intelligence** with a spark ✦
   glyph. Never expose internal jargon (`tier:"dev"` → say "Developing").

## Brand & craft

- **Fonts:** `Inter` (UI) + `JetBrains Mono` (scores, labels, timestamps)
  via Google Fonts `<link>`, with system fallbacks. (Concept 09 may add a
  serif — Fraunces — for its editorial voice.)
- **Light palette (default):** bg `#F7F9FC`/white; ink `#101626`; secondary
  `#3C4043`; muted `#5A6577`; dim `#9AA0A6`; hairline `rgba(16,22,38,.08)`;
  VMA slate `#3E5C84`; deep blue `#1A3D7C`; accent blue `#4285F4`; wash
  `#E8F0FE`; clay `#D97757` (the spark/accent); green `#1E7A41`/`#34A853`;
  amber `#B45309`/`#D97A2B`.
- **Dark "slick-AI" palette (concepts 02 & 10):** bg `#0A0F1E`→`#0B1220`,
  glass `rgba(255,255,255,.06)` + `backdrop-filter`, glows from `#4285F4` /
  `#7EA8CC` / `#D97757`, text `#E8EDF6` / `#8A94A6`.
- Subtle halo gradients (radial/conic, very low alpha) are on-brand.
- Motion: 150–350ms ease transitions; expand/collapse must animate; one
  tasteful ambient animation max (radar sweep, halo drift, pulse on NEW).
- Score colouring: ≥70 green, 45–69 amber, <45 grey.
- Trigger-type tints (from live console): Leadership/lead `#1d4ed8` on
  `#e9effb` · Funding `#1e7a41` on `#e7f3ec` · Restructure `#46556e` on
  `#edf0f4` · warning-class `#b5530e` on `#fdecdb` · M&A `#6b3fb5` on
  `#efe9fb` · people `#0e7c74` on `#ddf3f0`.
- Header: minimal — `VMA` (800) `GROUP` (letter-spaced 300) wordmark,
  "Communications Radar", `meta.dateLabel`, and the scan stat
  ("247 signals · 38 sources · scanned 06:40"). Concepts may restyle but
  keep the substance.

## Hard requirements

- One **self-contained** HTML file. Vanilla JS only, no libraries, no build.
  Must work from `file://`. Google-Fonts link allowed (graceful fallback).
- Inline the dataset from `_shared/data.js` **verbatim** as `const RADAR`.
  Render everything from it — no hardcoded lead markup.
- Perfect at **1440×900**; degrade gracefully down to 1100px. No horizontal
  scrollbars, no overflowing text, no overlapping elements.
- Zero console errors. All interactive elements: `cursor:pointer`, hover
  state, and visible keyboard focus.
- QA hooks: every collapsed lead's primary trigger element carries
  `data-qa="lead-trigger"`; the expanded call-file container carries
  `data-qa="portfolio"`.
- A toast component for action feedback (dark pill, bottom-centre).
- `<title>VMA — Communications Radar · {Concept Name}</title>` and a tiny
  corner label (e.g. footer, 10px mono, dim) naming the concept.

## What *not* to do

- No fake browser chrome, no lorem ipsum, no stock-photo vibes, no emoji
  as icons (inline SVG only — stroke 1.7–2, round caps).
- Don't show every field at rest. Restraint is the brief.
- Don't invent data; everything renders from `RADAR`.
- No em-dash-riddled cramped labels; copy stays British and calm.
