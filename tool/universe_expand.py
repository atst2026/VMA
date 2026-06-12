"""Universe expansion — proposals, never silent additions.

The watchlist is static: companies signalling need that were never
seeded can never become leads, however loud their signals. Once a week
this module shows the model (a) a sample of the current universe and
(b) companies recently seen in the engine's own signal stream that
FAILED watchlist resolution — each with the headline that surfaced
them — and asks which belong on a UK senior comms/marketing BD
watchlist, with a one-line case each.

The output is a PROPOSAL file the AD approves by hand (additions go
into peers.py / the hiring-contacts seeds): the model never widens the
universe by itself. Fresh proposals render as a note on the engine
page. Graceful no-op without ANTHROPIC_API_KEY; runs at most once per
RUN_EVERY_DAYS. Never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.universe_expand")

MODEL = "claude-opus-4-8"
RUN_EVERY_DAYS = 7
MAX_CANDIDATES = 80
MAX_PROPOSALS = 10
PROPOSAL_FRESH_DAYS = 7

_SYSTEM = (
    "You curate the account universe for a UK senior communications & "
    "marketing recruitment desk. You receive a sample of the CURRENT "
    "watchlist, then candidate companies recently seen in the desk's own "
    "signal stream that are NOT on the watchlist, each with the headline "
    "that surfaced it.\n\n"
    "Propose ONLY companies that genuinely belong: UK-based or with a "
    "substantial UK operation, large enough to employ senior in-house "
    "communications/marketing leadership, and plausibly able to pay a "
    "retained search fee. Exclude: foreign companies with no UK comms "
    "function, tiny businesses, public bodies that procure differently, "
    "news outlets/agencies/consultancies (conflicts), and anything you "
    "cannot identify confidently from the name. Fewer, better proposals "
    "beat a long list — every proposal costs an Account Director review "
    "time. case: one sentence on why this company belongs."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "case": {"type": "string"},
                },
                "required": ["company", "case"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


def _file():
    return state_dir() / "universe_proposals.json"


def _load() -> dict:
    try:
        f = _file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d, indent=1))
    except Exception:
        pass


def _call_model(content: str) -> dict | None:
    import os
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema",
                                      "schema": _SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("universe expansion model call failed: %s", e)
        return None


def candidates_from_signals(signals: list[dict]) -> list[tuple[str, str]]:
    """(company, headline) pairs from the signal stream that fail
    watchlist resolution — job-ad employers and named news subjects."""
    from tool.account_match import classify_account
    out, seen = [], set()
    for s in signals or []:
        if not isinstance(s, dict):
            continue
        company = (s.get("company") or "").strip()
        title = (s.get("title") or "").strip()
        if not company or not title or len(company) < 3:
            continue
        key = company.lower()
        if key in seen:
            continue
        resolved, tier = classify_account(company, title)
        if resolved and tier == "watchlist":
            continue                      # already in the universe
        seen.add(key)
        out.append((company, title))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def run(signals: list[dict], call=None, now: datetime | None = None) -> int:
    """Weekly proposal pass. Returns the number of proposals stored
    (0 when skipped/no-op). Never raises."""
    try:
        from tool.config import model_spend_allowed
        if not model_spend_allowed("optional"):
            log.info("%s skipped: VMA_MODEL_SPEND=contacts", "universe expansion")
            return 0
    except Exception:
        pass
    try:
        call = call or _call_model
        now = now or datetime.now(timezone.utc)
        store = _load()
        try:
            last = datetime.fromisoformat(store.get("last_run") or "")
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(days=RUN_EVERY_DAYS):
                return 0
        except ValueError:
            pass

        cands = candidates_from_signals(signals)
        if not cands:
            return 0
        from tool.account_match import _load_watchlist_names
        sample = _load_watchlist_names()[:120]
        content = ("CURRENT WATCHLIST (sample):\n" + ", ".join(sample)
                   + "\n\nCANDIDATES (company — headline that surfaced it):\n"
                   + "\n".join(f"- {c} — {t[:140]}" for c, t in cands))
        data = call(content)
        store["last_run"] = now.isoformat()
        if not isinstance(data, dict):
            _save(store)
            return 0
        proposals = []
        for p in (data.get("proposals") or [])[:MAX_PROPOSALS]:
            if (isinstance(p, dict) and (p.get("company") or "").strip()
                    and (p.get("case") or "").strip()):
                proposals.append({"company": p["company"].strip()[:80],
                                  "case": p["case"].strip()[:200]})
        store["proposals"] = proposals
        store["proposed_at"] = now.isoformat()
        _save(store)
        log.info("universe expansion: %d proposals from %d candidates",
                 len(proposals), len(cands))
        return len(proposals)
    except Exception as e:
        log.info("universe expansion skipped (%s)", e)
        return 0


def fresh_proposals(now: datetime | None = None) -> list[dict]:
    """Proposals from the last PROPOSAL_FRESH_DAYS, for the engine-page
    note. Never raises."""
    try:
        store = _load()
        at = datetime.fromisoformat(store.get("proposed_at") or "")
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        if now - at > timedelta(days=PROPOSAL_FRESH_DAYS):
            return []
        return store.get("proposals") or []
    except Exception:
        return []
