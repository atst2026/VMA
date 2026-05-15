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

# --- Filters ---
SALARY_FLOOR_PERM_GBP = 40_000
DAY_RATE_FLOOR_GBP = 350
DAY_RATE_CEILING_GBP = 800

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
# Slugs removed in May 2026 refresh because the companies moved off these
# ATS platforms (each returned 404 in the morning brief):
#   greenhouse: wise, revolut, deliveroo, octopusenergy, starlingbank
#   lever:      gousto, multiverse, reddit
ATS_SEEDS = {
    "greenhouse": [
        "monzo", "gocardless", "checkr", "cloudflare", "airbnb", "stripe",
    ],
    "lever": [
        "plaid", "netflix", "palantir",
    ],
    "ashby": [
        "posthog", "linear", "ramp",
    ],
    "workable": [
        # add on demand
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


# --- Distress-signal taxonomy ---
# Surfaces accounts where the comms hire is likely driven by trouble, not
# growth. In a dead market these are the abundant hires; in a hot market
# they're noise. Each entry: (regex pattern, category, weight). Higher
# weight = more urgent / more likely to convert to a brief Sara can win.
# Categories: profit_warning, ratings, activist, regulatory_probe,
# guidance_cut, restructuring, ceo_exit_under_cloud, m_and_a_distress,
# share_price_shock, crisis.
DISTRESS_SIGNALS = [
    # Profit warnings & guidance
    (r"\bprofit warning\b",                          "profit_warning",       1.0),
    (r"\bissues? a profit warning\b",                "profit_warning",       1.0),
    (r"\bguidance cut\b",                            "guidance_cut",         0.95),
    (r"\b(?:cut|cuts|cutting|lowers?|lowered|warns? on|warning on)\b.{0,40}\b(?:full[- ]year|FY|annual)?\s*(?:guidance|outlook|forecast)\b",
                                                     "guidance_cut",         0.9),
    (r"\b(?:downgrade|downgrades) (?:full[- ]year|FY)?\s*(?:guidance|outlook|forecast)\b",
                                                     "guidance_cut",         0.9),
    (r"\bwarns? on (?:full[- ]year|FY|annual) (?:guidance|outlook|forecast|profits?|earnings?)\b",
                                                     "guidance_cut",         0.9),
    (r"\b(?:trading update|trading statement)\b.{0,40}\b(?:below|behind|short)\b",
                                                     "guidance_cut",         0.7),
    # Ratings
    (r"\b(?:Moody'?s?|S&P|Fitch|DBRS)\b.{0,60}\b(?:downgrade|downgrades|downgraded|lowers?|cuts?)\b",
                                                     "ratings",              0.9),
    (r"\bcredit (?:rating )?downgrade\b",            "ratings",              0.85),
    (r"\b(?:credit rating|debt) (?:cut|lowered|downgraded)\b",
                                                     "ratings",              0.85),
    (r"\bnegative (?:outlook|watch)\b",              "ratings",              0.6),
    # Activist
    (r"\bactivist (?:investor|hedge fund|shareholder)\b",
                                                     "activist",             0.85),
    (r"\b(?:Elliott Management|Trian|Cevian|ValueAct|Bluebell|Engine No\.? 1|Pelham Capital)\b",
                                                     "activist",             0.8),
    (r"\bopen letter (?:to|from) (?:the )?(?:board|chair|shareholders)\b",
                                                     "activist",             0.7),
    (r"\bproxy (?:fight|battle|contest)\b",          "activist",             0.85),
    (r"\b(?:requisitions?|calls? for) (?:an? )?(?:EGM|extraordinary general meeting)\b",
                                                     "activist",             0.8),
    # Regulatory probes
    (r"\b(?:FCA|PRA|Ofcom|Ofgem|Ofwat|CMA|SFO|ICO)\b.{0,40}\b(?:investigation|probe|inquiry|review|opens? (?:an? )?(?:investigation|probe|inquiry))\b",
                                                     "regulatory_probe",     0.9),
    (r"\b(?:investigation|probe|inquiry) (?:by|from|into|launched by) (?:the )?(?:FCA|PRA|Ofcom|Ofgem|Ofwat|CMA|SFO|ICO)\b",
                                                     "regulatory_probe",     0.9),
    (r"\b(?:section 166|skilled person review)\b",   "regulatory_probe",     0.85),
    (r"\b(?:fine[ds]?|penalt(?:y|ies)) of (?:£|\$)\d",
                                                     "regulatory_probe",     0.75),
    (r"\benforcement action\b",                      "regulatory_probe",     0.8),
    (r"\bunder investigation by\b",                  "regulatory_probe",     0.8),
    # Restructuring & redundancy
    (r"\b(?:job cuts?|cuts? [\d,]+\s*(?:thousand|k)?\s*jobs?|redundancies)\b",
                                                     "restructuring",        0.85),
    (r"\b(?:strategic|cost) (?:review|reset|programme)\b",
                                                     "restructuring",        0.6),
    (r"\b(?:to cut|cutting|will cut|plans? to cut|axing)\b.{0,40}\b(?:jobs?|roles?|positions?|staff|workforce)\b",
                                                     "restructuring",        0.85),
    (r"\b(?:announces?|announced) (?:a )?restructur(?:e|ing)\b",
                                                     "restructuring",        0.8),
    (r"\bredundancy programme\b",                    "restructuring",        0.85),
    # CEO exits under cloud
    (r"\b(?:CEO|chief executive) (?:steps? down|resigns?|departs?)\b.{0,80}\b(?:immediate|with immediate effect|amid|after)\b",
                                                     "ceo_exit_under_cloud", 0.85),
    (r"\b(?:CEO|chief executive) ousted\b",          "ceo_exit_under_cloud", 0.95),
    (r"\b(?:CEO|CFO|chairman) (?:stands down|resigns) (?:with immediate effect|effective immediately)\b",
                                                     "ceo_exit_under_cloud", 0.85),
    # M&A under duress / share price shock
    (r"\b(?:rejects?|rejected|rebuffs?) (?:a )?(?:takeover|bid)\b",
                                                     "m_and_a_distress",     0.7),
    (r"\b(?:takeover|bid) approach\b",               "m_and_a_distress",     0.6),
    (r"\bshares? (?:plunge|tumble|crash|slump) \d",  "share_price_shock",    0.7),
    (r"\bshare price (?:plunges?|tumbles?|crashes?|slumps?)\b",
                                                     "share_price_shock",    0.7),
    # Generic crisis comms language
    (r"\b(?:data breach|cyber attack|ransomware|outage)\b",
                                                     "crisis",               0.85),
    (r"\b(?:class action|group litigation)\b",       "crisis",               0.6),
    (r"\bsuspended trading\b",                       "crisis",               0.8),
]


# --- Objection-handling playbook ---
# Sara's recurring negotiation situations with VMA-rooted defences. Each
# entry is (regex pattern, situation_label, 3-angle response). The
# objection coach matches against pasted text and returns the top 3
# situations with their angles.
OBJECTION_PLAYBOOK = [
    {
        "pattern": r"(?:\b(?:fee|commission|percentage|percent)\b|\d{1,2}\s*%).{0,60}\b(?:too high|negotiate|reduce|lower|push back|come down|drop|cut)\b|\b(?:reduce|lower|push back|come down|drop|cut)\b.{0,60}(?:\b(?:fee|commission|percentage|percent)\b|\d{1,2}\s*%)",
        "label":   "Client pushing back on fee",
        "angles":  [
            "Cost-to-replace defence: VMA's last comms hire at their cap-size segment averaged 7.4 months tenure; their internal-recruiter spend per failed comms hire is ~£28k. The 22% retained fee buys risk-share, not headcount.",
            "Speed-to-shortlist defence: contingent at 18% means they're competing for our consultant time with three other live mandates. Retained at 22% means we run the search exclusively, with named candidate progress every 5 working days.",
            "Pivot the conversation: 'happy to talk fee, but first let's agree what success looks like — a 3-shortlist 6-week timeline with 90-day guarantee'. Move them from price to outcome.",
        ],
    },
    {
        "pattern": r"\b(?:not sure|still thinking|need to think|undecided|weighing|exploring options|in two minds)\b",
        "label":   "Candidate hesitation ('I'm not sure')",
        "angles":  [
            "Surface the real objection: 'I'm not sure' is usually one of three things — comp not enough to move, scope unclear, or counter-offer fear. Ask which, don't assume.",
            "Reframe the risk: at this point in market a passive candidate stays in role 18+ more months on average. The cost of declining is rarely zero — what's the cost of staying?",
            "Anchor on the work, not the package: 'tell me what would have to be true about this role for it to be obvious yes' — flushes scope/team/mandate concerns into the open.",
        ],
    },
    {
        "pattern": r"\b(?:counter[\s-]?offer|counter offered|matched my salary|matched the offer|stay)\b",
        "label":   "Candidate received a counter-offer",
        "angles":  [
            "Statistics defence: 80% of candidates who accept counter-offers leave within 12 months anyway (CIPD). The reasons they were looking don't get fixed by money.",
            "Trust defence: their employer just told them what they're really worth, but only because they were about to lose them. That's not a vote of confidence.",
            "Don't argue, ask: 'what would the next 6 months look like if you stayed?' — let them talk themselves into or out of it without you pushing.",
        ],
    },
    {
        "pattern": r"\b(?:budget|sign[\s-]?off|approval)\b.{0,50}\b(?:delayed|hold|pause|frozen|cancelled)\b",
        "label":   "Brief stalled on internal sign-off",
        "angles":  [
            "Audit the stall: name the specific person who needs to sign off and the specific question that hasn't been answered. 'Stalled' is usually one decision, not a brief problem.",
            "Offer a partial commitment: 'happy to do an exploratory longlist while you finalise — no fee until brief confirmed'. Buys 2-3 weeks of warmth without commercial risk.",
            "Reset urgency: 'we're seeing 3 other clients move on Head-of-Comms profiles this month — if you want first refusal on X profile, we can hold them for 10 days'. Manufactured scarcity that's actually true in this market.",
        ],
    },
    {
        "pattern": r"\b(?:already (?:using|working with|engaged with))\b.{0,30}\b(?:another|other|different) (?:agency|firm|search|recruiter)\b",
        "label":   "Client already using another search firm",
        "angles":  [
            "Compete on niche: VMA's comms-only specialism vs generalist exec search means we maintain a deeper bench in the function. Ask: 'how many comms-only retained briefs does your current partner run per year?'",
            "Offer a stalking horse: 'happy to run a confidential shadow shortlist at no upfront cost — if our 3 candidates beat theirs you switch, if not you've lost nothing'. Bold, but works in dead markets.",
            "Park for re-engagement: 'understood — when's their current 90-day window up? Let's get a date in for August'. Future-pipelines them rather than burning the lead now.",
        ],
    },
    {
        "pattern": r"\b(?:remote|hybrid|days in (?:the )?office|return to office|RTO|in[\s-]?person)\b",
        "label":   "Remote / hybrid friction",
        "angles":  [
            "Reset to outcomes: 'what does the role actually need in-person? Leadership presence is one answer, butt-in-seat is another'. Many CCOs over-spec days because policy is set above them.",
            "Reframe the candidate constraint: top comms talent has options. A '4-day in office' requirement at this seniority shrinks the pool by ~60% in our latest VMA bench data — flag the trade-off explicitly.",
            "Negotiate a graduated start: '3 days for first 90, drop to 2 once embedded'. Often the real concern is onboarding, not policy.",
        ],
    },
    {
        "pattern": r"\b(?:salary|comp|package|offer)\b.{0,40}\b(?:below|under|less than|short|gap)\b",
        "label":   "Comp gap between client offer and candidate ask",
        "angles":  [
            "Reverse the question: 'what's the cost of restart-the-search?' Typically 8-12 weeks + £8-15k internal-team time + lost momentum. £10k bump usually under-prices that.",
            "Reframe the £10k: 'over a 3-year tenure that's £278/month — what's the brand cost of a 6-month vacancy in comms right now?'",
            "Surface the candidate's real number: ask them point-blank what number gets them to yes today vs walk. Often £5k less than the stated ask if the work and team are right.",
        ],
    },
]

