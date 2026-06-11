"""Call ammo — the sector insight the AD promises on the call.

The opener's offer is "I can tell you what I'm seeing in your sector".
This module makes that promise real: 2-3 concrete, public, citable
observations per lead, assembled from what the engine already watches —
live trigger activity across the company's peer set (the predictor
pipeline) topped up with the curated sector demand-drivers. The AD
walks into the conversation with the goods, without the engine ever
revealing what it knows about the target company itself.

Pure reads; never raises; never blank for a recognisable sector.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("brief.call_ammo")

PEER_WINDOW_DAYS = 90


def _date(iso: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _month(iso: str | None) -> str:
    d = _date(iso)
    return d.strftime("%b") if d else ""


# One pipeline read per render pass, not one per row.
_PIPE_CACHE: dict = {"at": 0.0, "data": None}


def _predictors() -> dict:
    import time
    now = time.time()
    if _PIPE_CACHE["data"] is None or now - _PIPE_CACHE["at"] > 60:
        from tool.predictor_pipeline import load_pipeline
        _PIPE_CACHE["data"] = (load_pipeline().get("predictors") or {})
        _PIPE_CACHE["at"] = now
    return _PIPE_CACHE["data"]


def sector_insights(company: str | None, desk_key: str = "comms",
                    limit: int = 3) -> list[str]:
    """2-3 sector observations the AD can give away on the call. Live
    peer-set trigger activity first (specific, dated, public), curated
    sector demand-drivers to fill. Never raises."""
    out: list[str] = []
    company = (company or "").strip()
    try:
        from tool import peers as P
        sector = P.detect_sector(company)
        peer_names, _src = P.peers_for(company)
        peers_norm = {(p or "").strip().lower() for p in (peer_names or [])}
        peers_norm.discard(company.lower())

        # ---- live peer activity from the predictor pipeline ----------
        if peers_norm:
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=PEER_WINDOW_DAYS)
            moves: list[tuple[str, str, str]] = []   # (peer, label, when)
            for e in _predictors().values():
                co = (e.get("company") or "").strip()
                if co.lower() not in peers_norm:
                    continue
                for ev in (e.get("events") or []):
                    if not isinstance(ev, dict):
                        continue
                    d = _date(ev.get("published"))
                    lbl = (ev.get("trigger_label") or "").strip()
                    if d and lbl and d >= cutoff:
                        moves.append((co, lbl, ev.get("published")))
            if moves:
                moves.sort(key=lambda m: m[2] or "", reverse=True)
                # one bullet per peer company, freshest first
                seen_cos: set[str] = set()
                named = []
                for co, lbl, when in moves:
                    if co in seen_cos:
                        continue
                    seen_cos.add(co)
                    named.append(f"{co} ({lbl.lower()}, {_month(when)})")
                    if len(named) == 2:
                        break
                out.append(
                    "Live in your peer set: " + "; ".join(named)
                    + f" — {len(moves)} senior-team trigger"
                    + ("s" if len(moves) != 1 else "")
                    + f" across the group in the last {PEER_WINDOW_DAYS} "
                      "days.")

        # ---- curated sector demand-drivers to fill ---------------------
        from tool import sector_context as SC
        try:
            from tool.peers import _affinity_key_for
            key = _affinity_key_for(company) or sector
        except Exception:
            key = sector
        for line in (SC.strategic_context(key, desk_key) or []):
            if len(out) >= limit:
                break
            out.append(line)
    except Exception as e:
        log.info("call_ammo skipped (%s)", e)
    return out[:limit]
