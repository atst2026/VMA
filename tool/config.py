"""Runtime configuration for Sara's morning brief."""
import os

# --- Scope ---
ROLE_KEYWORDS = [
    # Internal / change
    "internal communications", "internal comms", "change communications", "change comms",
    "employee communications", "employee comms", "colleague communications",
    # External / corporate
    "corporate communications", "corporate comms", "corporate affairs",
    "external communications", "external comms", "communications director",
    "head of communications", "head of comms", "director of communications",
    "chief communications officer",
    # PR / media
    "pr director", "public relations", "media relations", "head of media",
    "press office", "head of pr",
    # Generic / marketing-and-brand
    "marketing and brand", "brand director", "head of brand",
    "head of marketing and communications", "head of marketing and comms",
]

# Hard-exclude. These titles are agency/sales client-service roles that Sara
# does not work (she places into in-house comms functions only). A match here
# scores 0 regardless of how well the title hits the role keywords.
EXCLUDE_TITLE_TERMS = [
    # Agency client-service
    "account director", "senior account director", "group account director",
    "board account director", "account supervisor",
    "client services", "client director", "client partner", "client lead",
    # Sales
    "account executive", "account manager", "technical account",
    "account representative", "partner account", "named account",
    "renewal account", "renewals account", "enterprise account", "sales account",
    # Ambiguous but historically low-hit for in-house comms
    "sales director", "business development", "bd director",
    # CCO / CXO disambiguations — "CCO" alone is dropped from ROLE_KEYWORDS
    # because it also means Chief Compliance / Commercial / Customer / Cost.
    # These exclusions belt-and-braces against false positives like
    # "US CCO & BSA Officer" landing in the brief.
    "chief compliance officer", "chief commercial officer",
    "chief customer officer", "chief cost officer",
    "compliance officer", "bsa officer", "anti-money laundering",
    "bsa/aml", "aml officer",
]

# Role titles we surface even at lower seniority (kept tight to avoid noise)
JOB_TITLE_KEYWORDS = ROLE_KEYWORDS + [
    "senior communications manager", "communications manager",
    "head of internal communications", "head of corporate communications",
    "head of external communications",
]

# Canonical job-search query set. ONE source of truth for the phrases the
# job lanes (Adzuna / LinkedIn-public / Bright Data) search for, so a
# query added here widens every lane at once. Previously each lane hard-
# coded ~4-6 phrases — under-covering the role taxonomy and capping
# Today's-Leads recall. Ordered most→least senior so budget-capped lanes
# (LinkedIn/Bright Data) take the highest-value slice first.
JOB_SEARCH_QUERIES = [
    "chief communications officer",
    "director of communications",
    "communications director",
    "head of communications",
    "head of corporate communications",
    "corporate communications director",
    "head of corporate affairs",
    "corporate affairs director",
    "head of internal communications",
    "internal communications director",
    "head of external communications",
    "head of public affairs",
    "director of public affairs",
    "head of media relations",
    "head of investor relations",
    "pr director",
    "head of pr",
    "change communications lead",
    "employee communications manager",
]

# --- Filters ---
# VMA Group's specialism is Executive Search / Permanent Recruitment /
# Advisory — NOT interim staffing. A role whose only salary signal is a
# contractor day rate is therefore off-product and is filtered out;
# permanent salaries (>= floor) and unlabelled roles still pass. The old
# DAY_RATE_FLOOR/CEILING interim band was removed for this reason.
SALARY_FLOOR_PERM_GBP = 40_000

# Companies whose jobs should NEVER appear in Sara's leads list.
# VMA Group is her own employer; the others are direct competitor
# search firms (Sara doesn't pitch at her competitors).
COMPANY_EXCLUDE = [
    "VMA Group", "VMAGROUP", "VMA Recruitment",
    # Competitor IC/Corp Comms recruiters
    "Hanson Search", "Sapience Communications", "Sapience",
    "Ellwood Atfield", "Reuben Sinclair", "CommsSearch",
    "PRfect Search", "Madigan Search", "Quill Recruitment",
    "Major Players",
]
# --- Geography ---
# UK primary; international secondary. Primary markets are boosted in ranking.
GEO_PRIMARY = {"UK", "United Kingdom", "Britain", "England", "Scotland", "Wales", "Northern Ireland", "GB"}
GEO_SECONDARY_WEIGHT = 0.6  # non-UK leads are weighted down but not excluded

