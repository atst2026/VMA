#!/usr/bin/env python3
"""Branded "VMA GROUP SEARCH PROPOSAL" PDF generator (comms desk).

This is the comms-profile pitch pack: a true, client-facing PDF rendered from
the supplied VMA proposal template, parameterised per company. Compared with
the source template:

  * the example client's logo on the cover is replaced by the TARGET company's
    own logo (fetched at good quality, fitted to the cover), with a clean
    typographic wordmark as the fallback when no logo can be sourced;
  * every place the template named the example client now carries the target
    company name (woven through the body — "present a proposal to <company>",
    the weekly-update timing table, the exclusivity clause, etc.);
  * the cover reads  "For the position of: <predicted seat>" and
    "Date: <generation date>" (no time), and the "Prepared by" line is dropped;
  * the "Your VMA Group Consultant Team" page is dropped.

Everything else (the VMA branding, the "why VMA / our approach / timing / fee /
client appointments" copy and the back page) is faithful to the template.

Rendering is HTML+CSS -> PDF via WeasyPrint, so the per-company text reflows
cleanly. Only the comms profile uses this; marketing keeps tool/pitch_pack.py's
dynamic pack.
"""
from __future__ import annotations

import base64
import datetime as _dt
import html as _html
import logging
from functools import lru_cache
from pathlib import Path

from tool import logo_finder

log = logging.getLogger("pitch_proposal")

_ASSETS = Path(__file__).resolve().parent / "assets"

# Steel-navy sampled from the template cover band / VMA brand.
_BAND = "#385578"
_NAVY = "#24486f"
_INK = "#1f1f1f"

# Page label shown as the running footer on every interior page.
_RUNNING = "VMA GROUP SEARCH PROPOSAL"


# --------------------------------------------------------------------------
# Assets (VMA branding lifted from the template, embedded so the PDF is
# self-contained — no hosted asset, no base_url needed by WeasyPrint).
# --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _asset_data_uri(name: str) -> str:
    raw = (_ASSETS / name).read_bytes()
    ext = name.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


# --------------------------------------------------------------------------
# Company logo resolution lives in tool/logo_finder: it finds the target's
# real logo from its name (official website -> Wikidata/Wikipedia -> domain
# logo services), which is what makes startup accounts like "Geordie AI" or
# "OQC" resolve rather than printing the company name as text. The cover falls
# back to a typographic wordmark only when nothing resolves.
# --------------------------------------------------------------------------
company_logo = logo_finder.find_logo
_img_data_uri = logo_finder.img_data_uri
_normalize_logo = logo_finder.normalize_logo


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------
def _esc(s) -> str:
    return _html.escape(str(s) if s is not None else "", quote=True)


def _generation_date(when: _dt.date | _dt.datetime | None = None) -> str:
    """The pitch-pack generation date, no time — e.g. '5 June 2026'."""
    d = when or _dt.datetime.now()
    return f"{d.day} {d.strftime('%B %Y')}"


def _cover_logo_html(company: str, logo_bytes: bytes | None) -> str:
    """The target company's logo on the cover, or a clean typographic
    wordmark fallback (so the cover is always presentable). The logo is trimmed
    of surrounding padding so it fills the cover box at a sensible size."""
    if logo_bytes:
        logo_bytes = _normalize_logo(logo_bytes)
        return (f'<img class="client-logo" src="{_img_data_uri(logo_bytes)}" '
                f'alt="{_esc(company)}">')
    return f'<div class="client-wordmark">{_esc(company)}</div>'


def _interior(inner: str, *, first: bool = False) -> str:
    """Wrap interior-page content with the VMA mark + running footer."""
    cls = "page interior" + (" first-interior" if first else "")
    return (
        f'<section class="{cls}">'
        f'<img class="vma-mark" src="{_asset_data_uri("vma_square.png")}" alt="VMA Group">'
        f'<div class="page-body">{inner}</div>'
        f'<div class="running-footer">{_RUNNING}</div>'
        f'</section>'
    )


