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


# Broader-market (off-watchlist) discount. A genuine lead at an employer
# outside the curated ~550-name watchlist is admitted (recall), but scores
# below an equivalent core-watchlist account so the latter always ranks
# first. A stack counts as watchlist if ANY of its events resolved to a
# curated account.
OFF_WATCHLIST_MULT = 0.6


def _account_multiplier(events: list[TriggerEvent]) -> float:
    for e in events:
        if getattr(e, "account_tier", "watchlist") == "watchlist":
            return 1.0
    return OFF_WATCHLIST_MULT


def _freshness(latest: datetime) -> float:
    """Daily mode: 1.0 if <=7d; 0.8 if 7-21d; 0 if >21d.
    Sweep mode (VMA_SWEEP_DAYS=14): 1.0 across the whole window so older
    events in the look-back aren't penalised away.
    """
    from tool.config import sweep_days
    days = sweep_days()
    hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
    if hours < 0:
        return 1.0
    if days > 1:
        # Sweep: full credit anywhere in the window, 0 outside
        return 1.0 if hours <= 24 * (days + 7) else 0.0
    # Daily mode (original curve)
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


def _geo_multiplier(company: str) -> float:
    """UK companies score full weight; international peers score 60% so
    they appear in the pipeline but rank below UK at equal trigger weight."""
    try:
        from tool.peers import SECTOR_PEERS
        intl = set(p.lower() for p in SECTOR_PEERS.get("international", []))
        if company.lower() in intl:
            return 0.6
    except Exception:
        pass
    return 1.0


def score_stack(stk: Stack) -> float:
    if not stk.events:
        return 0.0
    fresh = _freshness(stk.latest_date)
    if fresh == 0:
        return 0.0
    from tool.peers import sector_heat_multiplier
    return round(
        _trigger_weight_for_stack(stk.events)
        * _stack_multiplier(stk.depth)
        * _tier_multiplier(stk.events)
        * _account_multiplier(stk.events)
        * _geo_multiplier(stk.company)
        * sector_heat_multiplier(stk.company)
        * fresh,
        3,
    )


def rank(stacks: list[Stack]) -> list[tuple[Stack, float]]:
    scored = [(s, score_stack(s)) for s in stacks]
    scored = [p for p in scored if p[1] > 0]
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored
