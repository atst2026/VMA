"""Runtime configuration for the morning brief.

Specialism-specific settings (role taxonomy, salary floor, competitor
excludes, delivery identity) now live in a *profile* — see tool/profiles/.
The active profile is chosen by the VMA_PROFILE env var (default: comms),
so this module re-exports its values under the same names the rest of the
codebase already imports. Infrastructure that is the same for every
specialism (source URLs, ATS seeds, user-agent, sweep window, API keys)
stays defined here directly.

The explanatory comments that used to annotate each list now live beside
the data in tool/profiles/comms.py.
"""
import os

from tool.profiles import active_profile

_PROFILE = active_profile()

# --- Scope (from the active profile) ---
# Re-exported as lists (the shapes the rest of the codebase expects); the
# profile stores them as immutable tuples.
ROLE_KEYWORDS = list(_PROFILE.role_keywords)
EXCLUDE_TITLE_TERMS = list(_PROFILE.exclude_title_terms)
# Role titles we surface even at lower seniority = role keywords + the
# profile's lower-seniority extras.
JOB_TITLE_KEYWORDS = ROLE_KEYWORDS + list(_PROFILE.extra_job_title_keywords)
# Canonical job-search query set (one source of truth for every job lane).
JOB_SEARCH_QUERIES = list(_PROFILE.job_search_queries)

# --- Filters (from the active profile) ---
SALARY_FLOOR_PERM_GBP = _PROFILE.salary_floor_perm_gbp
# Companies whose jobs should NEVER appear (own employer + competitor
# search firms for this specialism).
COMPANY_EXCLUDE = list(_PROFILE.company_exclude)

# Job-board / aggregator names that sometimes surface as the "company"
# instead of the hiring employer (onlyFE, Architecture Social, Guardian
# Jobs ...). NOT excluded outright — Sara keeps board-sourced leads. Used
# by ranking.dedup to collapse a board's COPY of a role into the same role
# listed under its real employer, so we don't show the job twice (once as
# the board, once as the employer). Board-only leads with no employer twin
# are left untouched. Matched on the exact normalised name (no substring),
# so "Reed" never trips "Reed Smith".
JOB_BOARD_COMPANIES = [
    "onlyFE", "Architecture Social", "Guardian Jobs", "Totaljobs",
    "CV-Library", "Jobsite", "CharityJob", "Fish4jobs", "jobs.ac.uk",
    "Escape the City", "Milkround", "Reed", "Indeed", "Jora", "Monster",
]

# Known employer aliases / rebrands with NO shared text or acronym, so the
# fuzzy company matcher in ranking can't link them automatically — they must
# be declared. Maps a variant -> the canonical name it deduplicates under.
# Seeded with London South East Colleges, which rebranded to "Elevare Civic
# Education Group": Adzuna returns the SAME role under both names (identical
# job description), and they share no token/acronym so nothing else catches
# the duplicate. Matched on the normalised name.
COMPANY_ALIASES = {
    "Elevare Civic Education Group": "London South East Colleges",
}
# --- Geography ---
# UK primary; international secondary. Primary markets are boosted in ranking.
GEO_PRIMARY = {"UK", "United Kingdom", "Britain", "England", "Scotland", "Wales", "Northern Ireland", "GB"}
GEO_SECONDARY_WEIGHT = 0.6  # non-UK leads are weighted down but not excluded

# --- Delivery (recipient + test inbox from the active profile) ---
RECIPIENT = _PROFILE.recipient
TEST_RECIPIENT = _PROFILE.test_recipient
SEND_AT = "08:55"                         # Europe/London
SEND_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]  # Monday sweeps Sat+Sun too

# Report-generators (Pitch Pack, Reverse Match, Pre-meeting Brief, Manual
# Sweep) save HTML to disk and upload via GitHub Actions artifact, but do
# NOT send email — they're picked up from the dashboard's Recent Reports
# panel. Flip this to True to re-enable per-report email sends.
NON_BRIEF_EMAIL_ENABLED = False

