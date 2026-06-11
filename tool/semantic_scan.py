"""Semantic signal reader — the model reads what the regex cannot.

The daily sweep detects triggers by keyword patterns over headlines. A
chief executive saying "we're investing heavily in our brand this year",
a results statement promising "strengthened stakeholder engagement", an
acquisition implying an integration comms job — none contain the magic
phrases, so none become leads. This module hands the day's UNMATCHED
news headlines to Claude once per brief and asks one question per item:
does this indicate a likely senior comms/marketing hiring need at an
identifiable company within ~two quarters?

Scoring stays FROZEN: the model never invents a trigger class or a
weight — it maps each find onto an EXISTING trigger key, so a semantic
find is just a new detector feeding the same priced taxonomy (the same
category as a new RSS feed). Every event it emits is labelled
"AI read:" with the model's one-line rationale as evidence, and the
account gate (classify_account, watchlist only) still owns precision.

Graceful no-op without ANTHROPIC_API_KEY. One batched model call per
run, capped at MAX_ITEMS headlines; each signal id is read at most once
ever (seen-cache). Never raises.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.semantic_scan")

MODEL = "claude-opus-4-8"
MAX_ITEMS = 150
SEEN_CAP = 6000

# The ONLY trigger classes the model may map onto — all existing, all
# already weighted. "none" rejects the item.
ALLOWED_KEYS = [
    "ceo_change", "cfo_change", "chro_change", "cmo_change", "chair_change",
    "comms_leader_departure", "ir_director_change", "mna", "pe_acquisition",
    "ipo_listing", "secured_financing", "funding", "ownership_change",
    "restructure", "redundancy", "crisis_event", "regulator_action",
    "profit_warning", "contract_loss", "market_entry", "rebrand", "none",
]

_SYSTEM = (
    "You are the signal reader for a UK senior communications & marketing "
    "recruitment desk. You receive one news headline per line, each "
    "prefixed by an index and its source. For each, decide whether it "
    "indicates that an identifiable company is likely to need SENIOR "
    "communications, corporate affairs, investor relations or marketing "
    "capability within the next two quarters.\n\n"
    "Rules:\n"
    "- Only return items you are genuinely confident about; silence beats "
    "noise — a recruiter will phone these companies.\n"
    "- company must be the company with the NEED (never the newspaper, "
    "agency or analyst quoted).\n"
    "- trigger_key must be the closest match from the allowed list; use "
    "'none' (or omit the item) when nothing fits.\n"
    "- rationale: one short sentence an Account Director can read — what "
    "the headline implies about comms/marketing demand.\n"
    "- Headlines that merely mention a company without implying "
    "organisational change, growth, distress or reputational pressure "
    "are 'none'."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "leads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "company": {"type": "string"},
                    "trigger_key": {"type": "string", "enum": ALLOWED_KEYS},
                    "confidence": {"type": "string",
                                   "enum": ["high", "medium", "low"]},
                    "rationale": {"type": "string"},
                },
                "required": ["index", "company", "trigger_key",
                             "confidence", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["leads"],
    "additionalProperties": False,
}


def _seen_file():
    return state_dir() / "semantic_scan_seen.json"


def _load_seen() -> dict:
    try:
        f = _seen_file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save_seen(d: dict) -> None:
    try:
        if len(d) > SEEN_CAP:
            # keep the newest entries
            d = dict(sorted(d.items(), key=lambda kv: kv[1])[-SEEN_CAP:])
        f = _seen_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d))
    except Exception:
        pass


def _call_model(lines: list[str]) -> dict | None:
    """One batched Messages API call; returns the parsed JSON dict or
    None. Isolated so tests inject a stub."""
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
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_config={"format": {"type": "json_schema",
                                      "schema": _SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            log.info("semantic scan: model declined the batch")
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("semantic scan model call failed: %s", e)
        return None


def detect(signals: list[dict], call=None) -> list:
    """Read the day's unscanned news headlines; return TriggerEvents for
    confident, watchlist-resolved finds. Never raises."""
    try:
        from tool.account_match import classify_account
        from tool.predictive.detector import TriggerEvent
        from tool.predictive.patterns import BY_KEY

        call = call or _call_model
        seen = _load_seen()
        now = datetime.now(timezone.utc)

        batch: list[dict] = []
        for s in signals or []:
            if not isinstance(s, dict):
                continue
            sid = s.get("id") or s.get("url") or ""
            title = (s.get("title") or "").strip()
            if (not sid or not title or sid in seen
                    or s.get("kind") not in ("news", "rns")):
                continue
            batch.append(s)
            if len(batch) >= MAX_ITEMS:
                break
        if not batch:
            return []

        lines = [f"{i}) [{(s.get('source') or 'press')}] {s.get('title')}"
                 for i, s in enumerate(batch)]
        data = call(lines)
        # Mark everything we attempted as read, success or not — a daily
        # re-scan of the same headlines would burn budget for nothing.
        stamp = now.isoformat()
        for s in batch:
            seen[s.get("id") or s.get("url")] = stamp
        _save_seen(seen)
        if not isinstance(data, dict):
            return []

        events = []
        for lead in data.get("leads") or []:
            if not isinstance(lead, dict):
                continue
            key = lead.get("trigger_key")
            if (key not in ALLOWED_KEYS or key == "none"
                    or lead.get("confidence") not in ("high", "medium")):
                continue
            try:
                sig = batch[int(lead.get("index"))]
            except Exception:
                continue
            title = (sig.get("title") or "").strip()
            # The account gate still owns precision: the model's company
            # string must resolve to a watchlist account.
            company, tier = classify_account(
                (lead.get("company") or "").strip(), title)
            if not company or tier != "watchlist":
                continue
            spec = BY_KEY.get(key)
            try:
                published = datetime.fromisoformat(
                    (sig.get("published") or "").replace("Z", "+00:00"))
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except Exception:
                published = now
            rationale = (lead.get("rationale") or "").strip()[:200]
            events.append(TriggerEvent(
                trigger_key=key,
                trigger_label=(spec.label if spec else key),
                company=company,
                evidence=(f"AI read: {rationale} — “{title[:140]}”"
                          if rationale else f"AI read: “{title[:140]}”"),
                url=sig.get("url") or "",
                source_label=sig.get("source") or "press",
                published=published,
                raw_signal_id=sig.get("id") or "",
                tier_hint="covered",
                account_tier="watchlist",
            ))
        log.info("semantic scan: %d headlines read, %d leads kept",
                 len(batch), len(events))
        return events
    except Exception as e:
        log.info("semantic scan skipped (%s)", e)
        return []
