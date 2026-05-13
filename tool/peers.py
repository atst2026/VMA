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
