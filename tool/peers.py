"""Sector classification + peer-employer lists for UK comms recruitment.

Used by pitch_pack.py and reverse_match.py. The lists are intentionally
hand-curated rather than scraped — they reflect the kind of large UK
employers Sara recruits comms leadership for. Easy to extend by editing
this file.
"""
from __future__ import annotations
import re

from tool.profiles import active_profile

# Sector -> ordered list of UK-listed or UK-major employers
SECTOR_PEERS: dict[str, list[str]] = {
    "financial_services": [
        "Barclays", "HSBC", "NatWest Group", "Lloyds Banking Group",
        "Standard Chartered", "Santander UK", "Nationwide Building Society",
        "Aviva", "Prudential", "Legal & General", "Phoenix Group",
        "M&G", "Schroders", "abrdn", "Quilter", "St James's Place",
        "Hargreaves Lansdown", "Admiral Group", "Direct Line Group",
        "Beazley", "Hiscox", "Just Group", "Lancashire Holdings",
        "RSA Insurance", "Saga", "Close Brothers", "Investec",
        "Paragon Banking Group", "Provident Financial", "Vanquis Banking",
        "AJ Bell", "IG Group", "Plus500", "CMC Markets", "TP ICAP",
        "Janus Henderson", "Jupiter Fund Management", "Liontrust Asset Management",
        "Rathbones Group", "Brewin Dolphin", "3i Group",
        "Intermediate Capital Group", "Bridgepoint", "Foresight Group",
        "Polar Capital", "OneSavings Bank", "Metro Bank", "Virgin Money UK",
        "TSB Bank", "Starling Bank", "Atom Bank", "Tide Bank",
    ],
    "pharma_healthcare": [
        "AstraZeneca", "GSK", "Haleon", "Hikma Pharmaceuticals",
        "Indivior", "ConvaTec", "Smith & Nephew", "Genus",
        "Bupa UK", "AXA Health", "Spire Healthcare",
        "NHS England", "NHS Confederation",
        "Vesuvius", "PureTech Health", "Oxford Biomedica",
        "Renalytix", "Diurnal Group", "Synairgen", "Tristel",
        "Mediclinic International", "Hilton Food Group",
        "Care UK", "Four Seasons Health Care", "Priory Group",
        "British Heart Foundation", "Marie Curie", "Cancer Research UK",
        "Diabetes UK", "Stroke Association", "Alzheimer's Society",
    ],
    "energy_utilities": [
        "BP", "Shell", "Centrica", "SSE", "National Grid",
        "Severn Trent", "United Utilities", "Pennon Group",
        "Octopus Energy", "EDF Energy UK", "E.ON UK", "Drax",
        "Cadent Gas", "Northumbrian Water", "Anglian Water",
        "Harbour Energy", "EnQuest", "Tullow Oil", "Hunting plc",
        "Petrofac", "Diversified Energy", "Capricorn Energy",
        "Greencoat UK Wind", "JLEN Environmental", "Bluefield Solar",
        "Foresight Solar", "Renewables Infrastructure", "Gore Street Energy",
        "OVO Energy", "Bulb Energy", "Good Energy",
        "Thames Water", "Yorkshire Water", "South West Water",
        "SGN", "Wales & West Utilities",
    ],
    "technology": [
        "Sage Group", "Aveva", "Auto Trader Group", "Rightmove",
        "Trustpilot", "Monzo", "Wise", "Revolut",
        "Deliveroo", "Just Eat Takeaway", "Ocado Group",
        "Cloudflare UK", "Stripe UK", "Cisco UK",
        "Computacenter", "Softcat", "Bytes Technology Group",
        "Kainos", "Boku", "Eckoh", "Aptitude Software",
        "GB Group", "Ideagen", "FDM Group", "Endava",
        "Network International", "Darktrace", "Sophos",
        "Trustly UK", "Klarna UK", "Checkout.com",
        "Zoopla", "OnTheMarket", "Funding Circle", "GoCardless",
    ],
    "retail_consumer": [
        "Unilever", "Reckitt", "Diageo", "Tesco", "Sainsbury's",
        "Marks & Spencer", "Next", "Burberry", "JD Sports",
        "Associated British Foods", "Whitbread", "Mitchells & Butlers",
        "WH Smith", "Greggs", "B&Q", "Boots", "Currys",
        "Frasers Group", "Sports Direct", "Howden Joinery",
        "Travis Perkins", "Kingfisher", "Halfords", "Wickes",
        "Pets at Home", "Watches of Switzerland", "Card Factory",
        "DFS Furniture", "Dunelm", "Domino's Pizza Group",
        "JD Wetherspoon", "Greene King", "Stonegate Pub Company",
        "Costa Coffee", "Pret a Manger", "Itsu", "Nando's Chickenland",
        "Imperial Brands", "British American Tobacco",
        "Cranswick", "Greencore", "Britvic", "Tate & Lyle",
        "Premier Foods", "Compass Group", "Whitbread",
        "John Lewis Partnership", "Waitrose", "Iceland Foods",
        "Asda Group", "Morrisons Supermarkets", "Co-operative Group",
        "Selfridges", "Harrods", "Liberty London", "Fortnum & Mason",
        "Burberry Group", "Mulberry Group", "Ted Baker",
    ],
    "industrial_manufacturing": [
        "Rolls-Royce", "BAE Systems", "Babcock International",
        "Melrose Industries", "IMI", "RS Group", "Smiths Group",
        "Spirax Group", "Bunzl",
        "Intertek Group", "Halma", "Rentokil Initial",
        "Croda International", "Renishaw", "Senior plc",
        "Avon Protection", "QinetiQ", "Chemring Group", "Ultra Electronics",
        "Weir Group", "Hill & Smith", "Morgan Advanced Materials",
        "Vesuvius", "AB Dynamics", "dotDigital",
        "Watkin Jones", "Volution Group", "Filtronic", "Castings plc",
        "Ferguson plc", "Howden Group", "DCC plc",
    ],
    "media_telecoms": [
        "BT Group", "Vodafone", "ITV", "Informa", "Pearson",
        "Sky UK", "Three UK", "Virgin Media O2", "TalkTalk",
        "Channel 4", "BBC", "Reach plc", "Future plc",
        "DMG Media", "Bauer Media", "Hollywood Bowl Group",
        "Cineworld Group", "Everyman Media Group",
        "William Hill", "Entain", "Flutter Entertainment", "888 Holdings",
        "Rank Group", "GVC Holdings",
        "WPP Group", "M&C Saatchi", "S4 Capital",
        "Daily Mail and General Trust", "Guardian Media Group",
        "STV Group", "Bloomsbury Publishing", "RELX",
    ],
    "professional_services": [
        "Hays", "PageGroup", "Robert Walters", "Capita", "Serco",
        "Mitie", "EY", "PwC", "KPMG", "Deloitte", "Accenture",
        "Robert Half", "Adecco", "Manpower Group", "Randstad",
        "Howden Group Holdings", "Marsh McLennan", "Aon",
        "BSI Group", "Lloyd's Register", "DNV",
        "Experian", "IWG", "Workspace Group",
        "Mott MacDonald", "Arup Group", "WSP", "Mace Group",
        "ISS Facility Services", "G4S", "Securitas",
        "Sodexo", "Aramark",
        "Kingsbridge Group", "Inchcape",
    ],
    "transport_logistics": [
        "Royal Mail", "DHL UK", "FedEx UK", "easyJet", "British Airways",
        "Wizz Air UK", "Network Rail", "Transport for London",
        "Stagecoach Group", "FirstGroup", "National Express",
        "International Consolidated Airlines", "IAG", "Ryanair UK",
        "DFDS", "Brittany Ferries", "P&O Ferries", "Stena Line",
        "DPDgroup UK", "Yodel", "Evri", "Parcelforce",
        "Go-Ahead Group", "Trainline", "ComfortDelGro UK",
        "Bidvest Logistics", "Wincanton", "Eddie Stobart Logistics",
    ],
    "real_estate": [
        "British Land", "Land Securities", "Segro", "Grainger",
        "Berkeley Group", "Persimmon", "Taylor Wimpey", "Barratt Developments",
        "Vistry Group",
        "Bellway", "Crest Nicholson", "Redrow", "Countryside Partnerships",
        "MJ Gleeson", "Henry Boot", "Springfield Properties",
        "Hammerson", "Derwent London", "Great Portland Estates",
        "Workspace Group", "Tritax Big Box REIT", "LXi REIT",
        "Primary Health Properties", "Capital & Counties Properties",
        "Foxtons", "Savills", "Knight Frank", "JLL UK",
        "L&Q Group", "Peabody Trust", "Notting Hill Genesis",
        "Clarion Housing Group", "Sanctuary Group", "Sovereign Network Group",
        "Network Homes", "Optivo", "Anchor Hanover",
    ],
    "public_sector_charities": [
        "Cabinet Office", "HM Revenue & Customs", "Department for Work and Pensions",
        "Department for Business and Trade", "Department of Health and Social Care",
        "British Red Cross", "Oxfam GB", "Cancer Research UK", "Macmillan Cancer Support",
        "RNLI", "WWF UK", "Save the Children UK", "Age UK", "Shelter",
        "Department for Education", "Department for Transport", "Home Office",
        "Ministry of Defence", "Ministry of Justice",
        "DEFRA", "Foreign Commonwealth and Development Office",
        "Met Office", "Office for National Statistics", "Companies House",
        "HM Prison Service", "Crown Prosecution Service",
        "Marie Curie", "British Heart Foundation",
        "RSPCA", "Dogs Trust", "Battersea Dogs & Cats Home", "NSPCC",
        "Christian Aid", "ActionAid UK", "Plan International UK",
        "Mind", "Samaritans", "Mencap", "Scope", "Royal British Legion",
        "Help for Heroes", "SSAFA", "Combat Stress",
        "British Council", "Arts Council England", "UK Sport",
        "Sport England", "Charity Commission",
    ],
    # International — Sara's secondary market. Major global firms with UK
    # offices / dual-listings / LSE+NYSE etc. Ranked lower than UK by the
    # geo-aware ranker but kept in the pipeline as legitimate predictors.
    "international": [
        # US — tech / financial / consumer
        "Apple", "Microsoft", "Alphabet", "Google", "Meta Platforms", "Amazon",
        "Tesla", "NVIDIA", "Oracle", "IBM", "Salesforce", "Adobe",
        "JPMorgan Chase", "Goldman Sachs", "Morgan Stanley", "Bank of America",
        "Citigroup", "BlackRock", "Berkshire Hathaway", "Wells Fargo",
        "Visa", "Mastercard", "American Express", "PayPal",
        "Apollo Global Management", "Carlyle Group", "KKR", "Blackstone",
        "TPG", "Bain Capital", "Brookfield",
        "Procter & Gamble", "PepsiCo", "Coca-Cola", "Walmart", "Costco",
        "Johnson & Johnson", "Pfizer", "Merck", "Eli Lilly", "AbbVie",
        "ExxonMobil", "Chevron", "ConocoPhillips",
        "Brown-Forman", "Estée Lauder", "Nike", "Starbucks", "McDonald's",
        "General Electric", "Boeing", "Lockheed Martin", "Raytheon",
        "Caterpillar", "3M", "Honeywell",
        # EU
        "Nestlé", "Roche", "Novartis", "Sanofi", "Bayer", "Merck KGaA",
        "Siemens", "Volkswagen", "BMW", "Mercedes-Benz", "Stellantis",
        "LVMH", "Kering", "Hermès", "L'Oréal", "Pernod Ricard",
        "BNP Paribas", "Société Générale", "Credit Suisse", "UBS",
        "Deutsche Bank", "Allianz", "AXA", "ING Group", "Santander",
        "Telefónica", "Orange", "Deutsche Telekom",
        "TotalEnergies", "Eni", "Equinor",
        "ASML", "SAP", "Spotify", "Adyen",
        "Heineken", "Carlsberg", "Anheuser-Busch InBev",
        "Maersk", "Lufthansa", "Air France-KLM", "Ryanair",
        # Asia-Pacific
        "Samsung Electronics", "Sony", "Toyota", "Honda", "Nintendo",
        "Tencent", "Alibaba", "Baidu", "ByteDance",
        "Macquarie Group", "Westpac", "ANZ",
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


def _starts_at_boundary(needle: str, hay: str) -> bool:
    """True if `needle` occurs in `hay` beginning at a word boundary."""
    if not needle or not hay:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle), hay) is not None


