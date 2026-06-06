"""Tests for tool/company_identity — exact, non-fuzzy canonical resolution."""
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from tool import company_identity as ci


def test_resolve_by_name_alias_id_and_slug():
    c = ci.resolve("Oxford Quantum Circuits")
    assert c.id == "oqc" and c.domain == "oqc.tech"
    assert ci.resolve("OQC").id == "oqc"               # alias
    assert ci.resolve("oqc").id == "oqc"               # id
    assert ci.resolve("  oxford quantum circuits ").id == "oqc"  # slug/space-insensitive
    assert ci.resolve("M&S").domain == "marksandspencer.com"
    assert ci.resolve("british gas").id == "centrica"  # alias -> canonical


def test_every_company_has_a_domain():
    # the whole system keys off a verified domain — none may be missing
    for c in ci._COMPANIES:
        assert c.domain and "." in c.domain, f"{c.id} missing a domain"


def test_unknown_company_raises_not_guesses():
    for bad in ("Totally Unknown Startup Ltd", "", "   ", "Acme Nonesuch"):
        with pytest.raises(ci.UnknownCompanyError):
            ci.resolve(bad)
    assert ci.is_known("Diageo") is True
    assert ci.is_known("Nope Inc") is False


def test_registry_has_no_key_collisions():
    # _build_index raises on collision; importing already ran it, but assert the
    # index is consistent (every id resolves to itself).
    for c in ci._COMPANIES:
        assert ci.resolve(c.id) is c
        assert ci.resolve(c.name) is c
