#!/usr/bin/env python3
"""Build a PDF mockup of the live dossier card, filled with the
API-funded 'dream dossier' content (illustrative). Renders with weasyprint
so it matches the dashboard's own CSS language."""
from weasyprint import HTML

OUT = "/home/user/VMA/docs/dream_dossier_example_kingfisher.pdf"

CSS = """
@page { size: A4 landscape; margin: 12mm 12mm 14mm 12mm; }
* { box-sizing: border-box; }
body { font-family: Helvetica, Arial, "DejaVu Sans", sans-serif;
       color: #2b313d; margin: 0; font-size: 11px; }
.mono { font-family: "DejaVu Sans Mono", "Courier New", monospace; }

.card { background: #fff; border: 1px solid #e6e8ec; border-radius: 14px;
        overflow: hidden; }

/* ---- header ---- */
.head { display: flex; align-items: center; gap: 14px; padding: 16px 26px 14px; }
.logo { width: 40px; height: 40px; flex: none; border-radius: 10px;
        background: #1a2750; color: #fff; display: flex; flex-direction: column;
        align-items: center; justify-content: center; line-height: 1; }
.logo b { font-size: 12px; font-weight: 800; letter-spacing: .02em; }
.logo span { font-size: 5px; letter-spacing: .26em; margin-top: 2px; }
.htitle .eyebrow { font: 700 8px "DejaVu Sans Mono", monospace;
        letter-spacing: .2em; color: #9aa0a6; }
.htitle .co { font-size: 19px; font-weight: 800; color: #0c1326; margin-top: 2px; }
.htitle .sub { font-size: 9.5px; color: #8a909c; margin-top: 1px; }
.legend { margin-left: auto; text-align: right; font-size: 8.5px; color: #6b7280;
        max-width: 230px; line-height: 1.5; }
.legend .star { color: #5B459E; font-weight: 800; }

.disc { margin: 0 26px 10px; padding: 7px 12px; border-radius: 8px;
        background: #fbf8ff; border: 1px solid #ece3fb; color: #6a5b8c;
        font-size: 8.7px; line-height: 1.5; }

/* ---- call band ---- */
.callband { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
        padding: 12px 26px; border-top: 1px solid #edeef1;
        border-bottom: 1px solid #edeef1; background: #f8fbf9; }
.cbv { font: 800 10px "DejaVu Sans Mono", monospace; letter-spacing: .12em;
        padding: 5px 12px; border-radius: 999px; color: #1e7a41;
        background: #e7f3ec; border: 1px solid #bfe3cd; flex: none; }
.cbw { color: #3a4150; flex: 1; min-width: 220px; font-size: 11px; }
.cbd { margin-left: auto; font: 700 9.5px "DejaVu Sans Mono", monospace;
        color: #46556e; background: #edf0f4; border: 1px solid #dfe3ea;
        border-radius: 999px; padding: 5px 12px; flex: none; }

/* ---- rows ---- */
.prow { display: grid; grid-template-columns: 150px 1fr; gap: 16px;
        padding: 12px 26px; border-top: 1px solid #eef0f2;
        page-break-inside: avoid; }
.prow.alt { background: #f7f8fa; }
.pl2 { font: 700 8px "DejaVu Sans Mono", monospace; letter-spacing: .17em;
        color: #9aa0a6; padding-top: 2px; }
.ai { display: inline-block; margin-top: 6px; font: 700 6.5px "DejaVu Sans Mono", monospace;
        letter-spacing: .1em; color: #5B459E; background: #f1ebfd;
        border: 1px solid #e2d6fb; border-radius: 999px; padding: 2px 6px; }
.pv { font-size: 11px; line-height: 1.62; color: #3a4150; min-width: 0; }
.pv b { color: #0c1326; font-weight: 700; }
.pv .muted { color: #8a909c; }

.src { font: 600 8.5px "DejaVu Sans Mono", monospace; color: #8a909c;
        margin-top: 4px; }
.tag { font: 600 7.5px "DejaVu Sans Mono", monospace; color: #b08a3a;
        margin-left: 6px; }

/* chips */
.chip { display: inline-block; font: 700 7.5px "DejaVu Sans Mono", monospace;
        letter-spacing: .04em; padding: 2px 8px; border-radius: 999px;
        white-space: nowrap; margin-right: 5px; }
.c-good { color: #1e7a41; background: #e7f3ec; border: 1px solid #bfe3cd; }
.c-mid  { color: #B45309; background: #fff7ed; border: 1px solid #fdd9a8; }
.c-grey { color: #46556e; background: #edf0f4; border: 1px solid #dfe3ea; }
.c-clay { color: #b5530e; background: #fdecdb; border: 1px solid #f3cda0; }
.svc-search { color: #1D5FA8; background: #e8f0fc; border: 1px solid #c4dafb; }
.svc-bench  { color: #5B459E; background: #f1ebfd; border: 1px solid #ddd0f7; }
.svc-interim{ color: #0E7C74; background: #ddf3f0; border: 1px solid #b6e6e0; }
.svc-adv    { color: #9A6A14; background: #fbf1dd; border: 1px solid #f0dcb0; }

/* needs / lists */
.need { display: grid; grid-template-columns: 92px 1fr; gap: 9px;
        margin-bottom: 7px; align-items: baseline; }
.bul { position: relative; padding-left: 13px; margin-bottom: 5px; line-height: 1.55; }
.bul::before { content: "\\2022"; position: absolute; left: 2px; color: #b5530e; }
.strat { display: grid; grid-template-columns: 74px 1fr; gap: 10px;
        margin-bottom: 5px; line-height: 1.5; }
.strat .sk { font: 700 7px "DejaVu Sans Mono", monospace; letter-spacing: .12em;
        color: #9aa0a6; padding-top: 2px; }

.contact { margin-bottom: 6px; }
.contact .nm { font-weight: 800; color: #0c1326; font-size: 12px; }
.hook { font-style: italic; color: #1a2235; background: #f6f4fb;
        border-left: 2px solid #5B459E; border-radius: 0 8px 8px 0;
        padding: 9px 12px; line-height: 1.6; }
.foot { padding: 12px 26px 16px; font-size: 8.3px; color: #9aa0a6;
        line-height: 1.55; border-top: 1px solid #eef0f2; }
.foot b { color: #6b7280; }
"""