def detect_sector(name: str) -> str | None:
    """Best-effort sector detection. Direct match first, then fuzzy."""
    if not name:
        return None
    n = _normalise(name)
    if not n:
        return None
    # Exact lookup against normalised hardcoded list. Every curated name
    # self-detects here, so the fuzzy fallback below can only ever affect
    # partial / variant queries — never a curated company's own sector.
    for known, sector in COMPANY_TO_SECTOR.items():
        if _normalise(known) == n:
            return sector
    # Word-boundary-anchored containment (e.g. "Barclays UK" matches
    # "Barclays"; "Lloyds Bank" matches "Lloyds Banking Group"). Anchoring
    # the match to a token boundary stops a stripped short stem — "RS Group"
    # -> "rs" — from matching MID-WORD inside an unrelated employer
    # ("Rive[rs]ide Housing Association"), which used to mis-assign a sector
    # (and its heat multiplier) to off-watchlist leads.
    for known, sector in COMPANY_TO_SECTOR.items():
        k = _normalise(known)
        if k and (_starts_at_boundary(k, n) or _starts_at_boundary(n, k)):
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
    """Return (peer list, detected sector or None).

    This is the RANKER-facing peer/sector resolver (also used by
    reverse_match). It is deliberately left alone — the Pitch Pack uses
    pitch_peers_for() below for a sharper talent universe without
    disturbing sector_heat / the ranker."""
    sector = detect_sector(name)
    if sector:
        peers = [c for c in SECTOR_PEERS[sector] if _normalise(c) != _normalise(name)]
        return peers[:k], sector
    return GENERIC_FALLBACK[:k], None


