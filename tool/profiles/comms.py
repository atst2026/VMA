"""The Comms profile — senior in-house Communications recruitment.

These are the exact values that lived as module-level constants in
config.py before Phase 0. Moving them here changes nothing: config.py
re-exports them under the same names, so the comms brief is byte-for-byte
identical. The explanatory comments came across with the data — they're
hard-won institutional knowledge, not throwaway notes.
"""
from __future__ import annotations

from tool.profiles.base import Profile

# --- Scope ---
ROLE_KEYWORDS = (
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
)

# Hard-exclude. These titles are agency/sales client-service roles that Sara
# does not work (she places into in-house comms functions only). A match here
# scores 0 regardless of how well the title hits the role keywords.
EXCLUDE_TITLE_TERMS = (
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
    # Interim / fixed-term / temporary — OFF-PRODUCT. VMA's specialism is
    # Executive Search / Permanent Recruitment / Advisory, not interim
    # staffing. R1 removed the day-rate SALARY path; this also drops
    # interim/FTC/maternity-cover roles advertised WITHOUT a day rate
    # (e.g. "Interim Chief Communications Officer" on LinkedIn). Word-
    # boundary matched on the TITLE only (jobs._EXCLUDE_RE +
    # ranking._EXCLUDE_PATTERNS), so role bodies can't false-trip it.
    "interim", "temporary", "secondment", "secondee",
    "maternity cover", "maternity leave", "paternity cover",
    "mat cover", "mat leave", "fixed term", "fixed-term", "ftc",
    "month contract", "months contract",
)

# Role titles we surface even at lower seniority (kept tight to avoid noise).
# config.JOB_TITLE_KEYWORDS = ROLE_KEYWORDS + these.
EXTRA_JOB_TITLE_KEYWORDS = (
    "senior communications manager", "communications manager",
    "head of internal communications", "head of corporate communications",
    "head of external communications",
)

# Canonical job-search query set. ONE source of truth for the phrases the
# job lanes (Adzuna / LinkedIn-public / Bright Data) search for, so a
# query added here widens every lane at once. Previously each lane hard-
# coded ~4-6 phrases — under-covering the role taxonomy and capping
# Today's-Leads recall. Ordered most→least senior so budget-capped lanes
# (LinkedIn/Bright Data) take the highest-value slice first.
JOB_SEARCH_QUERIES = (
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
)

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
COMPANY_EXCLUDE = (
    "VMA Group", "VMAGROUP", "VMA Recruitment",
    # Competitor IC/Corp Comms recruiters
    "Hanson Search", "Sapience Communications", "Sapience",
    "Ellwood Atfield", "Reuben Sinclair", "CommsSearch",
    "PRfect Search", "Madigan Search", "Quill Recruitment",
    "Major Players",
    # Observed posting comms roles on Sara's behalf-of-a-hidden-client
    # (agency mandates, not direct employer briefs — off-product for an
    # exec-search firm). The generic agency-name regex in ranking.py
    # catches the "...Recruitment/Search/Resourcing/Staffing/Talent
    # Solutions..." long tail; these two are brand names it can't infer.
    "EquiTalent", "Harris Hill",
    # Generalist / sector recruiters confirmed leaking via Adzuna's
    # aggregator feed — they post client mandates, not their own roles.
    # ("Michael Page" as a substring also covers "Michael Page Marketing".)
    "Michael Page", "NFP People", "Not For Profit People", "SF Partners",
)

# --- Delivery ---
RECIPIENT = "stehrani@vmagroup.com"
TEST_RECIPIENT = "franc.laude1994@gmail.com"   # practice-run inbox (Gmail for reliable test delivery)


COMMS = Profile(
    key="comms",
    label="Comms",
    role_keywords=ROLE_KEYWORDS,
    exclude_title_terms=EXCLUDE_TITLE_TERMS,
    extra_job_title_keywords=EXTRA_JOB_TITLE_KEYWORDS,
    job_search_queries=JOB_SEARCH_QUERIES,
    salary_floor_perm_gbp=SALARY_FLOOR_PERM_GBP,
    company_exclude=COMPANY_EXCLUDE,
    recipient=RECIPIENT,
    test_recipient=TEST_RECIPIENT,
)
