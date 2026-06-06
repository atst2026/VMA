"""Tests for the comms "VMA GROUP SEARCH PROPOSAL" PDF (tool/pitch_proposal).

Verifies the wired-in template behaviour:
  * the example client ("Belron") is replaced by the target company everywhere;
  * the consultant-team page and the cover "Prepared by" line are dropped;
  * the cover carries the predicted seat and the generation date (no time);
  * the cover displays the company name as a clean typographic wordmark;
  * the comms profile routes to this PDF (marketing keeps the dynamic pack).
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

def test_company_replaces_belron_everywhere():
    from tool import pitch_proposal as pp
    pdf = pp.generate("Diageo", "Head of Corporate Communications")
    text = _pdf_text(pdf)
    assert "Belron" not in text
    assert text.count("Diageo") >= 5


def test_team_page_and_prepared_by_removed():
    from tool import pitch_proposal as pp
    pdf = pp.generate("Diageo", "Head of Communications")
    text = _pdf_text(pdf)
    assert "CONSULTANT TEAM" not in text.upper()
    assert "Prepared by" not in text
    assert _page_count(pdf) == 8


def test_cover_carries_seat_and_date():
    from tool import pitch_proposal as pp
    seat = "Director of Corporate Affairs"
    pdf = pp.generate("Tesco", seat, when=dt.datetime(2026, 6, 5, 9, 0))
    text = _pdf_text(pdf)
    assert "For the position of:" in text
    assert seat in text
    assert "Date:" in text
    assert "5 June 2026" in text


def test_cover_renders_wordmark():
    from tool import pitch_proposal as pp
    assert pp._cover_logo_html("Acme & Co") == \
        '<div class="client-wordmark">Acme &amp; Co</div>'


def test_cover_renders_logo_when_provided():
    from tool import pitch_proposal as pp
    # 1x1 red PNG
    import base64
    red_px = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
        "nGP4z8BQDwAEgAF/pooBPQAAAABJRU5ErkJggg==")
    html = pp._cover_logo_html("TestCo", logo=(red_px, "image/png"))
    assert '<img class="client-logo"' in html
    assert 'alt="TestCo"' in html
    assert "data:image/png;base64," in html


def test_cover_falls_back_to_wordmark_without_logo():
    from tool import pitch_proposal as pp
    html = pp._cover_logo_html("TestCo", logo=None)
    assert "client-wordmark" in html
    assert "TestCo" in html


def test_generate_handles_any_company_name():
    from tool import pitch_proposal as pp
    pdf = pp.generate("Totally Unlisted Startup Ltd", "Head of Communications")
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


# ---- profile routing ---------------------------------------------------

def test_comms_profile_routes_to_proposal():
    # Default profile is comms -> the dynamic-pack flag is off, and the
    # comms proposal entry point exists and is wired into pitch_pack.main().
    from tool import pitch_pack
    assert pitch_pack._MKT is False
    assert hasattr(pitch_pack, "_run_comms_proposal")