# ---- Pitch-Pack talent universe: cross-sector affinity cohorts ----------
# The broad ranking sectors (SECTOR_PEERS) are right for the ranker but too
# coarse for a client-facing "where your candidates sit" list: retail_consumer
# lumps a global drinks brand (Diageo) in with grocers (Greggs, B&Q), so a
# Diageo pitch was shown the wrong move-from set. These affinity groups are a
# Pitch-Pack-ONLY refinement — they do NOT touch SECTOR_PEERS, COMPANY_TO_SECTOR
# or SECTOR_HEAT, so the ranker and predictor pipeline are unaffected.
#
# Insertion order = match priority (a company in two cohorts takes the first).
# Each member list is curated most-comparable-first, UK-major ahead of global,
# since Sara recruits UK-primary.
PITCH_AFFINITY_GROUPS: "dict[str, dict]" = {
    "global_consumer_brands": {
        "label": "Global consumer brands & FMCG",
        "members": [
            "Unilever", "Reckitt", "Diageo", "Haleon", "Associated British Foods",
            "Britvic", "Premier Foods", "Tate & Lyle", "Imperial Brands",
            "British American Tobacco", "Cranswick", "Greencore",
            "Pernod Ricard", "Anheuser-Busch InBev", "Heineken", "Carlsberg",
            "Mondelez", "PepsiCo", "Coca-Cola HBC", "Nestle", "Mars",
            "Kraft Heinz", "Procter & Gamble", "L'Oreal", "Estee Lauder",
            "Danone", "Kellanova",
        ],
    },
    "premium_luxury": {
        "label": "Premium & luxury brands",
        "members": [
            "Burberry", "Mulberry", "Watches of Switzerland", "Ted Baker",
            "Selfridges", "Harrods", "Liberty London", "Fortnum & Mason",
            "LVMH", "Kering", "Hermes", "Richemont",
        ],
    },
    "grocery_retail": {
        "label": "Grocery & high-street retail",
        "members": [
            "Tesco", "Sainsbury's", "Marks & Spencer", "Asda Group",
            "Morrisons Supermarkets", "Co-operative Group", "Waitrose",
            "Iceland Foods", "Next", "Boots", "Currys", "WH Smith", "Greggs",
            "B&Q", "Kingfisher", "Halfords", "Wickes", "Pets at Home", "Dunelm",
            "Frasers Group", "JD Sports", "Card Factory",
        ],
    },
    "hospitality_leisure": {
        "label": "Hospitality, leisure & eating-out",
        "members": [
            "Whitbread", "Compass Group", "Mitchells & Butlers", "Greene King",
            "JD Wetherspoon", "Stonegate Pub Company", "Domino's Pizza Group",
            "Costa Coffee", "Pret a Manger", "Hollywood Bowl Group", "Rank Group",
            "Entain", "Flutter Entertainment",
        ],
    },
    "uk_banks": {
        "label": "UK banks & building societies",
        "members": [
            "Barclays", "HSBC", "Lloyds Banking Group", "NatWest Group",
            "Santander UK", "Nationwide Building Society", "Standard Chartered",
            "Virgin Money UK", "TSB Bank", "Metro Bank", "Close Brothers",
            "Investec",
        ],
    },
    "insurers": {
        "label": "Insurers & protection",
        "members": [
            "Aviva", "Legal & General", "Prudential", "Phoenix Group",
            "Admiral Group", "Direct Line Group", "RSA Insurance", "Beazley",
            "Hiscox", "Just Group", "Lancashire Holdings", "Saga",
        ],
    },
    "asset_wealth_managers": {
        "label": "Asset & wealth management",
        "members": [
            "Schroders", "M&G", "abrdn", "St James's Place", "Quilter",
            "Hargreaves Lansdown", "Janus Henderson", "Jupiter Fund Management",
            "Liontrust Asset Management", "Rathbones Group", "AJ Bell",
            "3i Group", "Intermediate Capital Group", "Bridgepoint",
        ],
    },
    "fintech_challengers": {
        "label": "Fintech & challenger finance",
        "members": [
            "Monzo", "Starling Bank", "Wise", "Revolut", "GoCardless",
            "Funding Circle", "Atom Bank", "Tide Bank", "Checkout.com",
            "IG Group", "Plus500", "CMC Markets",
        ],
    },
    "telecoms": {
        "label": "Telecoms & connectivity",
        "members": [
            "BT Group", "Vodafone", "Virgin Media O2", "Three UK", "Sky UK",
            "TalkTalk",
        ],
    },
    "broadcast_media": {
        "label": "Broadcast & media",
        "members": [
            "ITV", "Channel 4", "BBC", "Sky UK", "STV Group", "Reach plc",
            "Future plc", "Guardian Media Group", "Daily Mail and General Trust",
            "Bauer Media", "Informa", "RELX", "Pearson", "Bloomsbury Publishing",
        ],
    },
}

