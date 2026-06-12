"""Scheduled auto-investigation — the /investigate playbook, unattended.

The gate flags leads "needs investigation" and then nothing investigates
them until a human types /investigate. This module closes that loop:
each morning, the top scored predictors WITHOUT a fresh investigation
overlay get one model pass with server-side web search/fetch, running
the playbook's core questions — verify the trigger via independent
sources, check the seat's title family for an incumbent, try honestly
to kill the lead, and capture any fee-propensity facts found on the
way. The typed verdict is written through the SAME door a human run
uses (investigations.write_overlay), so the gate, the cards and the
21-day expiry all behave identically — confirmed presents at High
confidence, killed queues with the reason, recheck sets the timer.

Budget: at most MAX_LEADS investigations per run, and a lead with a
fresh overlay is never re-run (the overlay's own expiry is the recheck
clock). Graceful no-op without ANTHROPIC_API_KEY. Never raises.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("brief.auto_investigate")

MODEL = "claude-opus-4-8"
MAX_LEADS = 5
MAX_CONTINUATIONS = 6

_SYSTEM = (
    "You are the investigation analyst for a UK senior communications & "
    "marketing recruitment desk. You receive ONE business-development "
    "hypothesis: a company, the public trigger events behind it, and the "
    "predicted hiring need. Verify it with live web research, then return "
    "a typed verdict.\n\n"
    "Method (use web_search and web_fetch; free public sources only):\n"
    "1. VERIFY the trigger: confirm the event with at least one source "
    "independent of the ones provided; check dates and that it concerns "
    "this company (not a subsidiary or namesake).\n"
    "2. INCUMBENT CHECK: search the seat's TITLE FAMILY, never just the "
    "predicted title — 'Corporate Affairs Director' is answered by a "
    "sitting 'Group Corporate Communications Director' all the same. A "
    "current incumbent does not kill the lead: it reframes it as a build "
    "under them (they are likely the buyer). Note who you find.\n"
    "3. TRY TO KILL IT: interim/caretaker cover, the event resolved or "
    "reversed, administration, incumbent agency lock, hiring freeze with "
    "no live work, wrong company.\n"
    "4. PROPENSITY: do agencies post roles for them? Any appointment "
    "credited to a search firm? TA/recruiter roles on their careers "
    "board?\n\n"
    "Verdict rules: 'confirmed' needs the trigger independently verified "
    "AND no kill condition — it puts the lead on a call list, so be the "
    "sceptic. 'killed' needs a concrete reason. Otherwise 'recheck' with "
    "a sensible recheck_days (7-60). 'Could not verify' is a finding — "
    "use recheck, never guess. Keep note to two sentences an Account "
    "Director can act on."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string",
                    "enum": ["confirmed", "killed", "recheck"]},
        "note": {"type": "string"},
        "kill_reasons": {"type": "array", "items": {"type": "string"}},
        "recheck_days": {"type": ["integer", "null"]},
        "economic_buyer": {"type": "string"},
        "champion_path": {"type": "string"},
        "incumbent_found": {"type": ["string", "null"]},
        "agency_user": {"type": ["boolean", "null"]},
        "agency_scope": {"type": ["string", "null"],
                         "enum": ["comms_marketing", "general",
                                  "temp_staffing", None]},
        "internal_ta": {"type": ["boolean", "null"]},
        "propensity_note": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "note", "kill_reasons", "recheck_days",
                 "economic_buyer", "champion_path", "incumbent_found",
                 "agency_user", "agency_scope", "internal_ta",
                 "propensity_note", "sources"],
    "additionalProperties": False,
}


def _brief_for(entry: dict) -> str:
    lines = [f"Company: {entry.get('company')}",
             f"Predicted need: {entry.get('predicted_role') or 'senior comms/marketing hire'}",
             f"Window: {entry.get('window_label') or 'unknown'}",
             f"Engine incumbency status: {entry.get('incumbent_status') or 'unchecked'}"
             + (f" ({entry.get('incumbent_name')})"
                if entry.get('incumbent_name') else ""),
             "Trigger events:"]
    for e in (entry.get("events") or [])[:6]:
        if isinstance(e, dict):
            lines.append(f"- {e.get('trigger_label')} "
                         f"({(e.get('published') or '')[:10]}) "
                         f"{(e.get('evidence') or '')[:160]} "
                         f"[{e.get('url') or 'no url'}]")
    return "\n".join(lines)


def _run_model(brief: str) -> dict | None:
    """One investigation: server-side web search/fetch loop, structured
    verdict. Isolated so tests inject a stub."""
    import os
    if not (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        tools = [{"type": "web_search_20260209", "name": "web_search"},
                 {"type": "web_fetch_20260209", "name": "web_fetch"}]
        messages = [{"role": "user", "content": brief}]
        resp = None
        for _ in range(MAX_CONTINUATIONS):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=_SYSTEM,
                tools=tools,
                messages=messages,
                output_config={"format": {"type": "json_schema",
                                          "schema": _SCHEMA}},
            )
            if resp.stop_reason != "pause_turn":
                break
            # Server-side tool loop paused — resume where it left off.
            messages = [{"role": "user", "content": brief},
                        {"role": "assistant", "content": resp.content}]
        if resp is None or resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("auto-investigate model call failed: %s", e)
        return None


def run(max_leads: int = MAX_LEADS, runner=None) -> int:
    """Investigate the top unscored-on predictors. Returns the number of
    overlays written. Never raises."""
    try:
        from tool.config import model_spend_allowed
        if not model_spend_allowed("optional"):
            log.info("%s skipped: VMA_MODEL_SPEND=contacts", "auto-investigate")
            return 0
    except Exception:
        pass
    try:
        from tool import investigations
        from tool.predictor_pipeline import load_pipeline

        runner = runner or _run_model
        existing = investigations.get_all()
        entries = [e for e in
                   (load_pipeline().get("predictors") or {}).values()
                   if e.get("status") == "active"
                   and e.get("pid") and e["pid"] not in existing]
        entries.sort(key=lambda e: e.get("score") or 0, reverse=True)
        written = 0
        for entry in entries[:max_leads]:
            data = runner(_brief_for(entry))
            if not isinstance(data, dict):
                continue
            verdict = data.get("verdict")
            if verdict not in ("confirmed", "killed", "recheck"):
                continue
            note = (data.get("note") or "").strip()[:300]
            inc = (data.get("incumbent_found") or "").strip()
            if inc:
                note = (note + f" Incumbent: {inc}.").strip()
            ok = investigations.write_overlay(
                entry["pid"], verdict,
                note=note or "auto-investigation",
                recheck_days=(data.get("recheck_days")
                              if isinstance(data.get("recheck_days"), int)
                              else None),
                evidence_added=[u for u in (data.get("sources") or [])
                                if isinstance(u, str)][:6],
                economic_buyer=(data.get("economic_buyer") or "")[:120],
                champion_path=(data.get("champion_path") or "")[:200],
                kill_reasons=[k for k in (data.get("kill_reasons") or [])
                              if isinstance(k, str)][:4],
            )
            if ok:
                written += 1
            # Propensity facts found on the way feed the whole engine.
            if (data.get("agency_user") is not None
                    or data.get("internal_ta") is not None):
                try:
                    from tool import propensity
                    propensity.record_finding(
                        entry.get("company"),
                        internal_ta=data.get("internal_ta"),
                        agency_user=data.get("agency_user"),
                        agency_scope=data.get("agency_scope"),
                        note=(data.get("propensity_note")
                              or "auto-investigation finding"),
                        source_url=(data.get("sources") or [""])[0])
                except Exception:
                    pass
        log.info("auto-investigate: %d overlays written "
                 "(%d candidates without fresh overlays)",
                 written, len(entries))
        return written
    except Exception as e:
        log.info("auto-investigate skipped (%s)", e)
        return 0