# Master switch for the daily morning-brief email. OFF by default: the
# dashboard is the primary surface, so the brief still scours, ranks and
# refreshes the dashboard every run — it just emails no one. Set
# MORNING_BRIEF_EMAIL_ENABLED=1 (or True/yes/on) to resume email delivery.
MORNING_BRIEF_EMAIL_ENABLED = (
    (os.environ.get("MORNING_BRIEF_EMAIL_ENABLED") or "").strip().lower()
    in ("1", "true", "yes", "on")
)

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
    # PR-industry trade titles. PRWeek / Campaign (Haymarket) and Provoke
    # killed their public RSS (see the removed-sources note above) — they're
    # only reachable via fragile HTML scraping, so we don't; GDELT + Google
    # News pick their stories up secondhand. PRmoment and CIPR Influence
    # still publish standard WordPress RSS — the fastest first-party lane for
    # UK senior-comms appointments / moves (feeds Today's Leads, the cascade
    # Hire Watch, and the personal-brand-velocity predictor). Graceful-skip
    # if a path moves; live reachability is verified in GitHub Actions.
    "prmoment": "https://www.prmoment.com/feed",
    "cipr_influence": "https://influenceonline.co.uk/feed/",
    # Marketing-desk trade press (used when VMA_PROFILE=marketing; the
    # marketing rss_feeds rows reference these keys). Standard WordPress /
    # CMS feeds; graceful-skip if a path moves, like every feed here.
    "marketing_week": "https://www.marketingweek.com/feed/",
    "the_drum": "https://www.thedrum.com/rss.xml",
    "marketing_beat": "https://www.marketingbeat.co.uk/feed/",
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
    # Devolved procurement portals (kind=procurement) — below-threshold
    # Scottish / Welsh / NI public-body comms RFPs that Find-a-Tender (UK-
    # wide, above-threshold only) misses. PCS and Sell2Wales both run on the
    # Proactis/BravoSolution "Public Contracts" platform, which exposes a
    # standard notices RSS at NoticeDownload/Rss.aspx; eTendersNI is the
    # Jaggaer NI portal. Graceful-skip + CI-verified like every feed here —
    # a moved/dead path degrades to "no items", never an error.
    "public_contracts_scotland": "https://www.publiccontractsscotland.gov.uk/NoticeDownload/Rss.aspx",
    "sell2wales": "https://www.sell2wales.gov.wales/NoticeDownload/Rss.aspx",
    "etenders_ni": "https://etendersni.gov.uk/epps/rss/rss.xml",
    # Profession-specific & sector job boards (kind=job) — the highest-yield
    # FREE comms/PR/charity boards the FTSE-skewed ATS/Adzuna lanes miss.
    # CIPR / PRCA / CharityComms run Madgex boards (standard ?format=rss);
    # CharityJob and NHS Jobs publish search RSS. Graceful-skip + CI-verified.
    "cipr_jobs": "https://jobs.cipr.co.uk/jobs/?format=rss",
    "prca_jobs": "https://jobs.prca.org.uk/jobs/?format=rss",
    "charitycomms_jobs": "https://www.charitycomms.org.uk/jobs/feed",
    "charityjob": "https://www.charityjob.co.uk/v3api/jobs/rss?Keywords=communications",
    "nhs_jobs": "https://www.jobs.nhs.uk/api/v1/search_rss?keyword=communications",
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
# Slugs are runtime-verified: each morning brief logs which boards return
# jobs vs 404 (a wrong/retired slug is skipped, never fatal). Pruned May
# 2026 against a live run's log to ONLY the slugs that returned 200 — the
# speculative additions and a batch of retired originals all 404'd and
# were just dead requests. A 404 from these ATS APIs means "no such board"
# (not a transient outage), so pruning on 404 is safe.
ATS_SEEDS = {
    "greenhouse": [
        "monzo", "gocardless", "cloudflare", "stripe",
        "trustpilot", "tide", "cleo",
    ],
    "lever": [
        "plaid", "netflix", "palantir",
    ],
    "ashby": [
        "posthog", "linear", "ramp", "synthesia", "pleo",
    ],
    # Workable lane parked: the public endpoint
    # (apply.workable.com/api/v3/accounts/{slug}/jobs) 404'd for every
    # account tried, including long-standing seeds — so the endpoint
    # format is wrong and/or these orgs aren't on Workable. fetch_workable
    # stays wired (no-op on an empty list); re-add slugs only once a
    # working endpoint + real Workable-using orgs are confirmed live.
    "workable": [],
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