# company (normalised) -> affinity group key. First group wins (insertion order).
PITCH_COMPANY_TO_AFFINITY: dict[str, str] = {}
for _gkey, _gdef in PITCH_AFFINITY_GROUPS.items():
    for _co in _gdef["members"]:
        PITCH_COMPANY_TO_AFFINITY.setdefault(_normalise(_co), _gkey)


def _affinity_key_for(name: str) -> str | None:
    """Resolve a company to its Pitch-Pack affinity cohort, or None."""
    n = _normalise(name)
    if not n:
        return None
    grp = PITCH_COMPANY_TO_AFFINITY.get(n)
    if grp:
        return grp
    # Boundary-anchored containment (same discipline as detect_sector) so
    # "Diageo plc" / "Diageo GB" resolve, without matching mid-word.
    for member_norm, g in PITCH_COMPANY_TO_AFFINITY.items():
        if member_norm and (_starts_at_boundary(member_norm, n)
                            or _starts_at_boundary(n, member_norm)):
            return g
    return None


def pitch_peers_for(name: str, k: int = 15) -> dict:
    """Pitch-Pack talent universe — a sharper 'where your candidates sit'
    list than the ranker's sector peers.

    Resolution order:
      1. AFFINITY cohort (cross-sector) — so a global drinks brand is shown
         other brand houses, not grocers.
      2. SECTOR peers — the existing list, when the sector is already coherent
         (e.g. energy_utilities).
      3. GENERIC — flagged so the Pitch Pack can REFUSE to show an irrelevant
         FTSE list (Barclays/BP) to an off-sector account (e.g. a charity).

    Returns {"peers", "label", "key", "source"} where source ∈
    {"affinity", "sector", "generic"} and key is the affinity/sector key
    (None when generic) — used to look up sector_context for Section 2."""
    grp = _affinity_key_for(name)
    if grp:
        n = _normalise(name)
        members = [c for c in PITCH_AFFINITY_GROUPS[grp]["members"]
                   if _normalise(c) != n]
        return {"peers": members[:k], "label": PITCH_AFFINITY_GROUPS[grp]["label"],
                "key": grp, "source": "affinity"}
    sector = detect_sector(name)
    if sector:
        peers = [c for c in SECTOR_PEERS[sector] if _normalise(c) != _normalise(name)]
        return {"peers": peers[:k], "label": sector.replace("_", " ").title(),
                "key": sector, "source": "sector"}
    return {"peers": GENERIC_FALLBACK[:k], "label": "Large UK employers",
            "key": None, "source": "generic"}


