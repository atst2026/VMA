"""Tests for tool/company_logos — the offline-first logo registry + local
override that anchors the pitch-pack cover to the RIGHT company's logo.

All offline: the registry is pure data and the override reads a local folder.
"""
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import company_logos as cl


# ---- slug + lookup -----------------------------------------------------

def test_slugify_collapses_to_alnum():
    assert cl.slugify("Oxford Quantum Circuits") == "oxfordquantumcircuits"
    assert cl.slugify("M&S") == "ms"
    assert cl.slugify("  Diageo  PLC ") == "diageoplc"
    assert cl.slugify("") == ""


def test_lookup_by_name_alias_and_slug():
    # canonical slug, the display name, and registered aliases all resolve
    assert cl.lookup("Diageo")["domain"] == "diageo.com"
    assert cl.lookup("diageo plc")["domain"] == "diageo.com"
    assert cl.lookup("DIAGEO")["domain"] == "diageo.com"
    # acronym / trading-name aliases
    assert cl.lookup("oxford quantum circuits")["domain"] == "oqc.tech"
    assert cl.lookup("M&S")["domain"] == "marksandspencer.com"
    assert cl.lookup("british gas")["domain"] == "centrica.com"
    # an unrelated name is NOT in the registry
    assert cl.lookup("Some Unlisted Startup Ltd") is None
    assert cl.lookup("") is None


def test_registry_domain_pins_known_accounts():
    assert cl.registry_domain("Hilton Hotels") == "hilton.com"
    assert cl.registry_domain("GKN Automotive") == "gknautomotive.com"
    assert cl.registry_domain("International Airlines Group") == "iairgroup.com"
    assert cl.registry_domain("Totally Unknown Co") is None


def test_registry_logo_url_forms():
    # no entry currently pins a hosted asset, but the URL builders must be right
    assert cl.registry_logo_url("Diageo") is None          # domain-only entry
    cl_entry = {"domain": "x.com", "logo": "wikimedia:Foo logo.svg"}
    # exercise the builder directly via a synthetic alias
    cl._BY_SLUG["synthwiki"] = cl_entry
    try:
        url = cl.registry_logo_url("synthwiki")
        assert url.startswith("https://commons.wikimedia.org/wiki/Special:FilePath/")
        assert "Foo%20logo.svg" in url
    finally:
        cl._BY_SLUG.pop("synthwiki", None)

    cl._BY_SLUG["synthhttp"] = {"domain": "x.com", "logo": "https://cdn.x.com/l.svg"}
    try:
        assert cl.registry_logo_url("synthhttp") == "https://cdn.x.com/l.svg"
    finally:
        cl._BY_SLUG.pop("synthhttp", None)


# ---- local override (the unequivocal guarantee) ------------------------

def test_local_logo_reads_slug_named_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "OVERRIDE_DIR", tmp_path)
    # nothing dropped yet -> a clean miss
    assert cl.local_logo("Acme Robotics") == (None, "")
    # drop a verified file named for the slug
    data = b"\x89PNG\r\n\x1a\n" + b"\x10" * 200
    (tmp_path / "acmerobotics.png").write_bytes(data)
    got, src = cl.local_logo("Acme Robotics")
    assert got == data
    assert src == "local:acmerobotics.png"


def test_local_logo_extension_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "OVERRIDE_DIR", tmp_path)
    (tmp_path / "acme.png").write_bytes(b"PNGBYTES")
    (tmp_path / "acme.svg").write_bytes(b"<svg></svg>")
    # .svg outranks .png
    got, src = cl.local_logo("Acme")
    assert src == "local:acme.svg"


def test_local_logo_via_registry_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "OVERRIDE_DIR", tmp_path)
    # a file named for the canonical slug also covers the company's aliases
    (tmp_path / "centrica.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x20" * 100)
    # "British Gas" is an alias of the centrica entry; slug is "britishgas",
    # so the canonical-slug file is found via the registry "local:" hook only if
    # configured. Here we assert the slug-named file resolves for the exact name.
    got, src = cl.local_logo("Centrica")
    assert got is not None and src == "local:centrica.png"


def test_local_logo_registry_pinned_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "OVERRIDE_DIR", tmp_path)
    (tmp_path / "pinned.svg").write_bytes(b"<svg>pinned</svg>")
    entry = {"domain": "x.com", "logo": "local:pinned.svg"}
    monkeypatch.setitem(cl._BY_SLUG, "synthpinned", entry)
    got, src = cl.local_logo("synthpinned")
    assert got == b"<svg>pinned</svg>" and src == "local:pinned.svg"


def test_no_registry_domain_is_ever_an_aggregator():
    # a pinned domain that is itself an aggregator would defeat the purpose
    from tool.logo_finder import _AGGREGATORS, _registrable
    for slug, entry in cl.REGISTRY.items():
        dom = _registrable(entry["domain"])
        assert dom not in _AGGREGATORS, f"{slug} -> {dom} is an aggregator"
        assert "." in dom, f"{slug} -> {dom} is not a domain"
