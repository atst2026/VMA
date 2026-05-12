"""Sector classification + peer-employer lists for UK comms recruitment.

Used by pitch_pack.py and reverse_match.py. The lists are intentionally
hand-curated rather than scraped — they reflect the kind of large UK
employers Sara recruits comms leadership for. Easy to extend by editing
this file.
"""
from __future__ import annotations
import re

# Sector -> ordered list of UK-listed or UK-major employers
SECTOR_PEERS: dict[str, list[str]] = {
    "financial_services": [
        "Barclays", "HSBC", "NatWest Group", "Lloyds Banking Group",
        "Standard Chartered", "Santander UK", "Nationwide Building Society",
        "Aviva", "Prudential", "Legal & General", "Phoenix Group",
        "M&G", "Schroders", "abrdn", "Quilter", "St James's Place",
        "Hargreaves Lansdown", "Admiral Group", "Direct Line Group",
    ],
    "pharma_healthcare": [
        "AstraZeneca", "GSK", "Haleon", "Hikma Pharmaceuticals",
        "Indivior", "ConvaTec", "Smith & Nephew", "Genus",
        "Bupa UK", "AXA Health", "Spire Healthcare",
        "NHS England", "NHS Confederation",
    ],
    "energy_utilities": [
        "BP", "Shell", "Centrica", "SSE", "National Grid",
        "Severn Trent", "United Utilities", "Pennon Group",
        "Octopus Energy", "EDF Energy UK", "E.ON UK", "Drax",
        "Cadent Gas", "Northumbrian Water", "Anglian Water",
    ],
    "technology": [
        "Sage Group", "Aveva", "Auto Trader Group", "Rightmove",
        "Trustpilot", "Monzo", "Wise", "Revolut",
        "Deliveroo", "Just Eat Takeaway", "Ocado Group",
        "Cloudflare UK", "Stripe UK", "Cisco UK",
    ],
    "retail_consumer": [
        "Unilever", "Reckitt", "Diageo", "Tesco", "Sainsbury's",
        "Marks & Spencer", "Next", "Burberry", "JD Sports",
        "Associated British Foods", "Whitbread", "Mitchells & Butlers",
        "WH Smith", "Greggs", "B&Q", "Boots", "Currys",
    ],
    "industrial_manufacturing": [
        "Rolls-Royce", "BAE Systems", "Babcock International",
        "Melrose Industries", "IMI", "RS Group", "Smiths Group",
        "Spirax Group", "Bunzl",
    ],
    "media_telecoms": [
        "BT Group", "Vodafone", "ITV", "Informa", "Pearson",
        "Sky UK", "Three UK", "Virgin Media O2", "TalkTalk",
        "Channel 4", "BBC", "Reach plc", "Future plc",
    ],
    "professional_services": [
        "Hays", "PageGroup", "Robert Walters", "Capita", "Serco",
        "Mitie", "EY UK", "PwC UK", "KPMG UK", "Deloitte UK",
        "Accenture UK",
    ],
    "transport_logistics": [
        "Royal Mail", "DHL UK", "FedEx UK", "easyJet", "British Airways",
        "Wizz Air UK", "Network Rail", "Transport for London",
        "Stagecoach Group", "FirstGroup", "National Express",
    ],
    "real_estate": [
        "British Land", "Land Securities", "Segro", "Grainger",
        "Berkeley Group", "Persimmon", "Taylor Wimpey", "Barratt Developments",
        "Vistry Group",
    ],
    "public_sector_charities": [
        "Cabinet Office", "HM Revenue & Customs", "Department for Work and Pensions",
        "Department for Business and Trade", "Department of Health and Social Care",
        "British Red Cross", "Oxfam GB", "Cancer Research UK", "Macmillan Cancer Support",
        "RNLI", "WWF UK", "Save the Children UK", "Age UK", "Shelter",
    ],
}

# Lookup: company name -> sector key. Built once at import.
COMPANY_TO_SECTOR: dict[str, str] = {}
for sector, companies in SECTOR_PEERS.items():
    for co in companies:
        COMPANY_TO_SECTOR[co.lower()] = sector


_SUFFIX_RX = re.compile(r"\b(plc|p\.l\.c\.|limited|ltd|group|holdings|inc|incorporated|llp|uk)\b", re.IGNORECASE)


def _normalise(name: str) -> str:
    s = (name or "").lower().strip()
    s = _SUFFIX_RX.sub("", s)
    s = re.sub(r"[^a-z0-9 &]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def detect_sector(name: str) -> str | None:
    """Best-effort sector detection. Direct match first, then fuzzy."""
    if not name:
        return None
    n = _normalise(name)
    if not n:
        return None
    # Exact lookup against normalised hardcoded list
    for known, sector in COMPANY_TO_SECTOR.items():
        if _normalise(known) == n:
            return sector
    # Substring match (e.g. "Barclays UK" matches "Barclays")
    for known, sector in COMPANY_TO_SECTOR.items():
        k = _normalise(known)
        if k and (k in n or n in k):
            return sector
    return None


# Fallback when sector can't be detected — a deliberately broad mix of
# large UK employers that are most likely to be relevant to senior comms
# recruitment. Sara can use this as a starting point for any pitch-pack.
GENERIC_FALLBACK: list[str] = [
    "Barclays", "BP", "AstraZeneca", "Unilever", "Diageo", "Tesco",
    "BT Group", "Vodafone", "Rolls-Royce", "Sage Group", "Aviva",
    "Reckitt", "GSK", "BAE Systems", "Lloyds Banking Group",
]


def peers_for(name: str, k: int = 15) -> tuple[list[str], str | None]:
    """Return (peer list, detected sector or None)."""
    sector = detect_sector(name)
    if sector:
        peers = [c for c in SECTOR_PEERS[sector] if _normalise(c) != _normalise(name)]
        return peers[:k], sector
    return GENERIC_FALLBACK[:k], None
