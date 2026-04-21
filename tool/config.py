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
    "chief communications officer", "cco",
    # PR / media
    "pr director", "public relations", "media relations", "head of media",
    "press office", "head of pr",
    # Generic / marketing-and-brand
    "marketing and brand", "brand director", "head of brand",
    "head of marketing and communications", "head of marketing and comms",
]

# Role titles we surface even at lower seniority (kept tight to avoid noise)
JOB_TITLE_KEYWORDS = ROLE_KEYWORDS + [
    "senior communications manager", "communications manager",
    "head of internal communications", "head of corporate communications",
    "head of external communications",
]

# --- Filters ---
SALARY_FLOOR_PERM_GBP = 40_000
DAY_RATE_FLOOR_GBP = 350
DAY_RATE_CEILING_GBP = 800

# --- Geography ---
# UK primary; international secondary. Primary markets are boosted in ranking.
GEO_PRIMARY = {"UK", "United Kingdom", "Britain", "England", "Scotland", "Wales", "Northern Ireland", "GB"}
GEO_SECONDARY_WEIGHT = 0.6  # non-UK leads are weighted down but not excluded

# --- Delivery ---
RECIPIENT = "stehrani@vmagroup.com"
TEST_RECIPIENT = "amirt12@hotmail.com"   # practice-run inbox
SEND_AT = "08:55"                         # Europe/London
SEND_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]  # Monday sweeps Sat+Sun too

# --- API keys (set via env, never commit secrets) ---
COMPANIES_HOUSE_KEY = os.environ.get("COMPANIES_HOUSE_KEY", "")
BRIGHT_DATA_KEY = os.environ.get("BRIGHT_DATA_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "onboarding@resend.dev")

# --- Source URLs (public, free) ---
SOURCES = {
    # UK authoritative
    "companies_house_stream": "https://stream.companieshouse.gov.uk/filings",
    "companies_house_api": "https://api.company-information.service.gov.uk",
    "investegate_rns": "https://www.investegate.co.uk/Rss.aspx?tf=LATEST",
    "fca_news": "https://www.fca.org.uk/news/rss.xml",
    "ofcom_news": "https://www.ofcom.org.uk/rss.xml",
    "ofgem_news": "https://www.ofgem.gov.uk/rss.xml",
    "ofwat_news": "https://www.ofwat.gov.uk/rss.xml",
    "ico_news": "https://ico.org.uk/about-the-ico/media-centre/rss/",
    "cma_news": "https://www.gov.uk/government/organisations/competition-and-markets-authority.atom",
    "contracts_finder": "https://www.contractsfinder.service.gov.uk/Published/Notices/rss",
    "find_a_tender": "https://www.find-tender.service.gov.uk/Notice/rss",
    "civil_service_jobs": "https://www.civilservicejobs.service.gov.uk/csr/rssfeed.cgi",
    "charity_commission_api": "https://api.charitycommission.gov.uk",
    # Trade press
    "prweek_uk": "https://www.prweek.com/rss/uk",
    "prweek_us": "https://www.prweek.com/rss/us",
    "prweek_asia": "https://www.prweek.com/rss/asia",
    "campaign": "https://www.campaignlive.co.uk/rss",
    "campaign_asia": "https://www.campaignasia.com/rss/all",
    "corpcomms": "https://www.corpcommsmagazine.co.uk/feed",
    "hr_magazine": "https://www.hrmagazine.co.uk/rss",
    "people_management": "https://www.peoplemanagement.co.uk/rss",
    "ragan": "https://www.ragan.com/feed/",
    "holmes_report": "https://www.provokemedia.com/rss",
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

# Known Greenhouse/Lever/Ashby/Workable slugs worth monitoring for comms roles.
# This list is seed-only; Sara can extend it. Represents major UK-adjacent employers
# using these ATSs.
ATS_SEEDS = {
    "greenhouse": [
        "monzo", "wise", "revolut", "deliveroo", "octopusenergy", "starlingbank",
        "gocardless", "checkr", "cloudflare", "airbnb", "stripe",
    ],
    "lever": [
        "gousto", "multiverse", "reddit", "plaid", "netflix", "palantir",
    ],
    "ashby": [
        "posthog", "linear", "ramp",
    ],
    "workable": [
        # add on demand
    ],
}

USER_AGENT = "Mozilla/5.0 (compatible; VMAMorningBrief/0.1; +https://www.vmagroup.com/)"
REQUEST_TIMEOUT = 20
