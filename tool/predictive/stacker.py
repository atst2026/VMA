"""Group trigger events by company.

A Stack is the set of all events on the same company inside the same
rolling window. Stack depth drives the `stack_multiplier` in ranker.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from tool.predictive.detector import TriggerEvent


STACK_WINDOW_DAYS = 30


@dataclass
class Stack:
    company: str
    events: list[TriggerEvent] = field(default_factory=list)

    @property
    def depth(self) -> int:
        # Count distinct trigger types, not raw event count — multiple RNS
        # items about the same CEO change shouldn't inflate the stack.
        return len({e.trigger_key for e in self.events})

    @property
    def latest_date(self) -> datetime:
        return max((e.published for e in self.events),
                   default=datetime.now(timezone.utc))


def _normalise(name: str) -> str:
    s = (name or "").lower()
    for suffix in (" plc", " p.l.c.", " limited", " ltd", " group",
                   " holdings", " inc", " incorporated"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def stack(events: list[TriggerEvent]) -> list[Stack]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=STACK_WINDOW_DAYS)
    # Only events inside the window
    events = [e for e in events if e.published >= cutoff]
    by_co: dict[str, Stack] = {}
    for e in events:
        key = _normalise(e.company)
        if not key:
            continue
        s = by_co.get(key)
        if s is None:
            s = Stack(company=e.company)
            by_co[key] = s
        s.events.append(e)
    return list(by_co.values())
