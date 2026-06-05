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


# ---- end-to-end render invariants -------------------------------------
# (Logo-resolution internals are covered in tests/test_logo_finder.py.)

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


def test_company_logo_delegates_to_finder():
    # pitch_proposal.company_logo is logo_finder.find_logo; an empty name
    # resolves to the wordmark sentinel with no network calls.
    from tool import pitch_proposal as pp
    from tool import logo_finder
    assert pp.company_logo is logo_finder.find_logo
    assert pp.company_logo("") == (None, "wordmark")


def test_cover_logo_is_trimmed_to_fill_the_box():
    # The cover trims surrounding padding so the logo sits at a sensible size
    # rather than floating tiny in a sea of whitespace ("appropriately sized").
    pytest.importorskip("PIL")
    import base64
    import io
    from PIL import Image, ImageDraw
    from tool import pitch_proposal as pp

    pad = Image.new("RGBA", (240, 120), (255, 255, 255, 0))
    ImageDraw.Draw(pad).rectangle((100, 50, 140, 70), fill=(20, 20, 20, 255))
    buf = io.BytesIO()
    pad.save(buf, format="PNG")

    html = pp._cover_logo_html("Acme", buf.getvalue())
    assert 'class="client-logo"' in html
    data_uri = html.split('src="', 1)[1].split('"', 1)[0]
    embedded = base64.b64decode(data_uri.split(",", 1)[1])
    assert Image.open(io.BytesIO(embedded)).size < (240, 120)


def test_cover_wordmark_when_logo_bytes_none():
    # No logo -> the cover always prints the company name as a wordmark, so it can
    # never render blank (the reported PDF that showed neither logo nor name).
    from tool import pitch_proposal as pp
    html = pp._cover_logo_html("Acme Corporation", None)
    assert "client-wordmark" in html and "Acme Corporation" in html


# ---- profile routing ---------------------------------------------------

def test_comms_profile_routes_to_proposal():
    # Default profile is comms -> the dynamic-pack flag is off, and the
    # comms proposal entry point exists and is wired into pitch_pack.main().
    from tool import pitch_pack
    assert pitch_pack._MKT is False
    assert hasattr(pitch_pack, "_run_comms_proposal")
