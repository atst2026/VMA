"""Domain -> watchlist company name mapping for the 30 Tier-A accounts.

Used by pre_meeting.py to identify which target account a meeting
attendee works for, given their email address. Maps the public-website
domain (and a few well-known alternatives) to the canonical company
name as it appears in hiring_contacts.json and peers.SECTOR_PEERS.

Limited to the Tier-A FY27 watchlist - adding a domain here is the
only manual step required when expanding coverage.
"""
from __future__ import annotations

# Lowercase domains. Aliases (e.g. parent + UK subsidiary domains) all
# point to the canonical watchlist name.
DOMAIN_TO_COMPANY: dict[str, str] = {
    # Financial services
    "hsbc.com":                "HSBC",
    "hsbc.co.uk":              "HSBC",
    "natwestgroup.com":        "NatWest Group",
    "natwest.com":             "NatWest Group",
    "rbs.co.uk":               "NatWest Group",
    "lloydsbanking.com":       "Lloyds Banking Group",
    "lloydsbank.com":          "Lloyds Banking Group",
    "lloydstsb.com":           "Lloyds Banking Group",
    "barclays.com":            "Barclays",
    "barclays.co.uk":          "Barclays",
    "aviva.com":               "Aviva",
    "aviva.co.uk":              "Aviva",
    "landg.com":               "Legal & General",
    "legalandgeneral.com":     "Legal & General",
    "thephoenixgroup.com":     "Phoenix Group",
    "phoenixlife.co.uk":       "Phoenix Group",
    "sc.com":                  "Standard Chartered",
    "standardchartered.com":   "Standard Chartered",
    "schroders.com":           "Schroders",

    # Healthcare & pharma
    "astrazeneca.com":         "AstraZeneca",
    "gsk.com":                 "GSK",
    "haleon.com":              "Haleon",
    "bupa.com":                "Bupa UK",
    "bupa.co.uk":              "Bupa UK",
    "nhs.net":                 "NHS England",
    "england.nhs.uk":          "NHS England",
    "nhs.uk":                  "NHS England",

    # Energy & utilities
    "nationalgrid.com":        "National Grid",
    "sse.com":                 "SSE",
    "bp.com":                  "BP",
    "shell.com":               "Shell",
    "severntrent.co.uk":       "Severn Trent",
    "severntrent.com":         "Severn Trent",
    "unitedutilities.com":     "United Utilities",
    "uuplc.co.uk":             "United Utilities",

    # Technology / telecoms
    "bt.com":                  "BT Group",
    "btplc.com":               "BT Group",
    "openreach.com":            "BT Group",
    "vodafone.com":            "Vodafone",
    "vodafone.co.uk":          "Vodafone",
    "microsoft.com":           "Microsoft",
    "google.com":              "Google",
    "alphabet.com":            "Google",
    "sage.com":                "Sage Group",

    # Public sector / regulated / NFP
    "bbc.co.uk":               "BBC",
    "bbc.com":                 "BBC",
    "cabinetoffice.gov.uk":    "Cabinet Office",
    "fca.org.uk":              "FCA",
    "macmillan.org.uk":        "Macmillan Cancer Support",

    # Cross-sector
    "baesystems.com":          "BAE Systems",
}


def company_for_email(email: str) -> str | None:
    """Return the watchlist company name for an attendee email, or None
    if the domain isn't in our Tier-A list."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    if domain in DOMAIN_TO_COMPANY:
        return DOMAIN_TO_COMPANY[domain]
    # Try parent domain if it's a subdomain (e.g. uk.barclays.com -> barclays.com)
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in DOMAIN_TO_COMPANY:
            return DOMAIN_TO_COMPANY[parent]
    return None


def company_for_title_text(text: str) -> str | None:
    """Best-effort: scan free text (event title, description) for any
    watchlist company name. Used as a fallback when no attendee email
    domain matches - e.g. 'Coffee with Liv at Severn Trent' or
    'Call: HSBC succession planning'."""
    if not text:
        return None
    haystack = text.lower()
    # Iterate by length (longest first) so 'NHS England' beats 'NHS'
    for company in sorted(set(DOMAIN_TO_COMPANY.values()), key=lambda c: -len(c)):
        if company.lower() in haystack:
            return company
    return None
