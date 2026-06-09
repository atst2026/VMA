"""Regression tests for the source-attribution prefix bug: 'Companies
House: Trustpilot filed…' resolved its SUBJECT to the watchlist employer
Companies House, merging unrelated companies' filings (Trustpilot, M&G)
into one phantom account that presented on the board."""
from tool.account_match import classify_account
from tool.predictive.detector import extract_company

SH01_TRUSTPILOT = ("Companies House: Trustpilot filed an allotment of shares "
                   "(SH01) on 2026-05-29 — fresh equity capital.")
SH01_MG = ("Companies House: M&G filed an allotment of shares (SH01) "
           "on 2026-06-06 — fresh equity capital.")


def test_registry_prefix_never_steals_the_subject():
    assert classify_account("Companies House", SH01_TRUSTPILOT) == \
        ("Trustpilot", "watchlist")
    assert classify_account("Companies House", SH01_MG) == ("M&G", "watchlist")


def test_mixed_evidence_resolves_to_the_real_company():
    hiring_gap = ("Trustpilot has 72 open roles on its public job board but "
                  "zero comms/PR/corporate-affairs positions.")
    name, tier = classify_account("Companies House",
                                  SH01_TRUSTPILOT + " . " + hiring_gap)
    assert name == "Trustpilot" and tier == "watchlist"


def test_companies_house_as_genuine_employer_still_resolves():
    # No colon — CH is the real subject here, and it IS a watchlist
    # employer. The strip must not suppress it.
    name, _ = classify_account(
        "Companies House",
        "Companies House appoints new Head of External Communications")
    assert name == "Companies House"


def test_extract_company_ignores_attribution_prefix():
    assert extract_company(SH01_TRUSTPILOT) != "Companies House"
    assert extract_company("SEC EDGAR: Acme Corp filed an 8-K") != "SEC EDGAR"


def test_other_registry_prefixes_stripped():
    for prefix in ("Find a Tender", "Charity Commission", "SEC EDGAR"):
        name, _ = classify_account(
            "Companies House", f"{prefix}: Trustpilot filed a notice today.")
        assert name != prefix, prefix