# --- Delivery ---
RECIPIENT = "stehrani@vmagroup.com"
TEST_RECIPIENT = "franc.laude1994@gmail.com"   # practice-run inbox (Gmail for reliable test delivery)
SEND_AT = "08:55"                         # Europe/London
SEND_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]  # Monday sweeps Sat+Sun too

# --- API keys (set via env, never commit secrets) ---
COMPANIES_HOUSE_KEY = os.environ.get("COMPANIES_HOUSE_KEY") or ""
BRIGHT_DATA_KEY = os.environ.get("BRIGHT_DATA_KEY") or ""
RESEND_API_KEY = os.environ.get("RESEND_API_KEY") or ""
# `or` (not `get(..., default)`) so that an env var set to empty string (which
# is what GitHub Actions does when a secret isn't configured) falls through
# to the default rather than overriding it.
RESEND_FROM = os.environ.get("RESEND_FROM") or "onboarding@resend.dev"

# --- Source URLs (public, free) ---
# Last refreshed 2026-05-15 after a morning brief run produced 9 dead-URL
# warnings. Sources removed since previous version:
#   - prweek_uk / prweek_us / prweek_asia: Haymarket killed RSS in 2024
#   - campaign / campaign_asia: same Haymarket family, RSS retired
#   - hr_magazine: feed URL no longer published
#   - people_management: CIPD removed RSS
#   - ico_news: ICO moved press centre, RSS path removed
#   - contracts_finder: gov.uk moved to API-only access
#   - holmes_report (provokemedia): RSS path removed
# Sources where the URL is still live but the runner gets 403 from bot
# protection now use a real-browser User-Agent (see USER_AGENT below).
SOURCES = {
    # UK authoritative
    "companies_house_stream": "https://stream.companieshouse.gov.uk/filings",
    "companies_house_api": "https://api.company-information.service.gov.uk",
    "investegate_rns": "https://www.investegate.co.uk/Rss.aspx?tf=LATEST",
    "fca_news": "https://www.fca.org.uk/news/rss.xml",
    "ofcom_news": "https://www.ofcom.org.uk/rss.xml",
    "ofgem_news": "https://www.ofgem.gov.uk/rss.xml",
    "ofwat_news": "https://www.ofwat.gov.uk/rss.xml",
    "cma_news": "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom",
    "find_a_tender": "https://www.find-tender.service.gov.uk/Notice/rss",
    "civil_service_jobs": "https://www.civilservicejobs.service.gov.uk/csr/rssfeed.cgi",
    "charity_commission_api": "https://api.charitycommission.gov.uk",
    # Trade press (only the feeds that still publish RSS in 2026)
    "corpcomms": "https://www.corpcommsmagazine.co.uk/feed",
    "ragan": "https://www.ragan.com/feed/",
    # Phase 3.9 — sector trade feeds. These deepen coverage of the
    # hot sectors (housing/real-estate, pharma, utilities) so the
    # predictor + following/contract-end detectors see sector moves
    # the generalist feeds miss. Standard CMS feed endpoints:
    #   - pharmaphorum / utility_week: WordPress default (/feed/)
    #   - fierce_biotech: Questex standard (/rss/xml)
    #   - inside_housing: Ocean Media public headline RSS (/rss)
    # Inside Housing & Utility Week are subscription titles: the public
    # feed is headline+standfirst only, which is exactly what the
    # detectors need (appointment / restructure / contract wording in
    # the title). Live reachability is verified in the GitHub Actions
    # run, NOT the sandbox (sandbox egress is 403-filtered). A dead /
    # paywalled / moved feed is fully non-fatal: rss_feeds.fetch_all()
    # skips any source whose key is missing, returns None, is empty, or
    # fails to parse — so a wrong URL degrades to "no items", never an
    # error, and is logged honestly like the removed feeds above.
    "pharmaphorum": "https://pharmaphorum.com/feed/",
    "fierce_biotech": "https://www.fiercebiotech.com/rss/xml",
    "inside_housing": "https://www.insidehousing.co.uk/rss",
    "utility_week": "https://utilityweek.co.uk/feed/",
    # Public-sector / HE / charity / media comms JOB lanes — the hot
    # sectors (public_sector_charities heat 1.25) that the FTSE-skewed
    # Adzuna/ATS lanes under-cover. Both publish standard search RSS;
    # graceful-skip if a feed path moves (logged like the dead feeds).
    #   jobs.ac.uk   — HE / research / university / some public-sector
    #   Guardian Jobs — the UK board for charity / public-sector / media
    #                    / NGO comms & PR leadership
    "jobs_ac_uk": "https://www.jobs.ac.uk/search/?keywords=communications&format=rss",
    "guardian_jobs": "https://jobs.theguardian.com/jobs/marketing-and-pr/?format=rss",
    # Funding / scale-up news — so the Funding-Round detector is no
    # longer GDELT-only (Sifted is paywalled with no clean public RSS;
    # these three publish standard WordPress RSS and cover UK/EU growth
    # rounds). kind=news -> flows into funding_round.detect_funding (and
    # the predictor). Graceful-skip if a feed path moves.
    "uktn": "https://www.uktech.news/feed",
    "businesscloud": "https://www.businesscloud.co.uk/feed/",
    "tech_eu": "https://tech.eu/feed/",
    # News graph
    "gdelt_doc": "https://api.gdeltproject.org/api/v2/doc/doc",
    # Jobs
    "adzuna_gb": "https://api.adzuna.com/v1/api/jobs/gb/search/1",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v3/accounts/{slug}/jobs",
    # SEC
    "sec_edgar": "https://www.sec.gov/cgi-bin/browse-edgar",
}

