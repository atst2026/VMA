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
from tool.profiles.marketing import MARKETING

log = logging.getLogger("brief.profiles")

DEFAULT_PROFILE_KEY = "comms"

# Registry of every available specialism. Registering a profile here is all
# it takes to make `VMA_PROFILE=<key>` and the landing-page door light up.
_REGISTRY: dict[str, Profile] = {
    COMMS.key: COMMS,
    MARKETING.key: MARKETING,
}

# Specialisms that are announced but not yet live. The landing-page chooser
# shows these as "coming soon" doors so the split is visible before the
# profile exists. Move an entry into _REGISTRY (and delete it here) the
# moment its profile is authored. (Marketing is now live — see above.)
UPCOMING_PROFILES: list[tuple[str, str]] = []


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
    """The active profile.

    Resolution order:
      1. A per-REQUEST override (``flask.g.vma_profile``) — this is how the
         single dashboard process serves BOTH desks (one URL → /comms, the
         other → /marketing). Only consulted inside a live web request.
      2. ``VMA_PROFILE`` env var — how a single-profile process (the brief,
         or a per-profile deploy) pins itself.
      3. Default (comms).
    """
    # 1. request-scoped override (dashboard serving multiple desks)
    try:
        from flask import g, has_request_context
        if has_request_context():
            key = getattr(g, "vma_profile", None)
            if key:
                p = _REGISTRY.get(str(key).strip().lower())
                if p is not None:
                    return p
    except Exception:
        pass
    # 2/3. env var, else default
    key = (os.environ.get("VMA_PROFILE") or DEFAULT_PROFILE_KEY).strip().lower()
    p = _REGISTRY.get(key)
    if p is None:
        log.info("VMA_PROFILE=%r not registered — falling back to %r",
                 key, DEFAULT_PROFILE_KEY)
        return _REGISTRY[DEFAULT_PROFILE_KEY]
    return p
