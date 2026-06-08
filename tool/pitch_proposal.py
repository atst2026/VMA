#!/usr/bin/env python3
"""Branded "VMA GROUP SEARCH PROPOSAL" PDF generator (comms desk).

This is the comms-profile pitch pack: a true, client-facing PDF rendered from
the supplied VMA proposal template, parameterised per company. Compared with
the source template:

  * the cover displays the target company name as a clean typographic wordmark;
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
import io as _io
import logging
import os as _os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("pitch_proposal")

_ASSETS = Path(__file__).resolve().parent / "assets"

# ---- Cover client logo (logo.dev) -------------------------------------
# When we can fetch a clean, real logo for the target company we show it on
# the cover instead of the typographic wordmark. Domain comes from the
# verified registry in tool/company_identity (NO guessing); the token comes
# from the LOGODEV_TOKEN env var (passed from a GitHub Actions secret in
# .github/workflows/pitch-pack.yml). Any failure — no token, unknown
# company, network error, or a logo that fails the visibility gate — falls
# back to the wordmark, so there is never a regression.
_LOGODEV_REQUEST_SIZE = 256      # px we ask logo.dev for (retina doubles it)
_LOGO_MIN_DIM = 128              # reject anything smaller on its long edge
_LOGO_MAX_ASPECT = 8.0          # reject absurdly wide/tall strips
_COVER_LOGO_MAX_H = 130         # matches the .cover-logo box height (CSS)
_COVER_LOGO_MAX_W = 420         # matches .client-wordmark max-width (CSS)
_LOGO_HTTP_TIMEOUT = 12

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
# Rendering helpers
# --------------------------------------------------------------------------
def _esc(s) -> str:
    return _html.escape(str(s) if s is not None else "", quote=True)


def _generation_date(when: _dt.date | _dt.datetime | None = None) -> str:
    """The pitch-pack generation date, no time — e.g. '5 June 2026'."""
    d = when or _dt.datetime.now()
    return f"{d.day} {d.strftime('%B %Y')}"


def _logo_token() -> str:
    return (_os.environ.get("LOGODEV_TOKEN") or "").strip()


def _fetch_logo_png(domain: str, token: str) -> bytes | None:
    """Fetch the logo PNG bytes from logo.dev for a verified domain, reusing
    the project's hardened HTTP helper (retries, UA, timeout)."""
    try:
        from tool.sources._http import get as _get
        r = _get(
            f"https://img.logo.dev/{domain}",
            # fallback=404: when logo.dev has NO real logo for the domain it
            # otherwise generates a single-letter monogram (e.g. a bare "O").
            # Asking for a 404 instead means a no-logo domain returns nothing,
            # so the pipeline falls to Wikidata or clean text — never a
            # meaningless letter. Domains with a real logo are unaffected (200).
            params={"token": token, "size": _LOGODEV_REQUEST_SIZE,
                    "format": "png", "retina": "true", "fallback": "404"},
            timeout=_LOGO_HTTP_TIMEOUT,
        )
        if r is None or r.status_code != 200 or not r.content:
            log.info("logo.dev: no usable response for %s", domain)
            return None
        return r.content
    except Exception as e:  # pragma: no cover - network/runtime guard
        log.info("logo.dev fetch failed for %s: %s", domain, e)
        return None


