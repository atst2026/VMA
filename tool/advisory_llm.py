"""The Opus advisory passes — Conviction Verdict + Outside-In Diagnostic.

These are the reasoned passes that replace the deterministic verdict and v0
diagnostic (ADVISORY_ENGINE.md §5). They are designed to run in TWO places:

  * `/advisory-brief`'s deep pass — the model is Claude Code itself, free
    under the subscription. That path writes the overlay directly; this
    module isn't needed there.
  * This module — an OPTIONAL API path for unattended automation (a
    scheduled action). It is OFF BY DEFAULT and a strict no-op unless ALL
    of: ANTHROPIC_API_KEY is set, ADVISORY_LLM_ENABLED=1, and the spend
    policy allows it. So the £0 nightly pipeline never calls a paid model
    unless a human explicitly turns this on. When off, callers fall back to
    the deterministic gate/diagnostic — nothing breaks.

The system prompt hard-wires the §9 credibility guardrails: every claim a
benchmark-anchored HYPOTHESIS not an assertion, published GOV.UK figures
only, gaps framed as opportunities, no PURSUE without a reachable buyer,
calibrated confidence that acknowledges uncertainty, and the novelty rule
(the insight must not be derivable from the company's own public pages).

Mirrors tool.semantic_scan's model-call shape (claude-opus-4-8, adaptive
thinking, json_schema output, graceful no-op, never raises).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("brief.advisory.llm")

MODEL = "claude-opus-4-8"

_GUARDRAILS = (
    "You are VMA Group's advisory origination analyst — a talent consultancy "
    "for the communications, corporate-affairs and marketing functions (NOT "
    "general management consulting). You qualify whether a detected signal is "
    "a genuine advisory opportunity and write the case that wins a "
    "consultative meeting.\n\n"
    "Hard rules (a breach is worse than a missed lead):\n"
    "- Every outside-in claim is a benchmark-anchored HYPOTHESIS ('functions "
    "of your size typically…'), never a cold factual assertion about a named "
    "employer's failings.\n"
    "- Use only published GOV.UK figures; frame any gap as an OPPORTUNITY, "
    "never an accusation.\n"
    "- Never return PURSUE without a concrete pain AND a reachable economic "
    "buyer — a clear action must be a reachable one.\n"
    "- The insight must rest on the non-public comparison (the peer cohort / "
    "resourcing benchmark), not on what the buyer can read on their own "
    "homepage. If it can't, that is a weaker (DEVELOP) lead.\n"
    "- Calibrate confidence honestly; acknowledge uncertainty rather than "
    "confabulate. Silence/abstention beats a confident guess.\n"
    "- UK spelling; write like an adviser, not a signal-spotter."
)

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["KILL", "DEVELOP", "PURSUE"]},
        "conviction": {"type": "integer"},
        "named_pain": {"type": "string"},
        "economic_buyer": {"type": "string"},
        "recommended_service": {"type": "string"},
        "sharpest_insight": {"type": "string"},
        "kill_reasons": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string",
                       "enum": ["High", "Moderate", "Low"]},
        "diagnostic": {"type": "string"},
    },
    "required": ["verdict", "conviction", "named_pain", "sharpest_insight",
                 "confidence"],
    "additionalProperties": False,
}


def enabled() -> bool:
    """True only when explicitly switched on AND a key is present AND the
    spend policy allows it. Off by default — the £0 guarantee."""
    if (os.environ.get("ADVISORY_LLM_ENABLED") or "").strip() not in (
            "1", "true", "yes", "on"):
        return False
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return False
    try:
        from tool.config import model_spend_allowed
        return bool(model_spend_allowed("optional"))
    except Exception:
        return True


def _brief(signal, qual: dict, ctx: dict) -> str:
    """The grounded user message: everything the deterministic layers know,
    handed to Opus to reason over (prompt-cacheable shared corpus aside)."""
    s = signal.to_dict() if hasattr(signal, "to_dict") else dict(signal or {})
    return json.dumps({
        "trigger": s.get("trigger"), "company": s.get("company"),
        "evidenced_pain": s.get("pain"), "why_now": s.get("why_now"),
        "buyer_hint": s.get("buyer_hint"), "service_mix": s.get("service_mix"),
        "qualification_scorecard": qual,
        "grounded_context": {
            "sector": ctx.get("sector"), "peers": ctx.get("peers"),
            "size_band": ctx.get("size_band"),
            "expected_comms_fte": ctx.get("expected_comms_fte"),
            "resourcing_benchmark": (ctx.get("resourcing_benchmark") or {}).get("line"),
            "median_gap": ctx.get("median_gap"), "late": ctx.get("late"),
        },
    }, ensure_ascii=False)


def _call_model(brief: str) -> dict | None:
    """One Messages API call → parsed verdict dict, or None. Isolated so
    tests inject a stub and so it no-ops cleanly when disabled."""
    if not enabled():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            system=_GUARDRAILS,
            messages=[{"role": "user", "content":
                       "Qualify this advisory lead and write the conviction "
                       "verdict + a 1-page outside-in diagnostic hypothesis "
                       "(anchored to the resourcing benchmark and VMA's "
                       "Network Rail methodology):\n" + brief}],
            output_config={"format": {"type": "json_schema",
                                      "schema": _VERDICT_SCHEMA}},
        )
        if getattr(resp, "stop_reason", "") == "refusal":
            log.info("advisory verdict: model declined")
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("advisory verdict model call failed: %s", e)
        return None


def conviction_verdict(signal, qual: dict, ctx: dict, call=None) -> dict | None:
    """The Opus Conviction Verdict for one lead, or None (disabled / failed
    / declined). Caller falls back to the deterministic verdict on None."""
    call = call or _call_model
    data = call(_brief(signal, qual, ctx))
    if not isinstance(data, dict) or data.get("verdict") not in (
            "KILL", "DEVELOP", "PURSUE"):
        return None
    return data


def run_and_persist(signal, qual: dict, ctx: dict, call=None) -> bool:
    """Run the Opus pass and write the overlay the gate/pack read. Returns
    False when disabled or the pass produced nothing. Never raises."""
    try:
        v = conviction_verdict(signal, qual, ctx, call=call)
        if not v:
            return False
        from tool.advisory_overlay import write
        return write(
            getattr(signal, "company", ""), getattr(signal, "trigger", ""),
            v["verdict"], conviction=v.get("conviction"),
            named_pain=v.get("named_pain", ""),
            economic_buyer=v.get("economic_buyer", ""),
            recommended_service=v.get("recommended_service", ""),
            sharpest_insight=v.get("sharpest_insight", ""),
            diagnostic=v.get("diagnostic", ""),
            kill_reasons=v.get("kill_reasons"),
            confidence=v.get("confidence", ""), source="opus_api")
    except Exception as e:
        log.info("advisory opus run skipped (%s)", e)
        return False