def render_proposal_html(company: str, seat: str,
                         logo_bytes: bytes | None = None,
                         when: _dt.date | _dt.datetime | None = None) -> str:
    """The full multi-page proposal as print-styled HTML (A4)."""
    co = _esc(company)
    seat_disp = _esc(seat or "Head of Communications")
    date_disp = _esc(_generation_date(when))

    # ---- Cover -----------------------------------------------------------
    cover = f"""
    <section class="page cover">
      <div class="cover-band">
        <img class="vma-wordmark" src="{_asset_data_uri('vma_wordmark_white.png')}" alt="VMA Group">
      </div>
      <div class="cover-logo">{_cover_logo_html(company, logo_bytes)}</div>
      <h1 class="cover-title">SEARCH PROPOSAL</h1>
      <div class="cover-sub">STRICTLY PRIVATE &amp; CONFIDENTIAL</div>
      <div class="cover-fields">
        <div class="cf-label">For the position of:</div>
        <div class="cf-value">{seat_disp}</div>
        <div class="cf-label">Date:</div>
        <div class="cf-value">{date_disp}</div>
      </div>
    </section>
    """

    # ---- Why VMA Group? --------------------------------------------------
    why = _interior(f"""
      <h2>WHY VMA GROUP?</h2>
      <p>We are very pleased to have been invited to present a proposal to {co} to provide
      executive search services for the role of {seat_disp}.</p>
      <p>Based on our experience and ongoing work with a number of comparable organisations, we
      believe that we are well placed to support you on this assignment and set out below how we
      would manage a successful search.</p>
      <ul class="bullets">
        <li><strong>Our specialism:</strong> We have a strong track record of supporting clients
        across all sectors with their communications hiring needs. We work with organisations of all
        shapes and sizes, from global listed businesses, to high growth independent start-ups. And
        this provides us with real insight into the type of skills and experience required to fulfil
        communications roles, in all stages of an organisation's life cycle. We'll use this industry
        leading knowledge to effectively match your requirements with the most suitable candidates
        for your vacancy.</li>
        <li><strong>Our long history and reputation of supporting clients with their communications
        talent requirements:</strong> We have been partnering with businesses to build highly
        successful communications functions for over 45 years. Because our search consultants are
        dedicated to the specialist professions, they bring with them a thorough understanding of
        those markets to all assignments. Over half of our consultants have previously worked in
        marketing and communications which brings an additional layer of insight, as well as
        encouraging confidence in our clients and prospective candidates.</li>
        <li><strong>Our international network:</strong> Throughout our 45-year history, we have been
        building a network of senior marketing, business development and communications professionals
        across multiple jurisdictions and are able to support clients by introducing candidates across
        borders.</li>
        <li><strong>Adding true value:</strong> An executive search service from VMA GROUP means the
        engagement of a team of specialists who will work closely with you to meet your expectations
        and ensure that the service we provide is exactly what you need.</li>
        <li><strong>Consultancy advice:</strong> Based on our experience and where required, we will
        work with you to fully scope the role and job description and overcome any specific challenges
        which the search may involve, to ensure only the most appropriate candidate is appointed.</li>
      </ul>
      <p>If you would like to know anything more about VMA GROUP or our proposal for this assignment,
      please contact:</p>
      <img class="signature" src="{_asset_data_uri('vma_signature.png')}" alt="">
      <p class="contact"><strong>Andrew Harvey</strong><br>CEO, VMA GROUP</p>
    """, first=True)

    # ---- Our Approach (part 1) ------------------------------------------
    approach1 = _interior(f"""
      <h2>OUR APPROACH</h2>
      <p>Our years of experience in conducting search and selection has meant that we have developed
      a highly successful approach to these assignments. However, we recognise that each assignment
      has specific requirements and we tailor our approach according to the needs of each situation.
      Typically, our approach involves:</p>
      <h3>A Highly Experienced Team</h3>
      <p>Your dedicated team involves two experienced search consultants and an assistant researcher,
      ensuring we provide sufficient resources to meet the activity commitments of the search in a
      timely manner.</p>
      <h3>Candidate Briefing Pack</h3>
      <p>We will work with you to develop a candidate briefing pack for the specific vacancy, which
      will provide suitable candidates with the relevant information about the role, an overview of
      the organisation, challenges, opportunities and requirements of the position etc. We will ensure
      that the candidate briefing pack promotes the vacancy in the most positive way.</p>
      <h3>Market Mapping</h3>
      <p>We will proactively scope and map the market for candidates which meet the criteria of the
      position, building a long list of individuals. The strongest/most suitable candidates on this
      list will then be approached and considered in detail.</p>
      <h3>Headhunting Activities</h3>
      <p>We will proactively approach individuals who we believe best fit the requirements of the
      role. All suitable candidates will pass through an interview screening process to help compile
      the most relevant long list possible. We will reach out to our large and trusted network to
      ensure we consider all relevant individuals, seeking additional market insight and character
      references where appropriate.</p>
      <h3>Network Approaches</h3>
      <p>We will approach and consider all relevant individuals within our network and we will utilise
      that network to access those individuals who may not be known to us but come with a
      recommendation from their peers.</p>
      <h3>Diversity &amp; Inclusion</h3>
      <p>We pride ourselves on having an extensive network of contacts across our specialisms. As a
      result, candidates for your role will be considered from a wide spectrum of backgrounds and a
      long list will be presented which focuses on those candidates who are most suitable for the
      role, irrespective of background, gender, race, or religion. We also access and engage with
      several ED&amp;I networking groups which enable us to gain recommendations from a range of
      diverse communities.</p>
      <h3>Pre-Screening Video Call Interviews</h3>
      <p>Following the development of a long list, the most relevant candidates will be screened via a
      video call interview. This interview will assess their level of interest in the vacancy, and
      their suitability for the role, including skills, experience and cultural fit.</p>
    """)

    # ---- Our Approach (part 2) ------------------------------------------
    approach2 = _interior("""
      <h3>Face-to-Face Interviewing</h3>
      <p>All potentially relevant candidates will then be interviewed face-to-face against appropriate
      criteria. This will ensure we short list the most suitable candidates, providing a successful
      match with the requirements of the role. Following the completion of face-to-face interviewing,
      VMA GROUP will rate the short-listed candidates in order to help prioritise client-side
      interviews.</p>
      <h3>Long List</h3>
      <p>A long list will be presented for review. This will include a candidate profile with CV and
      interview notes for discussion.</p>
      <h3>Short List</h3>
      <p>A final short list of candidates will be agreed for interview and VMA GROUP will manage all
      interview arrangements and logistics.</p>
      <h3>Psychometric Testing</h3>
      <p>Where relevant, appropriate testing will be arranged as part of the interview process.</p>
      <h3>Referencing</h3>
      <p>When appropriate, reference checks will be made on candidates, including character references
      from known contacts. Bespoke reference requirements will also be managed based on specific
      client requirements.</p>
      <h3>Candidate Management</h3>
      <p>VMA GROUP will manage all candidate relationships throughout the search and interview
      process, and we will ensure all offer negotiations are concluded in a positive, timely and
      professional manner.</p>
    """)

    # ---- Indicative assignment timing -----------------------------------
    def _row(week, action, resp):
        return (f"<tr><td class='wk'>{week}</td><td>{action}</td>"
                f"<td class='resp'>{resp}</td></tr>")

    timing = _interior(f"""
      <h2>INDICATIVE ASSIGNMENT TIMING</h2>
      <p>We have outlined below a suggested timeline proposal. However this timeline can be adapted to
      meet specific client requirements.</p>
      <table class="timing">
        <thead><tr><th>Date</th><th>Action</th><th>Responsibility</th></tr></thead>
        <tbody>
          {_row("Week 1<br><span class='wc'>w/c TBC</span>",
                f"Following the appointment of VMA GROUP as chosen Search Partner - meeting with {co} "
                "stakeholders and hiring team to further discuss the role, requirements, feedback and "
                "search process.<br><br>Candidate briefing pack developed and sign-off agreed.",
                f"VMA GROUP &amp; {co}")}
          {_row("Week 2<br><span class='wc'>w/c TBC</span>",
                "Commence market mapping, candidate search and targeting.<br><br>Approach network, seek "
                f"recommendations and follow up with relevant candidates.<br><br>Weekly progress update "
                f"provided to {co}.",
                "VMA GROUP")}
          {_row("Week 3<br><span class='wc'>w/c TBC</span>",
                "Continue market mapping, network search and recommendation sourcing.<br><br>Supply "
                f"preliminary benchmarking data to {co} on identified candidates, salaries, diversity "
                f"and relevant market feedback.<br><br>Weekly progress update provided to {co}.",
                "VMA GROUP")}
          {_row("Week 4<br><span class='wc'>w/c TBC</span>",
                "Presentation of candidate Long-List for discussion and Short-List agreed for "
                f"interview.<br><br>Weekly update provided to {co}.",
                f"VMA GROUP &amp; {co}")}
          {_row("Week 5<br><span class='wc'>w/c TBC</span>",
                "First stage interviews arranged.<br><br>Agree candidates to progress to second stage / "
                f"final interviews.<br><br>Weekly update provided to {co}.",
                f"VMA GROUP &amp; {co}")}
          {_row("Week 6<br><span class='wc'>w/c TBC</span>",
                "Final stage interviews conducted.<br><br>Offer to appropriate candidate and offer "
                "management. Start date and onboarding process agreed.<br><br>Weekly update provided to "
                f"{co}.",
                f"VMA GROUP &amp; {co}")}
        </tbody>
      </table>
    """)

    # ---- Fee structure ---------------------------------------------------
    fee = _interior(f"""
      <h2>FEE STRUCTURE</h2>
      <table class="fee">
        <thead><tr><th>Pricing model</th><th>Total cost</th></tr></thead>
        <tbody>
          <tr>
            <td>VMA GROUP are proposing a one off agreement based on a 18.5% fee rate on base salary
            only.<br><br>As an example, assuming a £130,000 annual salary.<br><br>
            Total fee = £130,000 x 18.5% = £24,050</td>
            <td class="fee-headline">18.5% fee to VMA GROUP</td>
          </tr>
        </tbody>
      </table>
      <p>Fee to be paid in three stages outlined below.</p>
      <table class="stages">
        <tbody>
          <tr><td><strong>Stage 1</strong></td><td>Commencement of search - 33% of fee payable = £8,016</td></tr>
          <tr><td><strong>Stage 2</strong></td><td>Agreement of short list for interview - 33% of fee payable = £8,016</td></tr>
          <tr><td><strong>Stage 3</strong></td><td>Candidate offer acceptance - 33% of fee payable = £8,016*</td></tr>
        </tbody>
      </table>
      <p class="note">*Final stage fee payment will be adjusted to reflect actual salary offered, on
      base salary only.</p>
      <h3>Exclusivity</h3>
      <p>To avoid any duplication, we ask that we act with full exclusivity as the search partner in
      representing {co} on this assignment.</p>
      <p>We believe that at the heart of our success is our innate knowledge and understanding of the
      communications industry and our understanding of the changes that are taking place within it;
      our credibility; our reputation for discretion and honesty; our robust search processes, our
      extensive network of contacts; and ultimately our friendly approach.</p>
      <p>We are confident that we will be able to find you the best candidate for the role and would
      be delighted to manage the search for this key member of your team.</p>
    """)

    # ---- Client appointments --------------------------------------------
    appts = _interior(f"""
      <h2>VMA GROUP CLIENT APPOINTMENTS</h2>
      <p>Some recent senior Communications appointments, £125,000 - £150,000.</p>
      <ol class="appts">
        <li>Head of Communications, GKN Automotive</li>
        <li>Director of Communications, Hilton Hotels</li>
        <li>Communications Director, Nestle</li>
        <li>Head of Communications, L'Oreal</li>
        <li>Communications Lead, Aston Martin</li>
        <li>Head of Communications, ABN AMRO</li>
        <li>Head of Communications, International Airlines Group</li>
        <li>Director of Communications, Virgin Media</li>
        <li>Head of Customer Communications, United Utilities</li>
        <li>Head of Communications, GSK</li>
      </ol>
      <p>VMA GROUP is trusted by a wide and varied group of listed and non-listed organisations,
      helping them build formidable Communications teams.</p>
      <img class="client-grid" src="{_asset_data_uri('vma_client_logos.png')}" alt="VMA Group clients">
    """)

    # ---- Back page -------------------------------------------------------
    back = f"""
    <section class="page back">
      <img class="vma-mark" src="{_asset_data_uri('vma_square.png')}" alt="VMA Group">
      <div class="back-footer">
        VMA Global Resourcing Group Ltd | 10 Bloomsbury Way, London, WC1A 2SL<br>
        Registered No: 06473593 England<br>
        www.vmagroup.com
      </div>
      <div class="running-footer">{_RUNNING}</div>
    </section>
    """

    return _DOC_TEMPLATE.format(
        css=_CSS,
        body="".join([cover, why, approach1, approach2, timing, fee, appts, back]),
    )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def generate(company: str, seat: str,
             when: _dt.date | _dt.datetime | None = None,
             fetch_logo: bool = True,
             logo_bytes: bytes | None = None) -> tuple[bytes, dict]:
    """Render the comms proposal to PDF bytes.

    Returns (pdf_bytes, meta) where meta = {"logo_source": str, "pages": int}.
    `logo_bytes` lets a caller inject a logo (used by tests); otherwise the
    target's logo is fetched best-effort, falling back to a wordmark."""
    source = "supplied" if logo_bytes else "wordmark"
    if logo_bytes is None and fetch_logo:
        logo_bytes, source = company_logo(company)
    html = render_proposal_html(company, seat, logo_bytes=logo_bytes, when=when)
    from weasyprint import HTML
    pdf = HTML(string=html).write_pdf()
    return pdf, {"logo_source": source}