def _logo_is_placeholder(img) -> bool:
    """Best-effort reject of a blank tile or logo.dev's generated monogram
    fallback: one background colour covering almost the whole image with only
    a handful of genuinely-present colours.

    The image is composited onto WHITE first (the cover background), so the
    colours counted are the ones that will actually appear. This is essential
    for transparent logos: a monochrome wordmark on transparency (e.g. a black
    "Morgan Stanley") would otherwise collapse to a single RGB colour with the
    alpha ignored and be wrongly classed as a blank tile. Counts EXACT colours
    (no resampling, which would smear in anti-alias colours and hide the
    signal). Conservative — the verified-domain registry is the primary guard,
    so this must not reject legitimately simple logos.

    The monogram/blank flag also requires the image to be roughly SQUARE: a
    generated monogram or blank tile is square, whereas a wide wordmark never
    is, so a wordmark can never be misclassed as a placeholder.

    Two square-only signals catch a placeholder:
      * uniform tile — one colour covers ~all of the image (a blank/solid tile);
      * sparse single glyph — very little ink on the page (logo.dev's
        generated letter-monogram, e.g. a bare "O", is a thin anti-aliased
        glyph covering only a few percent of a square canvas; the anti-alias
        edges give it many grey colours so the uniform-tile test alone misses
        it, but its ink coverage is tiny).
    A real wordmark/logo is either wide (aspect >= 1.6) or has far more ink, so
    neither signal fires on it."""
    from PIL import Image
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    bg.alpha_composite(rgba)
    rgb = bg.convert("RGB")
    colors = rgb.getcolors(maxcolors=8192)
    if colors is None:
        return False  # lots of distinct colours -> a real, rich image
    total = sum(c for c, _ in colors)
    if total == 0:
        return True
    w, h = rgb.size
    aspect = max(w, h) / max(1, min(w, h))
    if aspect >= 1.6:
        return False  # wide -> a wordmark, never a monogram/blank tile
    dominant = max(c for c, _ in colors)
    significant = sum(1 for c, _ in colors if c / total >= 0.01)
    # Fraction of pixels with meaningful ink once shown on the white cover.
    ink = sum(n for n, (r, g, b) in colors if (r + g + b) / 3 < 200) / total
    uniform_tile = dominant / total >= 0.96 and significant <= 3
    sparse_glyph = ink < 0.10 and significant <= 4
    return uniform_tile or sparse_glyph


def _logo_content_bbox(img):
    """Bounding box of the non-background content, for trimming whitespace.
    Trims on alpha for transparent logos, else on difference from the
    top-left (background) colour for solid-background logos."""
    from PIL import Image, ImageChops
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return alpha.getbbox()
    rgb = img.convert("RGB")
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    return ImageChops.difference(rgb, bg).getbbox()


def _process_logo(png_bytes: bytes, box_h: int, max_w: int) -> str | None:
    """Validate -> trim -> scale to fit the cover box -> return a data: URI."""
    from PIL import Image
    try:
        img = Image.open(_io.BytesIO(png_bytes))
        img.load()
    except Exception as e:
        log.info("logo.dev: not a decodable image (%s)", e)
        return None

    w, h = img.size
    if max(w, h) < _LOGO_MIN_DIM:
        log.info("logo.dev: image too small (%dx%d)", w, h)
        return None

    rgba = img.convert("RGBA")
    if rgba.getchannel("A").getextrema()[1] == 0:
        log.info("logo.dev: fully transparent image rejected")
        return None
    if _logo_is_placeholder(img):
        log.info("logo.dev: looks like a generated monogram placeholder, rejected")
        return None

    bbox = _logo_content_bbox(img)
    if bbox:
        rgba = rgba.crop(bbox)
    w, h = rgba.size
    if w == 0 or h == 0:
        return None
    if max(w, h) / max(1, min(w, h)) > _LOGO_MAX_ASPECT:
        log.info("logo.dev: aspect ratio %dx%d out of range, rejected", w, h)
        return None

    # Embed at ~2x the display box for crisp print rendering; the CSS caps the
    # on-page size. Fit within both height and width, never upscaling past 2x.
    scale = min(box_h * 2 / h, max_w * 2 / w, 2.0)
    rgba = rgba.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                       Image.LANCZOS)
    out = _io.BytesIO()
    rgba.save(out, format="PNG", optimize=True)
    return f"data:image/png;base64,{base64.b64encode(out.getvalue()).decode('ascii')}"


def _passes_visibility(png_bytes: bytes) -> bool:
    """True if the image stays visible composited on the WHITE cover — guards
    against white/near-transparent logos that would vanish on the page (the
    Wetherspoon-white-'W' failure mode). Requires >= 2% of pixels to be
    meaningfully dark/inked after compositing on white."""
    from PIL import Image
    try:
        img = Image.open(_io.BytesIO(png_bytes)).convert("RGBA")
        img.load()
    except Exception:
        return False
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    lum = bg.convert("L")
    total = img.size[0] * img.size[1]
    if total == 0:
        return False
    ink = sum(1 for p in lum.getdata() if p < 190) / total
    return ink >= 0.02


