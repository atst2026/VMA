"""The Profile dataclass — one specialism's worth of tunable settings.

A *profile* is everything that makes the engine recruit for a particular
specialism (Comms today; Marketing next). The scour → filter → rank →
render → deliver engine is profile-agnostic: it reads these fields and
behaves accordingly. Adding a new specialism is therefore a new Profile
instance (a data file), not a code change.

Phase 0 migrates the role taxonomy + delivery identity into here. Later
phases grow this surface (sector weights, role-title regexes, trade-press
feed selection, contact routing, calendar windows) as the marketing
values that populate them are confirmed — see PROFILES.md.

Fields are tuples (immutable) so a profile can't be mutated at runtime;
config.py converts the ones it re-exports back to the list/int/str shapes
the rest of the codebase already expects, so nothing downstream changes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    # --- Identity -------------------------------------------------------
    key: str            # url/env slug, e.g. "comms", "marketing"
    label: str          # human label for the landing page, e.g. "Comms"

    # --- Role taxonomy --------------------------------------------------
    role_keywords: tuple[str, ...]
    exclude_title_terms: tuple[str, ...]
    # Lower-seniority titles surfaced ON TOP of role_keywords (kept tight
    # to avoid noise). config.JOB_TITLE_KEYWORDS = role_keywords + these.
    extra_job_title_keywords: tuple[str, ...]
    # Canonical job-board search phrases (one source of truth for every
    # job lane). Ordered most→least senior so budget-capped lanes take
    # the highest-value slice first.
    job_search_queries: tuple[str, ...]

    # --- Filters --------------------------------------------------------
    salary_floor_perm_gbp: int
    # Employers whose jobs must NEVER appear (own employer + direct
    # competitor search firms in this specialism).
    company_exclude: tuple[str, ...]

    # --- Delivery -------------------------------------------------------
    recipient: str          # live brief recipient
    test_recipient: str     # practice-run inbox
