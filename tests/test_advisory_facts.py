"""Buyer resolution from the contacts roster — the wiring that moves a
verified advisory lead from DEVELOP to PURSUE once an owner is on file."""
from datetime import date

import pytest

from tool import advisory_gate as G
from tool.advisory_facts import facts_resolver
from tool.advisory_signals.from_predictors import predictor_advisory_signals
from tool.contacts.schema import ContactCard, ContactEntry

TODAY = date(2026, 6, 14)


@pytest.fixture(autouse=True)
def _comms_default(monkeypatch):
    monkeypatch.delenv("VMA_PROFILE", raising=False)


def _roster():
    card = ContactCard(company="Acme Group", entries={
        "cco": ContactEntry(name="Dana Okoye",
                            role_title="Chief Communications Officer",
                            role_slot="cco", confidence=0.92,
                            verified_at="2026-05-01T00:00:00+00:00")})
    return {"Acme Group": card}


def _mna_signal():
    entry = {"company": "Acme Group", "pid": "acme", "status": "active",
             "events": [{"trigger_key": "mna", "trigger_label": "M&A",
                         "url": "https://www.investegate.co.uk/x",
                         "source": "Investegate RNS",
                         "published": "2026-06-01T00:00:00+00:00"}]}
    return predictor_advisory_signals(entries=[entry], today=TODAY)[0]


def test_resolver_maps_named_contact_to_facts():
    resolve = facts_resolver(contacts=_roster())
    facts = resolve(_mna_signal())
    assert facts["sponsor_name"] == "Dana Okoye"
    assert "Communications" in facts["sponsor_title"]


def test_unknown_company_resolves_to_empty():
    resolve = facts_resolver(contacts=_roster())

    class _S:
        company = "Nobody We Know Ltd"
    assert resolve(_S()) == {}


def test_resolved_buyer_promotes_predictor_lead_to_pursue():
    sig = _mna_signal()
    # Without the roster: DEVELOP (no owner).
    assert G.assess(sig, facts={}, today=TODAY)["verdict"] == "DEVELOP"
    # With the roster-resolved buyer: PURSUE, owned by Lucy (advisory-led).
    facts = facts_resolver(contacts=_roster())(sig)
    out = G.assess(sig, facts=facts, today=TODAY)
    assert out["verdict"] == "PURSUE"
    assert out["qual"]["sponsor"] == 2 and out["qual"]["access"] >= 1
    assert out["owner"]["owner"] == "Lucy Cairncross"