def _company_logo_data_uri(company: str, box_h: int, max_w: int) -> str | None:
    """Real-logo data URI for a company, or None to use the text wordmark.

    Source priority:
      1. Wikidata logo image (P154) — PREFERRED, because it frequently includes
         the company NAME (a full wordmark), not just the bare symbol. Used only
         when it passes the Pillow quality gate AND the visible-on-white check.
      2. logo.dev brand image for the resolved domain — the fallback (today's
         behaviour) when there is no usable P154.
      3. None -> the caller renders the text wordmark.
    """
    # 1) Wikidata P154 first.
    try:
        from tool import company_domain
        p154 = company_domain.wikidata_logo_png(company)
        if p154 and _passes_visibility(p154):
            uri = _process_logo(p154, box_h, max_w)
            if uri:
                log.info("cover: using Wikidata P154 logo for %r", company)
                return uri
    except Exception as e:
        log.info("P154 logo lookup failed for %r (%s) — trying logo.dev", company, e)

    # 2) logo.dev fallback.
    token = _logo_token()
    if not token:
        log.info("no P154 and LOGODEV_TOKEN unset for %r — text wordmark", company)
        return None
    try:
        from tool import company_domain
        domain = company_domain.resolve_domain(company)
    except Exception as e:
        log.info("domain resolution failed for %r (%s) — text wordmark", company, e)
        return None
    if not domain:
        log.info("no confident domain for %r — text wordmark", company)
        return None
    png = _fetch_logo_png(domain, token)
    if not png:
        return None
    return _process_logo(png, box_h, max_w)


def _cover_logo_html(company: str) -> str:
    """Cover client identity: the company's real logo when we can fetch a
    clean one (logo.dev), otherwise the company name as a typographic
    wordmark (the original, always-safe fallback)."""
    try:
        uri = _company_logo_data_uri(company, _COVER_LOGO_MAX_H, _COVER_LOGO_MAX_W)
    except Exception as e:  # never let a logo lookup break PDF generation
        log.info("cover logo lookup failed for %r: %s", company, e)
        uri = None
    if uri:
        return f'<img class="client-logo" src="{uri}" alt="{_esc(company)}">'
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
                         when: _dt.date | _dt.datetime | None = None) -> str:
    """The full multi-page proposal as print-styled HTML (A4). The cover
    displays the company name as a clean typographic wordmark."""
    # Strip market-data noise ("... Stock", "(03888)") so the heading, body
    # copy and logo lookup all use the clean company name. No-op for clean names.
    try:
        from tool import company_domain
        company = company_domain.clean_name(company)
    except Exception:
        pass
    co = _esc(company)
    seat_disp = _esc(seat or "Head of Communications")
    date_disp = _esc(_generation_date(when))

    # ---- Cover -----------------------------------------------------------
    cover = f"""
    <section class="page cover">
      <div class="cover-band">
        <img class="vma-wordmark" src="{_asset_data_uri('vma_wordmark_white.png')}" alt="VMA Group">
      </div>
      <div class="cover-logo">{_cover_logo_html(company)}</div>
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
             when: _dt.date | _dt.datetime | None = None) -> bytes:
    """Render the comms proposal to PDF bytes. The cover displays the company
    name as a clean typographic wordmark."""
    html = render_proposal_html(company, seat, when=when)
    from weasyprint import HTML
    return HTML(string=html).write_pdf()


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
.cover-logo .client-wordmark {{
  max-width: 420px; color: {_NAVY}; font-weight: 800;
  font-size: 30pt; line-height: 1.1; letter-spacing: .3px; text-align: center;
  overflow-wrap: break-word; word-wrap: break-word;
}}
.cover-logo .client-logo {{
  max-width: 420px; max-height: 130px; width: auto; height: auto;
  object-fit: contain;
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
