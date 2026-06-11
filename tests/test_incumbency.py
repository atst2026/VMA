"""Tests for the incumbency check (tool/incumbency.py): title-family
mapping, SERP parsing, honest statuses, caching, and the graceful no-op
without Bright Data. No network calls anywhere."""
from datetime import datetime, timezone

import tool.incumbency as INC


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(INC, "CACHE_FILE", tmp_path / "incumbency.json")
    monkeypatch.setattr(INC.time, "sleep", lambda *_: None)


# ====================================================================
# Title-family mapping: the search must cover the FUNCTION, not the
# exact predicted phrase (the IMI / Erica Lockhart failure mode).
# ====================================================================
def test_corporate_affairs_family_includes_corporate_comms():
    key, titles = INC.family_for_seat("Corporate Affairs Director")
    assert key == "corporate_affairs"
    assert "Group Corporate Communications Director" in titles
    assert "External Affairs Director" in titles


def test_family_routing():
    assert INC.family_for_seat("Head of Investor Relations")[0] == \
        "investor_relations"
    assert INC.family_for_seat("Head of Internal Communications")[0] == \
        "internal_comms"
    assert INC.family_for_seat("Crisis / Head of Comms")[0] == "communications"
    assert INC.family_for_seat("Brand-Integration Director")[0] == \
        "marketing_brand"
    assert INC.family_for_seat("Corporate Affairs Director (new ownership)")[0] \
        == "corporate_affairs"
    # Unknown / empty seats fall back to the generic comms family.
    assert INC.family_for_seat("")[0] == "communications"


# ====================================================================
# SERP parsing: name + family title extracted; misses degrade to
# "profile found", never a wrong confident name.
# ====================================================================
_HTML = """
<h3>Erica Lockhart - Group Corporate Communications Director - IMI plc
| LinkedIn</h3>
<a href="https://uk.linkedin.com/in/erica-lockhart-123">profile</a>
"""


def test_parse_hit_extracts_name_and_title():
    _key, titles = INC.family_for_seat("Corporate Affairs Director")
    hit = INC._parse_hit(_HTML, titles)
    assert hit["url"].endswith("/in/erica-lockhart-123")
    assert hit["name"] == "Erica Lockhart"
    assert hit["title"] == "Group Corporate Communications Director"


def test_parse_hit_without_parsable_title_keeps_url_only():
    html = '<a href="https://www.linkedin.com/in/someone-456">x</a>'
    _key, titles = INC.family_for_seat("Corporate Affairs Director")
    hit = INC._parse_hit(html, titles)
    assert hit["url"].endswith("/in/someone-456")
    assert hit["name"] is None and hit["title"] is None


def test_parse_hit_no_profile():
    assert INC._parse_hit("<p>no profiles here</p>", ["X"]) is None


# ====================================================================
# check_incumbent: statuses, honesty of the note, cache behaviour,
# graceful no-op without Bright Data.
# ====================================================================
def _force_bd(monkeypatch, html):
    import tool.linkedin_resolver as LR
    monkeypatch.setattr(LR, "BRIGHT_DATA_KEY", "k")
    monkeypatch.setattr(LR, "BD_ZONE", "z")
    calls = []

    def fake_fetch(url, timeout=30):
        calls.append(url)
        return html
    monkeypatch.setattr(LR, "_bright_data_fetch", fake_fetch)
    return calls


def test_found_incumbent_reframes_not_kills(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    calls = _force_bd(monkeypatch, _HTML)
    res = INC.check_incumbent("IMI", "Corporate Affairs Director")
    assert res["status"] == "found"
    assert res["name"] == "Erica Lockhart"
    assert "UNDER them" in res["note"] and "buyer" in res["note"]
    # The query ORs the whole title family at the company.
    assert "Group+Corporate+Communications+Director" in calls[0].replace(
        "%22", "")
    # Second call is served from cache — no new fetch.
    INC.check_incumbent("IMI", "Corporate Affairs Director")
    assert len(calls) == 1


def test_none_found_is_reported_as_weak_evidence(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _force_bd(monkeypatch, "<p>nothing</p>")
    res = INC.check_incumbent("Acme", "Head of Communications")
    assert res["status"] == "none_found"
    assert "weak evidence" in res["note"]


def test_unchecked_without_bright_data(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    import tool.linkedin_resolver as LR
    monkeypatch.setattr(LR, "BRIGHT_DATA_KEY", "")
    monkeypatch.setattr(LR, "BD_ZONE", "")
    res = INC.check_incumbent("Acme", "Head of Communications")
    assert res["status"] == "unchecked"
    assert res["note"] == ""    # no claim either way


def test_build_seat_rewrites_to_the_build_under_the_incumbent():
    # The IMI case end-to-end: the card must sell the build under Erica
    # Lockhart, not her chair.
    assert INC.build_seat("Corporate Affairs Director", "Erica Lockhart") == \
        "Senior corporate affairs hires under Erica Lockhart"
    assert INC.build_seat("Head of Investor Relations", None) == \
        "Senior IR hires under the incumbent"
    assert INC.build_seat("Chief Marketing Officer", "A. Buyer") == \
        "Senior marketing hires under A. Buyer"


def test_annotate_entry_projects_fields(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _force_bd(monkeypatch, _HTML)
    entry = {"company": "IMI", "predicted_role": "Corporate Affairs Director"}
    INC.annotate_entry(entry)
    assert entry["incumbent_status"] == "found"
    assert entry["incumbent_name"] == "Erica Lockhart"
    assert "verify tenure" in entry["incumbent_note"]