def prow(label, content, alt=False, ai=False):
    cls = "prow alt" if alt else "prow"
    badge = '<div class="ai">✦ AI-RESEARCHED</div>' if ai else ""
    return (f'<div class="{cls}"><div class="pl2">{label}{badge}</div>'
            f'<div class="pv">{content}</div></div>')


rows = []

rows.append(prow("VERIFIED TRIGGER", (
    "The share-allotment filing that flagged this lead is a "
    "<b>routine employee share-scheme vesting — not a capital raise</b> "
    "(checked against the PDMR / AGM record). The free engine's "
    "“capital raise → team build” read is a <b>false positive.</b> "
    "The real, current reason to call: Kingfisher's latest results name "
    "<b>retail media</b> and the <b>marketplace</b> as growth pillars, while "
    "group marketing stays brand-fragmented and the French business is under "
    "restructuring pressure."
    '<div class="src">SOURCE ↗ FY24/25 results &amp; strategy · '
    'Kingfisher plc · Mar 2025<span class="tag">(illustrative)</span></div>'
), ai=True))

rows.append(prow("POINT OF CONTACT", (
    '<span class="chip c-good">DOOR OPEN</span><br>'
    '<div class="contact" style="margin-top:7px"><span class="nm">Helen Marsh</span> '
    '— Group Director of Corporate Affairs &amp; Communications &nbsp;'
    '<span class="chip c-grey">in seat ~3 yrs</span>'
    '<span class="chip c-grey">reports to CEO</span>'
    '<span class="chip c-good">verified email on file ✓</span>'
    '<span class="chip c-grey">LinkedIn ↗</span></div>'
    '<div class="contact"><span class="nm">Daniel Okoro</span> '
    '— Group Marketing Director · owns the retail-media P&amp;L question '
    '<span class="chip c-grey">LinkedIn ↗</span></div>'
    '<span class="tag">(names illustrative — the live engine inserts the real, '
    'current people and a verified email where one exists)</span>'
), alt=True, ai=True))

rows.append(prow("ACCOUNT THESIS", (
    "<b>Kingfisher is becoming a retail-media and marketplace platform with a "
    "marketing function built for stores.</b> The strategy has moved; the org "
    "chart hasn't — and France is absorbing the leadership's attention."
    '<div style="margin-top:6px" class="muted">Corporate Affairs sits centrally '
    "and reports to the CEO; marketing is run brand-by-brand with a thin group "
    "layer. Retail media is named as a profit pillar but is under-resourced "
    "versus the Tesco / Boots media networks. France (Castorama / Brico Dépôt) "
    "is under cost and management pressure.</div>"
), ai=True))

rows.append(prow("THE HOOK", (
    '<div class="hook">“Most home-improvement retailers we speak to are trying '
    "to stand up a retail-media business with a marketing team built for stores, "
    "not media — and your last results put media front and centre while the "
    "structure hasn't moved. We mapped how a named retail peer resourced exactly "
    'that shift. Worth a 20-minute compare?”</div>'
), alt=True, ai=True))

rows.append(prow("TALKING POINTS", (
    '<div class="bul">Retail-media commercial leadership is a thin, passive talent '
    "market — in-house TA rarely reaches it.</div>"
    '<div class="bul">The group marketing layer is built for store brands, not a '
    "media P&amp;L — a benchmarking question, not a pitch.</div>"
    '<div class="bul">France change-comms can be covered interim-first — no '
    "permanent headcount sign-off needed.</div>"
), ai=True))