# Phase 1.3: sector-heat re-weighting. Detection emphasis is biased
# toward the UK sub-sectors actually hiring senior comms in 2026 and
# away from the cold ones (Indeed Hiring Lab + the detection-engine
# report). Config-only — no new feeds, no signals added or removed; it
# only shifts ranking emphasis so hot-sector opportunities surface
# higher and cold-sector ones lower.
_COMMS_SECTOR_HEAT: dict[str, float] = {
    "financial_services":      1.30,   # hot: Consumer Duty, IR, change comms
    "pharma_healthcare":       1.30,   # hot: pipeline + restructuring comms
    "energy_utilities":        1.30,   # hot: water special-admin, transition
    "public_sector_charities": 1.25,   # steady-to-hot: GCS growth, MOG
    "real_estate":             1.15,   # housing-leaning but mixed bag
    "retail_consumer":         0.65,   # cold: retail/hospitality contracting
    # neutral (1.0): technology, media_telecoms, professional_services,
    # industrial_manufacturing, transport_logistics, international, and
    # unclassified — the account-relevance gate already ensures the
    # company is on the watchlist, so unknown sector is not penalised.
}

# FIRST DRAFT — marketing's hot sectors are nearly the inverse of comms:
# consumer/retail, tech and media spend hardest on brand & growth
# leadership, while heavy-regulated/industrial sectors are cooler. Review
# with the marketing team.
_MARKETING_SECTOR_HEAT: dict[str, float] = {
    "retail_consumer":         1.35,   # hot: brand, growth, ecommerce, DTC
    "technology":              1.20,   # hot: growth/performance/product marketing
    "media_telecoms":          1.20,   # hot: brand & audience marketing
    "financial_services":      1.05,   # challenger/fintech brand marketing
    "real_estate":             0.95,
    "transport_logistics":     0.90,
    "pharma_healthcare":       0.85,
    "public_sector_charities": 0.85,
    "energy_utilities":        0.80,
    "industrial_manufacturing": 0.75,  # cold: B2B, marketing-light
    # neutral (1.0): professional_services, international, unclassified.
}

