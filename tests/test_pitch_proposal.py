"""Tests for the comms "VMA GROUP SEARCH PROPOSAL" PDF (tool/pitch_proposal).

Verifies the wired-in template behaviour:
  * the example client ("Belron") is replaced by the target company everywhere;
  * the consultant-team page and the cover "Prepared by" line are dropped;
  * the cover carries the predicted seat and the generation date (no time);
  * a real logo is embedded when supplied, with a clean wordmark fallback;
  * the comms profile routes to this PDF (marketing keeps the dynamic pack).

These run offline (fetch_logo=False), so no network is required.
"""
import datetime as dt
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# The proposal is rendered with WeasyPrint; skip cleanly where its native
# stack isn't installed (the PDF system libs are provisioned in the workflow).
pytest.importorskip("weasyprint")


def _pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF using whatever reader is available."""
    try:
        import io
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        pass
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        pass
    try:
        import fitz
        d = fitz.open(stream=pdf_bytes, filetype="pdf")
        return "\n".join(p.get_text() for p in d)
    except Exception:
        pytest.skip("no PDF text extractor available")


def _page_count(pdf_bytes: bytes) -> int:
    try:
        import io
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        import fitz
        return fitz.open(stream=pdf_bytes, filetype="pdf").page_count


# ---- generation-date helper -------------------------------------------

def test_generation_date_has_no_time():
    from tool.pitch_proposal import _generation_date
    s = _generation_date(dt.datetime(2026, 6, 5, 14, 30, 59))
    assert s == "5 June 2026"
    # no clock time leaked in
    assert ":" not in s and "14" not in s


# ---- domain candidates / logo resolution ------------------------------

def test_domain_candidates_known_and_guessed():
    from tool.pitch_proposal import _domain_candidates
    assert "belron.com" in _domain_candidates("Belron")
    assert "diageo.com" in _domain_candidates("Diageo")
    # generic guess for an unknown name (suffixes stripped, .com + .co.uk)
    cands = _domain_candidates("Acme Holdings Ltd")
    assert "acme.com" in cands and "acme.co.uk" in cands


# ---- end-to-end render invariants -------------------------------------

def test_company_replaces_belron_everywhere():
    from tool import pitch_proposal as pp
    pdf, meta = pp.generate("Diageo", "Head of Corporate Communications",
                            fetch_logo=False)
    text = _pdf_text(pdf)
    assert "Belron" not in text
    # company woven through the body (why-VMA, timing table, exclusivity, …)
    assert text.count("Diageo") >= 5


def test_team_page_and_prepared_by_removed():
    from tool import pitch_proposal as pp
    pdf, _ = pp.generate("Diageo", "Head of Communications", fetch_logo=False)
    text = _pdf_text(pdf)
    assert "CONSULTANT TEAM" not in text.upper()
    assert "Prepared by" not in text
    # 9-page template minus the consultant-team page == 8.
    assert _page_count(pdf) == 8


def test_cover_carries_seat_and_date():
    from tool import pitch_proposal as pp
    seat = "Director of Corporate Affairs"
    pdf, _ = pp.generate("Tesco", seat, when=dt.datetime(2026, 6, 5, 9, 0),
                         fetch_logo=False)
    text = _pdf_text(pdf)
    assert "For the position of:" in text
    assert seat in text
    assert "Date:" in text
    assert "5 June 2026" in text


def test_wordmark_fallback_when_no_logo():
    from tool import pitch_proposal as pp
    pdf, meta = pp.generate("Riverside Housing Association",
                            "Head of Communications", fetch_logo=False)
    assert meta["logo_source"] == "wordmark"
    # the company name appears on the cover as the wordmark
    assert "Riverside Housing Association" in _pdf_text(pdf)


def test_supplied_logo_is_embedded():
    from tool import pitch_proposal as pp
    # a tiny but valid PNG (1x1) — exercises the image-embed path
    import base64
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
    pdf, meta = pp.generate("Diageo", "Head of Communications",
                            fetch_logo=False, logo_bytes=png)
    assert meta["logo_source"] == "supplied"
    assert _page_count(pdf) == 8


def test_valid_logo_rejects_junk_accepts_png():
    from tool.pitch_proposal import _valid_logo
    assert not _valid_logo(b"", "image/png")
    assert not _valid_logo(b"<html>404</html>", "text/html")
    # too-small RASTER payload rejected even if it claims to be an image
    assert not _valid_logo(b"x" * 100, "image/png")


def test_valid_logo_accepts_svg_by_structure():
    from tool.pitch_proposal import _valid_logo
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">'
           b'<rect width="200" height="80"/></svg>')
    assert _valid_logo(svg, "image/svg+xml")
    assert _valid_logo(svg, "")                 # sniffed even without a content-type
    assert not _valid_logo(b"<svg> no closing tag", "image/svg+xml")


def test_img_data_uri_sniffs_mime():
    import base64
    from tool.pitch_proposal import _img_data_uri
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")
    assert _img_data_uri(png).startswith("data:image/png;base64,")
    assert _img_data_uri(b"\xff\xd8\xff\xe0junk").startswith("data:image/jpeg;base64,")
    assert _img_data_uri(b"<svg></svg>").startswith("data:image/svg+xml;base64,")


def test_logo_providers_are_ordered_authoritative_first():
    # Official-logo sources (Wikidata P154, Wikipedia infobox) lead, so the
    # cover gets the real brand mark, not a domain-service guess or a wordmark.
    from tool import pitch_proposal as pp
    names = [p.__name__ for p in pp._LOGO_PROVIDERS]
    assert names[0] == "_logo_from_wikidata"
    assert names.index("_logo_from_wikidata") < names.index("_logo_from_clearbit")
    assert "_logo_from_wikipedia" in names and "_logo_from_favicon" in names


def test_company_logo_falls_back_to_wordmark_offline():
    # No network in the test env -> every provider misses and we get the
    # wordmark sentinel rather than an exception.
    from tool import pitch_proposal as pp
    data, src = pp.company_logo("Equinor")
    assert data is None and src == "wordmark"


# ---- profile routing ---------------------------------------------------

def test_comms_profile_routes_to_proposal():
    # Default profile is comms -> the dynamic-pack flag is off, and the
    # comms proposal entry point exists and is wired into pitch_pack.main().
    from tool import pitch_pack
    assert pitch_pack._MKT is False
    assert hasattr(pitch_pack, "_run_comms_proposal")