# Greenhouse/Lever/Ashby/Workable slugs to monitor for in-house comms
# roles. This is an OPPORTUNISTIC lane: large UK comms employers (FTSE,
# NHS, gov, housing) mostly use Workday/Eploy/bespoke ATS we can't seed
# here — so this lane targets the UK scale-ups / media / charities that
# DO use these public ATS boards. A wrong/retired slug is fully non-
# fatal: fetch_* skips any board returning non-200 (404). The real
# public-sector / HE recall comes from the dedicated RSS job lanes
# (jobs.ac.uk / NHS / Civil Service), not from here.
#   Slugs removed May 2026 (moved off-platform, 404):
#   greenhouse: wise, revolut, deliveroo, octopusenergy, starlingbank
#   lever:      gousto, multiverse, reddit
ATS_SEEDS = {
    "greenhouse": [
        "monzo", "gocardless", "cloudflare", "stripe",
        "trustpilot", "depop", "onfido", "snyk", "improbable",
        "thetrainline", "moonpig", "secretescapes", "bulb",
        "marshmallow", "zego", "tide", "cleo",
    ],
    "lever": [
        "plaid", "netflix", "palantir", "brewdog", "elvie",
    ],
    "ashby": [
        "posthog", "linear", "ramp", "synthesia", "pleo",
    ],
    "workable": [
        # UK charities / NGOs / mid-size orgs commonly use Workable.
        "comicrelief", "shelter", "scope", "mind",
    ],
}

# A real-browser User-Agent. The previous custom string
# ('VMAMorningBrief/0.1') was tripping bot-protection on Ofcom, Ofwat,
# Campaign, and Provoke Media, causing 403s on every run. Public RSS feeds
# are intended to be machine-readable, so a generic browser UA is the
# pragmatic fix and is widely used by feed readers.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20


# --- Window mode ---
# Daily run: 1 day. Manual fortnightly sweep: 14 days. Driven by an env var
# so individual source modules and the predictive ranker can widen their
# look-back window without touching their signature.
def sweep_days() -> int:
    val = os.environ.get("VMA_SWEEP_DAYS")
    if val and val.isdigit():
        n = int(val)
        return max(1, min(n, 60))   # cap at 60 days to keep API budgets sane
    return 1


def is_sweep() -> bool:
    return sweep_days() > 1

