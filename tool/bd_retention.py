"""Unified BD-Leads dashboard retention.

Every BD lead in the radar's BD panels — a predictor seat, a funding
round, or a vacated-seat / cascade move — is removed from the dashboard a
fixed number of days after it was FIRST PRESENTED there (its first_seen /
detected_at anchor):

  * default            -> BD_RETENTION_DAYS (30) after it was presented
  * status=followed_up -> BD_FOLLOWED_UP_RETENTION_DAYS (90) after it was
                          presented

Followed-up leads get the longer window because they're Sara's active
working record; once that window lapses they clear too, so no BD panel
grows without bound. A dismissed lead clears on the default 30-day clock
like any other non-followed-up lead.

This single rule is shared by every BD engine (predictor_pipeline,
cascade, funding_round) and applied on BOTH the server-side prune (so the
persisted state files stay bounded) and the dashboard read path (so a
lapsed lead disappears from every tab — Active / New today / Followed up
/ Dismissed / All — even before the next morning brief prunes it).

State is isolated per profile (see tool/state_paths), so the same rule
governs the communications and marketing radars without any per-profile
branching here. Live Jobs keep their own, shorter 7-day rule (see
tool/lead_first_seen); this module is BD-only.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Days a BD lead stays on the dashboard after it's first presented.
BD_RETENTION_DAYS = 30
# Followed-up leads (Sara's working record) get the longer window.
BD_FOLLOWED_UP_RETENTION_DAYS = 90


def window_days(status: str | None) -> int:
    """Retention window for a lead with the given triage status."""
    return (BD_FOLLOWED_UP_RETENTION_DAYS
            if status == "followed_up"
            else BD_RETENTION_DAYS)


def is_expired(presented_iso: str | None,
               status: str | None,
               now: datetime | None = None) -> bool:
    """True if a BD lead first presented at ``presented_iso`` has passed
    its retention window for the given triage ``status``.

    The window is BD_RETENTION_DAYS for everything except a followed-up
    lead, which gets BD_FOLLOWED_UP_RETENTION_DAYS. Removal is inclusive
    of the boundary — a lead presented exactly ``window`` days ago is
    expired — matching the Live Jobs rule (``>= RETENTION_DAYS``).

    Fail-safe: a missing or unparseable anchor returns False (never hide
    a lead on bad/absent data — let it ride until it has a real anchor).
    A naive timestamp is treated as UTC.
    """
    if not presented_iso:
        return False
    try:
        anchor = datetime.fromisoformat(presented_iso)
    except (ValueError, TypeError):
        return False
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - anchor).days >= window_days(status)