# --------------------------------------------------------------------------
# Document shell + CSS  (kept at the bottom so the copy above reads cleanly).
# --------------------------------------------------------------------------
_DOC_TEMPLATE = (
    "<!doctype html><html><head><meta charset='utf-8'><style>{css}</style>"
    "</head><body>{body}</body></html>"
)

_CSS = f"""
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  font-family: "Helvetica Neue", Helvetica, Arial, "Liberation Sans", sans-serif;
  color: {_INK}; font-size: 10.5pt; line-height: 1.5;
}}
.page {{
  position: relative; width: 210mm; height: 297mm; overflow: hidden;
  page-break-after: always;
}}
.page:last-child {{ page-break-after: auto; }}

/* ---- Cover ---- */
.cover-band {{
  position: absolute; top: 0; left: 0; right: 0; height: 150px;
  background: {_BAND};
}}
.cover-band .vma-wordmark {{
  position: absolute; left: 42px; top: 50px; height: 56px; width: auto;
}}
.cover-logo {{
  position: absolute; top: 300px; left: 60px; right: 60px; height: 130px;
  display: flex; align-items: center; justify-content: center;
  text-align: center;
}}
.cover-logo .client-logo {{
  max-width: 300px; max-height: 130px; width: auto; height: auto;
  object-fit: contain;
}}
.cover-logo .client-wordmark {{
  font-size: 30pt; font-weight: 700; color: {_NAVY}; letter-spacing: .5px;
  line-height: 1.15;
}}
.cover-title {{
  position: absolute; top: 470px; left: 0; right: 0; margin: 0;
  text-align: center; color: {_NAVY};
  font-size: 34pt; font-weight: 800; letter-spacing: 1px;
}}
.cover-sub {{
  position: absolute; top: 540px; left: 0; right: 0; text-align: center;
  color: {_NAVY}; font-size: 15pt; font-weight: 700; letter-spacing: .5px;
}}
.cover-fields {{
  position: absolute; top: 640px; left: 96px; right: 96px;
}}
.cover-fields .cf-label {{
  color: {_NAVY}; font-weight: 700; font-size: 13pt; margin-top: 22px;
}}
.cover-fields .cf-value {{ font-size: 12pt; margin-top: 6px; }}

/* ---- Interior pages ---- */
.interior {{ padding: 64px 72px 70px; }}
.first-interior {{ padding-top: 58px; }}
.vma-mark {{
  position: absolute; top: 30px; right: 56px; width: 70px; height: auto;
}}
.interior .page-body {{ padding-top: 26px; }}
.running-footer {{
  position: absolute; bottom: 30px; right: 72px;
  color: {_NAVY}; font-size: 8.5pt; font-weight: 600; letter-spacing: .4px;
}}
h2 {{
  color: {_NAVY}; font-size: 15pt; font-weight: 800; letter-spacing: .4px;
  margin: 0 0 14px; text-transform: uppercase;
}}
h3 {{
  color: {_NAVY}; font-size: 11.5pt; font-weight: 700; margin: 16px 0 4px;
}}
p {{ margin: 0 0 11px; }}
ul.bullets {{ margin: 6px 0 12px; padding-left: 18px; }}
ul.bullets li {{ margin: 0 0 9px; }}
.signature {{ height: 42px; width: auto; margin: 6px 0 0; display: block; }}
.contact {{ margin-top: 2px; }}

/* ---- Timing table ---- */
table.timing {{ width: 100%; border-collapse: collapse; font-size: 9pt; }}
table.timing th {{
  background: {_BAND}; color: #fff; text-align: left; padding: 7px 9px;
  font-size: 9.5pt;
}}
table.timing td {{
  border: 1px solid #c9d2dd; padding: 7px 9px; vertical-align: top;
}}
table.timing td.wk {{ white-space: nowrap; font-weight: 700; color: {_NAVY}; width: 78px; }}
table.timing td.wk .wc {{ font-weight: 400; color: #777; font-size: 8pt; }}
table.timing td.resp {{ width: 120px; font-weight: 600; color: {_NAVY}; }}

/* ---- Fee tables ---- */
table.fee {{ width: 100%; border-collapse: collapse; font-size: 10pt; margin-bottom: 12px; }}
table.fee th {{
  background: {_BAND}; color: #fff; text-align: left; padding: 8px 10px;
}}
table.fee td {{ border: 1px solid #c9d2dd; padding: 12px 12px; vertical-align: top; }}
table.fee td.fee-headline {{ width: 200px; font-weight: 700; color: {_NAVY}; }}
table.stages {{ width: 100%; border-collapse: collapse; font-size: 10pt; }}
table.stages td {{ border: 1px solid #c9d2dd; padding: 8px 12px; }}
table.stages td:first-child {{ width: 90px; color: {_NAVY}; }}
.note {{ font-size: 8.5pt; color: #555; margin-top: 8px; }}

/* ---- Appointments ---- */
ol.appts {{ margin: 6px 0 12px; padding-left: 22px; }}
ol.appts li {{ margin: 2px 0; }}
.client-grid {{
  display: block; margin: 14px auto 0; max-width: 430px; width: 100%; height: auto;
}}

/* ---- Back page ---- */
.back .vma-mark {{ width: 92px; }}
.back-footer {{
  position: absolute; left: 0; right: 0; bottom: 150px; text-align: center;
  font-size: 10pt; line-height: 1.7;
}}
"""