SECTOR_HEAT: dict[str, float] = (
    _MARKETING_SECTOR_HEAT if active_profile().key == "marketing"
    else _COMMS_SECTOR_HEAT
)


def sector_heat_multiplier(name: str) -> float:
    """Sector-heat weight for a company name. 1.0 for neutral/unknown."""
    sec = detect_sector(name)
    return SECTOR_HEAT.get(sec, 1.0) if sec else 1.0


# ---- LinkedIn company-slug mapping -------------------------------------
# LinkedIn's company-employees URL pattern:
#     linkedin.com/company/{slug}/people/?keywords=...
# This page shows ONLY that company's employees filtered by keyword on
# titles. Materially more reliable than global people-search because the
# company filter is implicit in the URL. Top result is the actual person
# at the actual company, not 'someone called CHRO somewhere in India'.
#
# Hand-curated for the UK FTSE-350-ish set we know. Unknown companies
# fall back to a guessed slug (lowercase, hyphenated). LinkedIn often
# redirects to the right URL even on guesses.
LINKEDIN_COMPANY_SLUGS: dict[str, str] = {
    # Financial services
    "barclays": "barclays-bank",
    "hsbc": "hsbc",
    "natwest group": "natwestgroup",
    "lloyds banking group": "lloyds-banking-group",
    "standard chartered": "standard-chartered-bank",
    "santander uk": "santander-uk",
    "nationwide building society": "nationwide-building-society",
    "aviva": "aviva-plc",
    "prudential": "prudential-uk",
    "legal & general": "legal-&-general",
    "phoenix group": "phoenix-group-holdings",
    "m&g": "m-and-g-plc",
    "schroders": "schroders",
    "abrdn": "abrdn",
    "quilter": "quilter-plc",
    "st james's place": "st-james's-place-wealth-management",
    "hargreaves lansdown": "hargreaves-lansdown",
    "admiral group": "admiral-group-plc",
    "direct line group": "directlinegroup",
    # Pharma / healthcare
    "astrazeneca": "astrazeneca",
    "gsk": "glaxosmithkline",
    "haleon": "haleon-com",
    "hikma pharmaceuticals": "hikma-pharmaceuticals-plc",
    "indivior": "indivior",
    "convatec": "convatec",
    "smith & nephew": "smith-&-nephew",
    "genus": "genus-plc",
    "bupa uk": "bupa",
    "axa health": "axa-uk",
    "spire healthcare": "spire-healthcare",
    "nhs england": "nhsengland",
    "nhs confederation": "nhsconfed",
    # Energy / utilities
    "bp": "bp",
    "shell": "shell",
    "centrica": "centrica",
    "sse": "sse-plc",
    "national grid": "national-grid",
    "severn trent": "severn-trent-plc",
    "united utilities": "united-utilities",
    "pennon group": "pennon-group-plc",
    "octopus energy": "octopus-energy",
    "edf energy uk": "edf-energy",
    "e.on uk": "eon-uk",
    "drax": "drax-group",
    "cadent gas": "cadent",
    "northumbrian water": "northumbrian-water-group",
    "anglian water": "anglian-water-services-ltd",
    "thames water": "thameswateruk",
    # Technology
    "sage group": "sage-software",
    "aveva": "aveva",
    "auto trader group": "auto-trader-group-plc",
    "rightmove": "rightmove",
    "trustpilot": "trustpilot",
    "monzo": "monzo-bank",
    "wise": "wise-com",
    "revolut": "revolut",
    "deliveroo": "deliveroo",
    "just eat takeaway": "just-eat-takeaway-com",
    "ocado group": "ocado-group",
    "cloudflare uk": "cloudflare",
    "stripe uk": "stripe",
    "cisco uk": "cisco",
    "palo alto networks": "palo-alto-networks",
    # Retail / consumer
    "unilever": "unilever",
    "reckitt": "reckitt",
    "diageo": "diageo",
    "tesco": "tesco",
    "sainsbury's": "j-sainsbury-plc",
    "marks & spencer": "marks-and-spencer",
    "next": "nextplc",
    "burberry": "burberry",
    "jd sports": "jd-sports-fashion-plc",
    "associated british foods": "associated-british-foods-plc",
    "whitbread": "whitbread",
    "mitchells & butlers": "mitchells-&-butlers",
    "wh smith": "wh-smith-plc",
    "greggs": "greggs-plc",
    "b&q": "b&q",
    "boots": "boots-uk",
    "currys": "currys-plc",
    # Industrial
    "rolls-royce": "rolls-royce",
    "bae systems": "bae-systems",
    "babcock international": "babcock-international-group",
    "melrose industries": "melrose-industries-plc",
    "imi": "imi-plc",
    "rs group": "rs-group",
    "smiths group": "smiths-group-plc",
    "spirax group": "spirax-sarco-engineering-plc",
    "bunzl": "bunzl-plc",
    # Media / telecoms
    "bt group": "bt",
    "vodafone": "vodafone",
    "itv": "itv",
    "informa": "informa-plc",
    "pearson": "pearson",
    "sky uk": "skyuk",
    "three uk": "three-uk",
    "virgin media o2": "virgin-media-o2",
    "talktalk": "talktalk-business",
    "channel 4": "channel-4-television",
    "bbc": "bbc",
    "reach plc": "reach-plc",
    "future plc": "future-plc",
    # Professional services
    "hays": "hays",
    "pagegroup": "pagegroup",
    "robert walters": "robert-walters-plc",
    "capita": "capita",
    "serco": "serco-group",
    "mitie": "mitie",
    "ey uk": "ernstandyoung",
    "pwc uk": "pwc",
    "kpmg uk": "kpmg-uk",
    "deloitte uk": "deloitte",
    "accenture uk": "accenture",
    # Transport / logistics
    "royal mail": "royal-mail-group",
    "dhl uk": "dhl",
    "fedex uk": "fedex",
    "easyjet": "easyjet",
    "british airways": "british-airways",
    "wizz air uk": "wizz-air",
    "network rail": "network-rail",
    "transport for london": "transport-for-london",
    "stagecoach group": "stagecoach-group",
    "firstgroup": "firstgroup-plc",
    "national express": "nationalexpressgroup",
    # Real estate
    "british land": "british-land-company",
    "land securities": "landsec",
    "segro": "segro",
    "grainger": "grainger-plc",
    "berkeley group": "berkeley-group-plc",
    "persimmon": "persimmon-plc",
    "taylor wimpey": "taylor-wimpey-plc",
    "barratt developments": "barratt-developments-plc",
    "vistry group": "vistry-group",
    # Public sector / charities
    "cabinet office": "cabinet-office",
    "hm revenue & customs": "hm-revenue-&-customs",
    "department for work and pensions": "dwpdigital",
    "department for business and trade": "department-for-business-and-trade",
    "department of health and social care": "department-of-health-and-social-care",
    "british red cross": "british-red-cross",
    "oxfam gb": "oxfam-international",
    "cancer research uk": "cancer-research-uk",
    "macmillan cancer support": "macmillan-cancer-support",
    "rnli": "rnli",
    "wwf uk": "wwf-uk",
    "save the children uk": "save-the-children",
    "age uk": "age-uk",
    "shelter": "shelter",
    # Additional commonly-targeted comms employers
    "youth hostel association": "youth-hostel-association-yha-",
    "centrica plc": "centrica",
}


