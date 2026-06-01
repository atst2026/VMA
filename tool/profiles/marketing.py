"""The Marketing profile — senior in-house Marketing & Brand recruitment.

⚠ FIRST DRAFT. These lists are seeded from general UK marketing-leadership
recruitment knowledge so the Marketing desk works end-to-end today. They are
the things to review with the marketing team and tune over the first couple
of weeks — exactly as the comms taxonomy was tuned. Editing this one file
re-tunes the whole Marketing desk; no engine changes needed.

Delivery: for now the recipient is Sara's address (placeholder until a
marketing recruiter is set up). The daily brief email is disabled globally
anyway (config.MORNING_BRIEF_EMAIL_ENABLED) — the dashboard is the surface.
"""
from __future__ import annotations

from tool.profiles.base import Profile

# --- Scope ---
# NB: bare "cmo" is deliberately NOT a keyword — in pharma/health it means
# Chief *Medical* Officer (and in manufacturing, Contract Manufacturing Org).
# We match "chief marketing officer" in full and hard-exclude the collisions
# below, mirroring how the comms profile handles the "CCO" ambiguity.
ROLE_KEYWORDS = (
    # Top of house
    "chief marketing officer", "chief brand officer", "chief growth officer",
    "chief marketing and growth officer", "chief customer officer",
    # Marketing leadership
    "marketing director", "director of marketing", "head of marketing",
    "vp marketing", "vice president of marketing", "group marketing director",
    "marketing and communications director", "head of marketing communications",
    # Brand
    "brand director", "head of brand", "director of brand", "brand marketing director",
    "head of brand marketing",
    # Growth / performance / digital
    "head of growth", "growth marketing", "vp growth", "head of digital marketing",
    "digital marketing director", "head of performance marketing",
    "performance marketing director", "head of acquisition", "head of paid media",
    # Product / lifecycle / CRM / ecommerce
    "product marketing director", "head of product marketing",
    "head of demand generation", "demand generation",
    "head of customer marketing", "lifecycle marketing", "head of crm",
    "crm director", "head of ecommerce", "ecommerce director", "director of ecommerce",
    # Campaigns / content
    "campaign director", "head of campaigns", "head of content",
)

# Hard-exclude. Agency/sales client-service titles VMA does not place into,
# the CMO disambiguations (Medical / Manufacturing), and (as for comms)
# interim/FTC since VMA's specialism is permanent / executive search.
EXCLUDE_TITLE_TERMS = (
    # Agency client-service
    "account director", "senior account director", "group account director",
    "board account director", "account supervisor",
    "client services", "client director", "client partner", "client lead",
    # Sales / martech sales (a frequent false-positive for "marketing" ads)
    "account executive", "account manager", "technical account",
    "account representative", "partner account", "named account",
    "renewal account", "renewals account", "enterprise account", "sales account",
    "sales director", "business development", "bd director", "sdr", "bdr",
    # CMO collisions — bare "CMO" is excluded from ROLE_KEYWORDS; belt-and-
    # braces against the medical / manufacturing senses leaking in.
    "chief medical officer", "chief manufacturing officer",
    "contract manufacturing", "medical officer",
    # Interim / fixed-term / temporary — OFF-PRODUCT (perm / exec search).
    "interim", "temporary", "secondment", "secondee",
    "maternity cover", "maternity leave", "paternity cover",
    "mat cover", "mat leave", "fixed term", "fixed-term", "ftc",
    "month contract", "months contract",
)

# Lower-seniority titles surfaced on top of ROLE_KEYWORDS (kept tight).
EXTRA_JOB_TITLE_KEYWORDS = (
    "senior marketing manager", "marketing manager", "brand manager",
    "senior brand manager", "digital marketing manager",
    "product marketing manager", "growth manager", "ecommerce manager",
)

# Canonical job-board search phrases, most → least senior.
JOB_SEARCH_QUERIES = (
    "chief marketing officer",
    "chief brand officer",
    "marketing director",
    "director of marketing",
    "head of marketing",
    "vp marketing",
    "group marketing director",
    "brand director",
    "head of brand",
    "head of growth",
    "growth marketing director",
    "head of digital marketing",
    "digital marketing director",
    "head of performance marketing",
    "head of product marketing",
    "product marketing director",
    "head of demand generation",
    "head of customer marketing",
    "crm director",
    "ecommerce director",
    "head of marketing communications",
)

# --- Filters ---
SALARY_FLOOR_PERM_GBP = 40_000

# Companies whose jobs should NEVER appear: VMA itself + competitor
# marketing / digital / creative search firms. FIRST DRAFT — confirm the
# real competitor set with the marketing team.
COMPANY_EXCLUDE = (
    "VMA Group", "VMAGROUP", "VMA Recruitment",
    # Marketing / brand / digital / creative search & recruitment firms
    "Grace Blue", "The Talent Set", "Stonor Search", "Brand Recruitment",
    "Better Placed", "Forward Role", "EMR", "Major Players", "Aspire",
    "Gravitas Recruitment", "Salt", "Blu Digital", "Intelligent People",
    "Henry Nicholas", "Tonic Talent", "We Are Adam", "Michael Page",
    "Direct Recruitment", "Cathcart Associates",
)

# --- Delivery ---
# Sara's address for now (placeholder recipient). Daily brief email is
# disabled globally regardless — see config.MORNING_BRIEF_EMAIL_ENABLED.
RECIPIENT = "stehrani@vmagroup.com"
TEST_RECIPIENT = "amirt12@hotmail.com"   # practice-run inbox


MARKETING = Profile(
    key="marketing",
    label="Marketing",
    role_keywords=ROLE_KEYWORDS,
    exclude_title_terms=EXCLUDE_TITLE_TERMS,
    extra_job_title_keywords=EXTRA_JOB_TITLE_KEYWORDS,
    job_search_queries=JOB_SEARCH_QUERIES,
    salary_floor_perm_gbp=SALARY_FLOOR_PERM_GBP,
    company_exclude=COMPANY_EXCLUDE,
    recipient=RECIPIENT,
    test_recipient=TEST_RECIPIENT,
)
