# Communications Radar v2 — SECOND WAVE art direction (concepts 11–15)

Read `BRIEF.md` first for the product contract (dataset, call-file content,
QA hooks, hard requirements). **This file overrides its visual direction.**

## Why this wave exists

The first ten were rejected by the client as *"just so poorly designed…
do these really look like next-level AI dashboards to you?"* They were
clean-but-flat: white pages, hairline borders, quiet lists. Tasteful SaaS,
not AI-native. The ONE thing the client liked: **the full call-file content,
presented neatly** — keep that content structure exactly, and rebuild
everything around it to look like the most advanced AI product of 2026.

Litmus test for every panel you draw: *if it could appear in a generic admin
template, redesign it.* Think Linear's marketing site, Vercel, Raycast,
Perplexity, Arc, Bloomberg-rebuilt-by-v0 — depth, glow, glass, motion,
data-as-light. Not flat whitespace.

## The non-negotiable craft recipes (use these, concretely)

**1. Backgrounds are layered scenes, never flat colours.** Dark concepts:
base vertical gradient `#05080F → #0B1222`, plus 2–3 huge blurred radial
glows (`radial-gradient(closest-side, rgba(66,133,244,.16), transparent)`
≥900px, one blue top-left, one clay/amber low-right, `filter:blur(60px)` on
their own fixed divs), plus grain (SVG `feTurbulence` data-URI at 3–5%
opacity, `pointer-events:none`), plus a vignette
(`radial-gradient(ellipse at 50% 40%, transparent 55%, rgba(0,0,0,.5))`).
Slow drift on the glow blobs (30–45s alternate).

**2. Glass panels — the exact recipe:**
```css
background: linear-gradient(180deg, rgba(255,255,255,.085), rgba(255,255,255,.035));
border: 1px solid rgba(255,255,255,.10);
border-top-color: rgba(255,255,255,.22);      /* edge-light */
backdrop-filter: blur(22px) saturate(1.3);
border-radius: 18px;
box-shadow: 0 24px 70px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.07);
```
Hover: lift `translateY(-3px)`, border-color brightens, add an outer accent
glow `0 0 0 1px rgba(103,232,249,.25), 0 12px 50px rgba(66,133,244,.25)`.

**3. Data is drawn in light.** Score/conviction gauges = SVG arcs with
gradient strokes (`<linearGradient>` cyan→blue or amber→clay) +
`filter: drop-shadow(0 0 6px rgba(103,232,249,.7))`, track at
`rgba(255,255,255,.07)`, animated `stroke-dashoffset` draw-on. Sparklines,
segment bars, donuts: same treatment. Numerals: JetBrains Mono 700 with
gradient text (`background:linear-gradient(...); -webkit-background-clip:text;
color:transparent`) and a count-up animation on load (rAF, ~900ms, ease-out).

**4. The AI must feel ALIVE.** At least two of: a streaming agent-status
line cycling real activity ("✦ scanning RNS… 247 signals · corroborating
Octopus Energy · red-team pass complete"); reasoning/text that types in;
panels that assemble from shimmer skeletons
(`linear-gradient(110deg, transparent 30%, rgba(255,255,255,.08) 45%,
transparent 60%)` sweeping, 1.4s); a pulsing LIVE dot (3s breathe); citation
chips that pop in as text streams. Motion discipline: entrance staggers
40–80ms, spring `cubic-bezier(.2,.8,.25,1)`, ambient pulses 3–4s. No chaos:
every animation either communicates state or breathes once.

**5. Typography with intent.** Add **Space Grotesk** (500/600/700) via the
Google Fonts link for display headings/numeral labels; Inter for body;
JetBrains Mono for data/labels. Display sizes are confident (40–64px,
tracking -0.02em). On dark: body `#E8EDF6`, secondary `#94A3B8`, never pure
white, never grey mush below `#64748B` for meaningful text.

**6. Dark palette accents** (banded): score-high/positive `#34D399`,
mid `#FBBF24`, cyan signal `#67E8F9`, blue `#60A5FA`/`#4285F4`, clay/ember
`#FF8A65`/`#D97757`, danger `#F87171`. Chips/badges on dark = translucent
fills (`rgba(52,211,153,.12)` + 1px border at .35 alpha + the text colour).

**7. The call file is sacred.** Identical content structure to wave 1 (the
client's words: *"the only thing I liked"*): WHY NOW (+FEE EVENT/Q4 badges)
→ WHO TO CALL (buyer + warm path) → THE OPENING (+ Draft-opener typewriter)
→ WHAT KILLS IT → PROOF (verification chip, RED-TEAMED ✓ conviction,
confidence, source chips with domain+age) → THE NUMBERS (qual ✗/✓/✓✓ + /8,
propensity + why, window, business case) → action row (Generate pitch /
Draft opener / View sources / ✓ Followed up / ✕ Dismiss, with toasts).
Mono uppercase section labels, 13–13.5px/1.65 body. Restyle the chrome to
your concept; do not reinvent the content or its order. Non-ready leads get
the amber "WHY NOT CALL-READY" gate block; blocked = red DO NOT CALL.

## Hard requirements (unchanged from BRIEF.md)

Standalone file, vanilla JS, `const RADAR` inlined verbatim from
`_shared/data.js`, render all leads from data, perfect at 1440×900, zero
console errors, `data-qa="lead-trigger"` / `data-qa="portfolio"` hooks,
toasts, concept corner label, Google Fonts link with fallbacks.
Performance: CSS animations + at most a few small rAF loops (count-ups,
typewriters); no canvas, no WebGL, no libraries.

One more QC note: automated screenshots are taken ~5s after load and after
clicking the first `[data-qa="lead-trigger"]`. Whatever state the page is in
at those moments must look finished and impressive — boot/assembly sequences
must complete or be visually complete by then.
