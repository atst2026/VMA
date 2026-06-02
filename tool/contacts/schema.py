"""Data structures for the hiring-contacts table.

Confidence tiers are deliberately two, not three. Tier B ("likely but
unverified") is a trap — naming someone we can't verify is currently in role
is worse than a Recruiter search URL. Sara cold-opens to "Hi Jane" and Jane
left in February. So: VERIFIED within 120 days, or FALLBACK. Nothing in
between.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

# Canonical role-slot keys stored per company. The routing layer maps
# trigger keys -> ordered priority lists over these slots.
ROLE_SLOTS = (
    "ceo",
    "chair",
    "cfo",
    "cco",                       # Chief Comms Officer / CCO
    "chro",
    "gc",                        # General Counsel
    "head_of_comms",
    "head_of_corporate_affairs",
    "head_of_ic",                # Head of Internal Communications
    "ir_director",
)

# Maximum age of a contact entry before it's considered stale. Beyond
# this, we surface the Recruiter-search fallback instead of the named
# contact (and the entry is queued for re-verification).
FRESHNESS_DAYS = 120

# Minimum stored confidence for an entry to surface as a NAMED contact.
# Below this, the entry is treated like a miss and the UI falls back to a
# role-search ("verified or fallback" — naming a weak guess is worse than a
# search). Set conservatively: the live curated graph floors at 0.70, and
# Companies House / RNS / auto-populated entries are 0.85–0.92, so 0.70
# keeps every genuinely-sourced contact and drops only the speculative
# single-scrape sources (e.g. bright_data at 0.55). Tighten via feedback.
MIN_NAMED_CONFIDENCE = 0.70

# How long to wait before retrying an entity that's failed 3 resolution
# attempts in a row. Distinct from CACHE_TTL_DAYS in linkedin_resolver.
COOL_OFF_DAYS = 30
MAX_ATTEMPTS_BEFORE_COOL_OFF = 3


class ResolutionStatus:
    """Outcome enum used by the resolver. Only certain values increment
    the attempt counter on the re-verify queue — see reverify.py."""
    RESOLVED_VERIFIED = "resolved_verified"
    RESOLVED_NO_MATCH = "resolved_no_match"
    RESOLVED_STALE = "resolved_stale"
    RESOLVED_UNMATCHABLE = "resolved_unmatchable"
    SKIPPED_RATE_LIMIT = "skipped_rate_limit"
    SKIPPED_BUDGET = "skipped_budget"
    SKIPPED_COOL_OFF = "skipped_cool_off"

    # Statuses that count as an actual attempt against the queue cap
    COUNTS_AS_ATTEMPT = frozenset({
        "resolved_no_match",
        "resolved_stale",
        "resolved_unmatchable",
    })


@dataclass
class ContactEntry:
    """One named person in one role at one company."""
    name: str
    role_title: str              # The actual title at this employer
    role_slot: str               # Which of ROLE_SLOTS this fills
    linkedin_url: str | None = None
    source_url: str = ""         # Where we found them (leadership page, CH, RNS)
    source_label: str = ""       # Human-readable, e.g. "Severn Trent leadership page"
    tenure_start: str | None = None   # ISO date if known
    verified_at: str = ""        # ISO timestamp when last verified
    confidence: float = 0.0      # 0-1, set by resolver

    def is_fresh(self, as_of: datetime | None = None) -> bool:
        if not self.verified_at:
            return False
        try:
            v = datetime.fromisoformat(self.verified_at)
        except Exception:
            return False
        now = as_of or datetime.now(timezone.utc)
        # Coerce naive timestamps (hand-edited / externally-written entries)
        # to UTC so a tz-aware minus tz-naive subtraction can't raise. The
        # earlier try/except wrapped only the parse, not this subtraction,
        # so a naive verified_at used to throw an uncaught TypeError and
        # crash the caller (resolve_lead_contact / best_named_contact).
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        try:
            return (now - v) < timedelta(days=FRESHNESS_DAYS)
        except Exception:
            return False

    def meets_named_confidence(self) -> bool:
        """True if this entry is strong enough to surface as a NAMED
        contact (vs falling back to a role-search). The single, shared
        named-tier gate used by every runtime reader."""
        return (self.confidence or 0.0) >= MIN_NAMED_CONFIDENCE


@dataclass
class ContactCard:
    """The full contact set for one company — one entry per role slot, or
    None if that slot has never been resolved at this employer."""
    company: str
    entries: dict[str, ContactEntry] = field(default_factory=dict)
    last_seeded_at: str = ""
    # Optional per-company structure hint that reorders slot priority
    # for comms vacancies. Suggested values: "" (default — CCO-led),
    # "chro_led" (comms reports into HR), "corp_affairs_led"
    # (Head of Corporate Affairs is the senior seat). Populated by hand
    # for Tier-A; the resolver consults it before falling back to the
    # generic seniority-up rule.
    structure: str = ""

    def get(self, role_slot: str) -> ContactEntry | None:
        return self.entries.get(role_slot)

    def to_jsonable(self) -> dict:
        return {
            "company": self.company,
            "last_seeded_at": self.last_seeded_at,
            "structure": self.structure,
            "entries": {k: asdict(v) for k, v in self.entries.items()},
        }

    @classmethod
    def from_jsonable(cls, d: dict) -> "ContactCard":
        entries = {
            k: ContactEntry(**v) for k, v in (d.get("entries") or {}).items()
        }
        return cls(
            company=d.get("company", ""),
            entries=entries,
            last_seeded_at=d.get("last_seeded_at", ""),
            structure=d.get("structure", ""),
        )


@dataclass
class CandidateRecord:
    """One candidate the resolver considered. Stored on the resolution log
    so we can tell ranking bugs ("right answer at #2") from recall bugs
    ("right answer never considered") when Sara corrects an entry."""
    name: str
    role_title: str
    confidence: float
    source: str                  # which source produced this candidate
    linkedin_url: str | None = None


@dataclass
class SourceQuery:
    """One source consulted during a resolution attempt."""
    source: str                  # 'companies_house' / 'rns' / 'leadership_page' / 'bright_data_linkedin'
    returned_data: bool          # did the source produce any record at all?
    used: bool = False           # did we pick a candidate from this source?
    reason: str = ""             # short explanation when not used


@dataclass
class ResolutionRecord:
    """Append-only log entry. One per resolution attempt (seed, re-verify,
    Sara correction). Stored as jsonl so it never overwrites and grows
    cheaply."""
    timestamp: str
    company: str
    role_slot: str
    role_title_query: str        # what role we were trying to fill
    outcome: str                 # ResolutionStatus value
    picked_name: str | None = None
    picked_url: str | None = None
    confidence: float = 0.0
    candidates_considered: list[CandidateRecord] = field(default_factory=list)
    sources_queried: list[SourceQuery] = field(default_factory=list)
    notes: str = ""              # free-text, e.g. correction reason from Sara

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "company": self.company,
            "role_slot": self.role_slot,
            "role_title_query": self.role_title_query,
            "outcome": self.outcome,
            "picked_name": self.picked_name,
            "picked_url": self.picked_url,
            "confidence": self.confidence,
            "candidates_considered": [asdict(c) for c in self.candidates_considered],
            "sources_queried": [asdict(s) for s in self.sources_queried],
            "notes": self.notes,
        }


@dataclass
class ReverifyEntry:
    """One pending re-verification job. Lives in
    state/contact_reverify_queue.json. Consumed at the top of each
    nightly run; popped on RESOLVED_VERIFIED, incremented on actual
    failed attempts (not on queue-and-skip events)."""
    company: str
    role_slot: str
    queued_at: str
    attempts: int = 0
    last_attempt_at: str = ""
    last_failure_reason: str = ""
    cool_off_until: str = ""     # ISO; entry is skipped until this passes
