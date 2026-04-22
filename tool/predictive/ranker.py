"""Rank stacked predictive signals.

    score = trigger_weight x stack_multiplier x company_tier x freshness
"""
from __future__ import annotations
from datetime import datetime, timezone

from tool.predictive import patterns as P
from tool.predictive.stacker import Stack
from tool.predictive.detector import TriggerEvent


def _stack_multiplier(depth: int) -> float:
    if depth >= 3:
        return 2.2
    if depth == 2:
        return 1.6
    return 1.0


def _tier_multiplier(events: list[TriggerEvent]) -> float:
    """Best tier across the stack wins."""
    tiers = {e.tier_hint for e in events}
    if "listed" in tiers:
        return 1.0
    if "covered" in tiers:
        return 0.9
    return 0.7


def _freshness(latest: datetime) -> float:
    """1.0 if most recent event <=7d ago; 0.8 if 7-21d; 0 if >21d."""
    hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
    if hours < 0:
        return 1.0
    if hours <= 24 * 7:
        return 1.0
    if hours <= 24 * 21:
        return 0.8
    return 0.0


def _trigger_weight_for_stack(events: list[TriggerEvent]) -> float:
    """When events stack, use the strongest trigger's weight as the base."""
    ws = []
    for e in events:
        trig = P.BY_KEY.get(e.trigger_key)
        if trig is not None:
            ws.append(trig.weight)
        elif e.trigger_key == "job_ad_cluster":
            ws.append(1.1)   # Highest-yield single signal per the ceiling PDF
    return max(ws) if ws else 0.0


def score_stack(stk: Stack) -> float:
    if not stk.events:
        return 0.0
    fresh = _freshness(stk.latest_date)
    if fresh == 0:
        return 0.0
    return round(
        _trigger_weight_for_stack(stk.events)
        * _stack_multiplier(stk.depth)
        * _tier_multiplier(stk.events)
        * fresh,
        3,
    )


def rank(stacks: list[Stack]) -> list[tuple[Stack, float]]:
    scored = [(s, score_stack(s)) for s in stacks]
    scored = [p for p in scored if p[1] > 0]
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored
