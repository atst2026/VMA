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


# --- Round 2: the phantom came back as 'Companies House' -> IMI PLC. ----
# The first fix only stripped the bare colon-attached prefix. Three forms
# survived and re-created the phantom: the QUALIFIED prefix ("Companies
# House (historical): …"), the worded prefix ("Companies House stream:
# …"), and the name-LAST parenthetical ("… at IMI (Companies House
# filing).") — the last one defeats any prefix strip, and because the
# watchlist scan is longest-name-first, 'Companies House' (distinctive,
# multiword) always out-ranked an acronym-path subject like IMI.

HIST_IMI = ("Companies House (historical): SMITH, John resigned as "
            "Director of Corporate Communications at IMI on 2026-05-12.")
PAREN_IMI = ("John Smith departed as Director of Communications at IMI "
             "(Companies House filing).")


def test_qualified_registry_prefix_never_steals_the_subject():
    assert classify_account("IMI", HIST_IMI) == ("IMI", "watchlist")


def test_stream_prefix_never_steals_the_subject():
    name, tier = classify_account(
        "Trustpilot",
        "Companies House stream: Trustpilot filed change of registered "
        "office address on 2026-06-01.")
    assert (name, tier) == ("Trustpilot", "watchlist")


def test_name_last_parenthetical_never_steals_the_subject():
    assert classify_account("IMI", PAREN_IMI) == ("IMI", "watchlist")


def test_bare_registry_mention_loses_to_any_other_subject():
    # No prefix, no parenthetical — wording no strip regex anticipated.
    # The registry demotion (scan-last) must still let the real subject win.
    name, _ = classify_account(
        "IMI", "IMI registered a charge at Companies House on 2026-06-01.")
    assert name == "IMI"


def test_charity_commission_never_steals_the_subject():
    name, _ = classify_account(
        "British Heart Foundation",
        "British Heart Foundation board change — new trustee(s): A. Brown "
        "(Charity Commission register).")
    assert name == "British Heart Foundation"


def test_extract_company_ignores_qualified_and_parenthetical_forms():
    assert extract_company(HIST_IMI) != "Companies House"
    assert extract_company(PAREN_IMI) != "Companies House"
    assert extract_company("Officer departure", PAREN_IMI) != "Companies House"
