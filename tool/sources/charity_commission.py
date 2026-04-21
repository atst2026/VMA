"""UK Charity Commission — new trustee appointments (portfolio-building signal)."""
from __future__ import annotations
import logging

from tool.sources._http import get, signal_id

log = logging.getLogger("brief.charity")


def fetch_all() -> list[dict]:
    """Charity Commission publishes an atom-like feed of register updates.
    v1 stub: the authoritative API requires a registered API key (free but
    per-partner). We'll supply the search URL Sara can check manually and
    skip automated pulls until she's registered.
    """
    # Intentional no-op at v1 — flagged in the brief's "sources attempted" list.
    return []