rows.append(prow("GENUINE NEEDS", (
    '<div class="need"><span class="chip svc-search">SEARCH</span>'
    "<span><b>Retail-media commercial &amp; marketing leader</b> to scale the media "
    'network into a real profit line. <span class="muted">— results name it a '
    "current-year priority; no senior owner visible. (high)</span></span></div>"
    '<div class="need"><span class="chip svc-bench">BENCHMARKING</span>'
    "<span><b>Group marketing / comms operating-model review</b> — brand-"
    'fragmented vs. a group-platform strategy. <span class="muted">— brand-level '
    "leadership pages show no unified group layer. (medium)</span></span></div>"
    '<div class="need"><span class="chip svc-interim">INTERIM</span>'
    "<span><b>France restructuring change &amp; stakeholder comms.</b> "
    '<span class="muted">— France weakness + management change flagged in '
    "results. (medium)</span></span></div>"
), alt=True, ai=True))

rows.append(prow("SECTOR INSIGHT", (
    '<div class="bul">Live in your peer set: WH Smith (profit warning, Jun); '
    "Unilever (activist stake, May) — 25 senior-team triggers across the group "
    "in the last 90 days.</div>"
    '<div class="bul">Cost-of-living pricing politics and supplier disputes keep '
    "retailers in near-permanent reputation management.</div>"
    '<div class="bul">Store-estate restructuring and automation drive heavy '
    "internal-change communications demand.</div>"
)))

rows.append(prow("WINDOW", (
    "<b>Now</b> — the retail-media leadership gap is live and unfilled; the "
    "conversation doesn't wait for a vacancy to be posted. &nbsp;"
    '<span class="chip c-clay">Live demand</span>'
)))

rows.append(prow("FEE AT STAKE", (
    "<b>Retained search</b> (retail-media commercial leader, ~£90–120k base) "
    "<b>+ a benchmarking project</b> ≈ <b>£45k–£70k</b> — plus an "
    "advisory engagement that opens the account <i>before</i> any placement. "
    '<span class="muted">Confidence: High (primary-source this quarter).</span>'
)))

rows.append(prow("IF YOU LAND THE MEETING", (
    '<span class="muted">From first contact, the AD takes over — this is held in '
    "reserve, not the headline. On file for the AD: three diagnostic opening "
    "questions, and the two most-likely objections with honest counters "
    "(“we recruit ourselves”; “budget's tight after France”).</span>"
), alt=True, ai=True))

rows.append(prow("SOURCES", (
    '<span class="mono" style="font-size:9px;color:#5a6270">'
    "Kingfisher FY24/25 results &amp; strategy ↗ &nbsp;·&nbsp; "
    "group leadership page ↗ &nbsp;·&nbsp; PRWeek / Campaign moves ↗ "
    "&nbsp;·&nbsp; Companies House PDMR / AGM filings ↗</span>"
    '<span class="tag">(illustrative source set)</span>'
), ai=True))

HTML_DOC = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{CSS}</style></head><body>
<div class="card">
  <div class="head">
    <div class="logo"><b>VMA</b><span>GROUP</span></div>
    <div class="htitle">
      <div class="eyebrow">BUSINESS LEAD DOSSIER · AI-RESEARCHED EXAMPLE</div>
      <div class="co">Kingfisher plc</div>
      <div class="sub">B&amp;Q · Screwfix · Castorama · Brico Dépôt · TradePoint</div>
    </div>
    <div class="legend"><span class="star">✦</span> marks every section produced by
      the AI research pass — i.e. exactly what the Anthropic&nbsp;API funds. The rest
      runs free.</div>
  </div>
  <div class="disc"><b>Illustrative example</b> of the AI-researched dossier the API
    funds, shown against the same Kingfisher lead currently on the live board. Names,
    quotes and figures are illustrative; the live engine generates every line from
    real-time web research and cites a real, dated source for each.</div>
  <div class="callband">
    <span class="cbv">CALL: YES · ✦ VERIFIED</span>
    <span class="cbw">Trigger independently confirmed · one named route in · a
      specific, evidenced reason to call this week.</span>
    <span class="cbd">Retained search + advisory · £45k–£70k</span>
  </div>
  {''.join(rows)}
  <div class="foot"><b>For funding evaluation only.</b> This reconstructs the
    output the Anthropic-funded intelligence engine produces (account thesis,
    verified trigger, named contact, meeting hook) for a single lead. The live
    engine produces every named person, quote, figure and URL from real-time
    research and cites the dated source for each — do not treat the specific
    evidence above as verified. © VMA Group.</div>
</div>
</body></html>"""

HTML(string=HTML_DOC).write_pdf(OUT)
import os
print("wrote", OUT, os.path.getsize(OUT), "bytes")
