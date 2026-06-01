"""Profile registry + active-profile resolution.

One engine, many specialisms. The active profile is chosen by the
``VMA_PROFILE`` environment variable (default ``comms``), so the morning
brief, the dashboard, and any tool can all run a given specialism by
setting one env var — no code change, no fork.

    VMA_PROFILE=comms      python -m tool.morning_brief   # today's behaviour
    VMA_PROFILE=marketing  python -m tool.morning_brief   # once added

Resolution is forgiving: an unknown or empty value falls back to the
default profile rather than raising, so a typo never breaks a run.
"""
from __future__ import annotations

import logging
import os

from tool.profiles.base import Profile
from tool.profiles.comms import COMMS

log = logging.getLogger("brief.profiles")

DEFAULT_PROFILE_KEY = "comms"

# Registry of every available specialism. Marketing slots in here once its
# profile is authored (Phase 2) — that single line is all it takes to make
# `VMA_PROFILE=marketing` and the landing-page door light up.
_REGISTRY: dict[str, Profile] = {
    COMMS.key: COMMS,
}


def all_profiles() -> list[Profile]:
    """Every registered profile, in registration order (drives the
    landing-page door list)."""
    return list(_REGISTRY.values())


def get_profile(key: str | None) -> Profile:
    """Look a profile up by key; fall back to the default on miss."""
    if key:
        p = _REGISTRY.get(key.strip().lower())
        if p is not None:
            return p
    return _REGISTRY[DEFAULT_PROFILE_KEY]


def active_profile() -> Profile:
    """The profile selected by VMA_PROFILE (default: comms)."""
    key = (os.environ.get("VMA_PROFILE") or DEFAULT_PROFILE_KEY).strip().lower()
    p = _REGISTRY.get(key)
    if p is None:
        log.info("VMA_PROFILE=%r not registered — falling back to %r",
                 key, DEFAULT_PROFILE_KEY)
        return _REGISTRY[DEFAULT_PROFILE_KEY]
    return p
