"""Tests for tool/company_web — pure name -> site -> logo logic (no network)."""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import company_web as cw


# ---- name normalisation ------------------------------------------------

def test_name_tokens_strips_suffixes():
    assert cw.name_tokens("Marks & Spencer plc") == ["marks", "spencer"]
    assert cw.name_tokens("Rolls-Royce Holdings") == ["rolls", "royce"]


def test_name_slug():
    assert cw.name_slug("Currys plc") == "currys"
    assert cw.name_slug("Pets at Home Group") == "petsathome"   # 'group' dropped, 'at' kept


# ---- domain guessing ---------------------------------------------------

def test_candidate_domains_com_first():
    doms = cw.candidate_domains("Currys")
    assert doms[0] == "currys.com"
    assert "currys.co.uk" in doms


def test_candidate_domains_multiword_variants():
    doms = cw.candidate_domains("Pets at Home")
    assert "petsathome.com" in doms        # joined
    assert "petshome.com" in doms          # join-word dropped
    assert "pets-at-home.com" in doms      # hyphenated


def test_candidate_domains_empty():
    assert cw.candidate_domains("   ") == []


# ---- host helpers ------------------------------------------------------

def test_registrable_handles_two_level_tlds():
    assert cw.registrable("www.currys.co.uk") == "currys.co.uk"
    assert cw.registrable("shop.foo.example.com") == "example.com"
    assert cw.registrable("acme.io") == "acme.io"


def test_is_excluded_host():
    assert cw.is_excluded_host("uk.linkedin.com")
    assert cw.is_excluded_host("en.wikipedia.org")
    assert not cw.is_excluded_host("currys.co.uk")


# ---- confidence gate ---------------------------------------------------

def test_gate_accepts_when_domain_is_the_name():
    assert cw.name_matches_site("Currys", "currys.co.uk", "<title>Whatever</title>")


def test_gate_accepts_when_site_name_matches():
    html = '<meta property="og:site_name" content="Acme Robotics Ltd">'
    assert cw.name_matches_site("Acme Robotics", "ar-group-holdings.com", html)


def test_gate_rejects_unrelated_site():
    html = "<title>Daily Press — Breaking News</title>"
    assert not cw.name_matches_site("Nonesuch Advisory", "daily-press.com", html)


# ---- logo extraction ---------------------------------------------------

def test_logo_prefers_header_wordmark_img():
    html = ('<header><a class="site-logo"><img src="/assets/logo.svg"></a></header>'
            '<link rel="apple-touch-icon" href="/ati.png">')
    out = cw.logo_urls_from_html(html, "https://x.com")
    assert out[0] == ("img", "https://x.com/assets/logo.svg")
    assert ("declared", "https://x.com/ati.png") in out


def test_logo_declared_icon_ranking():
    html = ('<link rel="icon" href="/fav.png">'
            '<link rel="apple-touch-icon" href="/ati.png">')
    out = cw.logo_urls_from_html(html, "https://x.com")
    declared = [u for k, u in out if k == "declared"]
    assert declared == ["https://x.com/ati.png"]   # apple-touch outranks plain icon


def test_logo_ignores_non_logo_images():
    html = '<header><img src="/hero-banner.jpg" alt="hero"></header>'
    out = cw.logo_urls_from_html(html, "https://x.com")
    assert all(kind != "img" for kind, _ in out)


def test_logo_img_via_src_cue():
    html = '<div><img src="/static/company-logo.png" alt=""></div>'
    out = cw.logo_urls_from_html(html, "https://x.com")
    assert ("img", "https://x.com/static/company-logo.png") in out


# ---- search-result parsing ---------------------------------------------

def test_search_decodes_uddg_and_filters_directories():
    html = ('<a class="result__a" href="/l/?uddg=https%3A%2F%2Fwww.currys.co.uk%2F">Currys</a>'
            '<a href="https://uk.linkedin.com/company/currys">LinkedIn</a>'
            '<a href="https://en.wikipedia.org/wiki/Currys">Wikipedia</a>')
    assert cw.search_result_domains(html) == ["currys.co.uk"]


def test_search_dedups_and_limits():
    html = "".join(
        f'<a href="https://site{i}.com/page">s{i}</a>' for i in range(10)
    ) + '<a href="https://site0.com/again">dup</a>'
    doms = cw.search_result_domains(html, limit=3)
    assert doms == ["site0.com", "site1.com", "site2.com"]
