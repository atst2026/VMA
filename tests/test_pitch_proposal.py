"""Tests for the comms "VMA GROUP SEARCH PROPOSAL" PDF (tool/pitch_proposal).

Verifies the wired-in template behaviour:
  * the example client ("Belron") is replaced by the target company everywhere;
  * the consultant-team page and the cover "Prepared by" line are dropped;
  * the cover carries the predicted seat and the generation date (no time);
  * the cover embeds the validated company logo (resolved by tool/logo_service,
    which is stubbed here; its own behaviour is in tests/test_logo_service.py),
    and generation FAILS rather than ship a pack without a correct logo;
  * the comms profile routes to this PDF (marketing keeps the dynamic pack).

These run offline (the logo service is stubbed), so no network is required.
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
# generate() resolves the logo via tool/logo_service; stub it so these render
# tests stay offline and deterministic. Logo-service behaviour has its own
# suite (tests/test_logo_service.py).

_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 400)


@pytest.fixture
def _stub_logo(monkeypatch):
    from tool import pitch_proposal as pp
    from tool import logo_service as ls

    def fake(name):
        c = __import__("tool.company_identity", fromlist=["resolve"])
        try:
            comp = c.resolve(name)
            cid, cname = comp.id, comp.name
        except Exception:
            cid, cname = "stub", name
        return ls.ResolvedLogo(cid, cname, f"https://{cid}.example/logo.png",
                               _PNG, "image/png", "domain:clearbit")

    monkeypatch.setattr(pp.logo_service, "get_logo", fake)
    return fake


def test_company_replaces_belron_everywhere(_stub_logo):
    from tool import pitch_proposal as pp
    pdf = pp.generate("Diageo", "Head of Corporate Communications")
    text = _pdf_text(pdf)
    assert "Belron" not in text
    # company woven through the body (why-VMA, timing table, exclusivity, …)
    assert text.count("Diageo") >= 5


def test_team_page_and_prepared_by_removed(_stub_logo):
    from tool import pitch_proposal as pp
    pdf = pp.generate("Diageo", "Head of Communications")
    text = _pdf_text(pdf)
    assert "CONSULTANT TEAM" not in text.upper()
    assert "Prepared by" not in text
    # 9-page template minus the consultant-team page == 8.
    assert _page_count(pdf) == 8


def test_cover_carries_seat_and_date(_stub_logo):
    from tool import pitch_proposal as pp
    seat = "Director of Corporate Affairs"
    pdf = pp.generate("Tesco", seat, when=dt.datetime(2026, 6, 5, 9, 0))
    text = _pdf_text(pdf)
    assert "For the position of:" in text
    assert seat in text
    assert "Date:" in text
    assert "5 June 2026" in text


def test_cover_embeds_the_resolved_logo(_stub_logo):
    # the cover always carries the validated logo image (no text fallback)
    from tool import pitch_proposal as pp
    html = pp._cover_logo_html("Acme", "data:image/png;base64,QUJD")
    assert html == '<img class="client-logo" src="data:image/png;base64,QUJD" alt="Acme logo">'


def test_generate_fails_when_logo_unresolved(monkeypatch):
    # the hard gate: if the logo can't be resolved, generate RAISES — no pdf,
    # no silent text fallback.
    from tool import pitch_proposal as pp
    from tool import logo_service as ls

    def boom(name):
        raise ls.LogoResolutionError("no valid logo")
    monkeypatch.setattr(pp.logo_service, "get_logo", boom)
    with pytest.raises(ls.LogoResolutionError):
        pp.generate("Diageo", "Head of Communications")


def test_generate_fails_for_unknown_company():
    # an unknown company can't be resolved to an identity -> generation fails.
    from tool import pitch_proposal as pp
    from tool import company_identity as ci
    with pytest.raises(ci.UnknownCompanyError):
        pp.generate("Totally Unknown Co Ltd", "Head of Communications")


# ---- profile routing ---------------------------------------------------

def test_comms_profile_routes_to_proposal():
    # Default profile is comms -> the dynamic-pack flag is off, and the
    # comms proposal entry point exists and is wired into pitch_pack.main().
    from tool import pitch_pack
    assert pitch_pack._MKT is False
    assert hasattr(pitch_pack, "_run_comms_proposal")