def linkedin_company_slug(name: str) -> str | None:
    """Resolve a company name to its LinkedIn slug. None if not in the map."""
    n = _normalise(name)
    if not n:
        return None
    if n in LINKEDIN_COMPANY_SLUGS:
        return LINKEDIN_COMPANY_SLUGS[n]
    for k, slug in LINKEDIN_COMPANY_SLUGS.items():
        if k == n or k in n or n in k:
            return slug
    return None


def _slugify(name: str) -> str:
    """Best-guess slug for an unknown company. LinkedIn frequently redirects
    from a guessed slug to the real one, so this often works even if our
    map doesn't have the company."""
    s = (name or "").lower().strip()
    s = _SUFFIX_RX.sub("", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def linkedin_company_employees_url(name: str, role_keyword: str = "") -> str:
    """Build URL to LinkedIn's company-people page, optionally filtered by
    a role keyword. This is the targeted URL pattern that surfaces actual
    employees at the actual company — not a global search."""
    from urllib.parse import quote_plus
    slug = linkedin_company_slug(name) or _slugify(name)
    if not slug:
        kw = (role_keyword + " " + name).strip() if role_keyword else (name or "")
        return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(kw)}"
    base = f"https://www.linkedin.com/company/{slug}/people/"
    if role_keyword:
        return f"{base}?keywords={quote_plus(role_keyword)}"
    return base
