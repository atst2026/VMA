"""Advisory Gap Research — the AD-grade account thesis, unattended.

The static service-fit lens (tool/advisory.py) maps trigger classes to
the services VMA sells; it is a playbook, not research — its reasons are
generic to the trigger, never to THIS company. This module closes that
gap for the leads that matter (gate-presented "Ready" + score-qualified
"Developing"): one model pass per company with live web search, fed the
ENTIRE accumulated picture the engine already holds — the dossier's
signal timeline, the living team map, the agency-relationship ledger,
peer activity, the investigation verdict and the static service mix as
a hypothesis — and asked to do what an expert Account Director would do
the night before a first meeting:

  - establish what the comms/marketing function actually looks like
    today (leadership page, careers page, LinkedIn public surface,
    annual-report strategy language);
  - identify GENUINE, evidence-cited gaps and needs VMA can plug —
    advisory (org design, benchmarking, coaching, ED&I), referral lanes
    (partner agency, engagement platform) and hires alike;
  - write the specific meeting hook that converts the lead to a meeting.

The typed thesis is stored as a per-lead overlay (21-day expiry, and
invalidated early when the lead's event set changes), rendered into the
company dossier and onto the lead cards in place of the generic
service-fit reasons. Services are schema-locked to the catalogue in
tool/advisory.SERVICES — the model grounds the mix; it cannot invent
new product lines. Budget: MAX_LEADS passes per run; a lead with a
fresh, still-valid thesis is never re-spent. Graceful no-op without
ANTHROPIC_API_KEY. Never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.advisory_research")

MODEL = "claude-opus-4-8"
MAX_LEADS = int(os.environ.get("ADVISORY_RESEARCH_MAX") or 8)
MAX_CONTINUATIONS = 8
EXPIRY_DAYS = 21

_SYSTEM = (
    "You are an expert UK Account Director at VMA Group — a talent "
    "consultancy in communications and marketing whose services go far "
    "beyond recruitment: Strategy & Organisation Design reviews "
    "(consultation → benchmarking & design → implementation, e.g. the "
    "Network Rail engagement), Benchmarking of team structure / "
    "headcount / salary against comparable organisations (the L'Oréal-"
    "style peer report), Professional Development & Coaching (Change "
    "Oasis, Famn), ED&I Consulting (RiverRoad, neuroinclusion), partner-"
    "agency referral when there's work but no headcount budget, "
    "employee-engagement platform introductions (e.g. Workvivo, "
    "Staffbase), plus executive search, permanent recruitment and "
    "interim cover.\n\n"
    "You receive ONE business-development lead: a company, everything "
    "our engine has accumulated about it, and a generic service "
    "hypothesis. Work it like the night before a first meeting — with "
    "live web research (web_search / web_fetch; free public sources "
    "only):\n"
    "1. FUNCTION TODAY: establish what their communications/marketing "
    "function actually looks like NOW — leadership and team pages, "
    "careers page and live ads, LinkedIn public pages, recent press "
    "releases, the annual report's strategy language. Who leads it, "
    "roughly how big, what's visibly missing.\n"
    "2. GENUINE NEEDS: from that evidence plus the trigger events, "
    "identify the specific gaps, risks and opportunities VMA can plug — "
    "advisory projects and referral lanes as much as hires. Every need "
    "must cite dated, URL'd evidence about THIS company. A need you "
    "cannot ground in evidence does not go in the list.\n"
    "3. THE MEETING HOOK: write the specific opener that earns a "
    "meeting — the one observation about their world that proves we did "
    "the work, tied to the single service we'd lead with.\n\n"
    "Rules: be specific or be silent — generic trigger-class reasoning "
    "is already on the card and is worthless here. Calibrate "
    "confidence honestly (high = primary source this quarter). Never "
    "fabricate names, numbers or URLs; if research comes up dry on a "
    "point, omit the point. British English. Write for a sharp AD: "
    "tight, factual, conversion-focused."
)


def _schema() -> dict:
    from tool.advisory import SERVICES
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "function_snapshot": {"type": "string"},
            "needs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "need": {"type": "string"},
                        "service": {"type": "string",
                                    "enum": list(SERVICES)},
                        "why_now": {"type": "string"},
                        "evidence": {"type": "string"},
                        "url": {"type": "string"},
                        "date": {"type": "string"},
                        "confidence": {"type": "string",
                                       "enum": ["high", "medium", "low"]},
                    },
                    "required": ["need", "service", "why_now", "evidence",
                                 "confidence"],
                },
            },
            "hiring_needs": {"type": "array", "items": {"type": "string"}},
            "meeting_hook": {"type": "string"},
            "talking_points": {"type": "array", "items": {"type": "string"}},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"url": {"type": "string"},
                                   "label": {"type": "string"}},
                    "required": ["url"],
                },
            },
        },
        "required": ["headline", "function_snapshot", "needs",
                     "meeting_hook", "sources"],
    }


# ---------------------------------------------------------------- store

def _dir():
    d = state_dir() / "advisory_research"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def events_hash(events: list) -> str:
    """Stable fingerprint of a lead's event set — when it changes, the
    thesis is stale and worth re-researching."""
    ids = sorted({(e.get("id") or e.get("trigger_key") or "")
                  + (e.get("date") or e.get("published") or "")[:10]
                  for e in (events or []) if isinstance(e, dict)})
    return hashlib.sha1("|".join(ids).encode()).hexdigest()[:16]


def get(pid: str) -> dict | None:
    """The fresh thesis overlay for a lead, or None. Never raises."""
    try:
        p = _dir() / f"{pid}.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        at = datetime.fromisoformat(d.get("researched_at"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - at > timedelta(days=EXPIRY_DAYS):
            return None
        return d
    except Exception:
        return None


def get_all() -> dict:
    """pid -> fresh thesis, for render-time bulk loads. Never raises."""
    out = {}
    try:
        for p in _dir().glob("*.json"):
            d = get(p.stem)
            if d:
                out[p.stem] = d
    except Exception:
        pass
    return out


def _write(pid: str, data: dict) -> bool:
    try:
        (_dir() / f"{pid}.json").write_text(
            json.dumps(data, indent=1, sort_keys=True))
        return True
    except Exception as e:
        log.info("advisory research write %s failed: %s", pid, e)
        return False


# ------------------------------------------------------------- context

def _company_context(entry: dict) -> str:
    """Everything the engine already knows about this company, assembled
    as the model's working file — the point is that the research pass
    starts from our accumulated memory, not from zero."""
    company = entry.get("company") or ""
    parts: list[str] = [
        f"COMPANY: {company}",
        f"Predicted hiring angle: {entry.get('predicted_role') or 'senior comms/marketing'}"
        f" · window {entry.get('window_label') or 'unknown'}",
    ]
    # Full accumulated signal timeline (dossier memory, not just today).
    try:
        from tool import dossier as _doss
        from tool import predictor_pipeline as _pp
        pid = entry.get("pid") or _pp._pid(company)
        rec = (_doss._load_index().get("companies") or {}).get(pid) or {}
        evs = sorted(rec.get("events") or [],
                     key=lambda x: x.get("date") or "")[-20:]
        if evs:
            parts.append("\nSIGNAL TIMELINE (everything we have ever seen):")
            for e in evs:
                parts.append(
                    f"- {e.get('date') or '????'} {e.get('label') or e.get('key')}: "
                    f"{(e.get('evidence') or '')[:180]} "
                    f"[{e.get('url') or 'no url'}]")
    except Exception:
        evs = entry.get("events") or []
        if evs:
            parts.append("\nTRIGGER EVENTS:")
            for e in evs[:8]:
                if isinstance(e, dict):
                    parts.append(
                        f"- {(e.get('published') or '')[:10]} "
                        f"{e.get('trigger_label')}: "
                        f"{(e.get('evidence') or '')[:180]}")
    # Living team map (leadership page roster + joiners/leavers).
    try:
        from tool import team_map as _tm
        tm = _tm.summary_lines(company)
        if tm:
            parts.append("\nTEAM MAP (their own leadership page):")
            parts += tm[:12]
    except Exception:
        pass
    # Agency-relationship history.
    try:
        from tool import agency_relationships as _ar
        ar = _ar.summary_lines(company)
        if ar:
            parts.append("\nAGENCY RELATIONSHIPS (public record):")
            parts += ar[:8]
    except Exception:
        pass
    # Peer-set activity (what's live in their sector).
    try:
        from tool.call_ammo import sector_insights
        from tool.profiles import active_profile
        ammo = sector_insights(company, active_profile().key, limit=3)
        if ammo:
            parts.append("\nPEER-SET ACTIVITY:")
            parts += [f"- {a}" for a in ammo]
    except Exception:
        pass
    # Investigation verdict (buyer, champion path, kill risks).
    try:
        from tool import investigations as _inv
        ov = (_inv.get_all() or {}).get(entry.get("pid") or "")
        if ov:
            parts.append(
                f"\nINVESTIGATION VERDICT: {ov.get('verdict')} — "
                f"{(ov.get('note') or '')[:200]}")
            if ov.get("economic_buyer"):
                parts.append(f"Economic buyer: {ov['economic_buyer']}")
    except Exception:
        pass
    # The static service hypothesis (the playbook to verify, not recite).
    try:
        from tool.advisory import service_fit_for
        keys = [e.get("trigger_key") or e.get("key")
                for e in (entry.get("events") or []) if isinstance(e, dict)]
        fit = service_fit_for(keys)
        parts.append("\nGENERIC SERVICE HYPOTHESIS (trigger-class playbook "
                     "— your job is to GROUND or REPLACE it with this "
                     "company's reality):")
        for s in fit.get("services") or []:
            parts.append(f"- {s['label']}: {s['reason']}")
        if fit.get("budget_note"):
            parts.append(f"- NOTE: {fit['budget_note']}")
    except Exception:
        pass
    parts.append("\nResearch this company now and return the account "
                 "thesis.")
    return "\n".join(parts)


# --------------------------------------------------------------- model

def _run_model(brief: str) -> dict | None:
    """One research pass: server-side web search/fetch loop, structured
    thesis. Isolated so tests inject a stub."""
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
                                          "schema": _schema()}},
            )
            if resp.stop_reason != "pause_turn":
                break
            messages = [{"role": "user", "content": brief},
                        {"role": "assistant", "content": resp.content}]
        if resp is None or resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text) if text else None
    except Exception as e:
        log.info("advisory research model call failed: %s", e)
        return None


def _validated(data: dict) -> dict | None:
    """Hard validation beyond the schema: services locked to the
    catalogue, caps applied, junk dropped. None = unusable."""
    try:
        from tool.advisory import SERVICES
        if not isinstance(data, dict):
            return None
        headline = (data.get("headline") or "").strip()[:220]
        hook = (data.get("meeting_hook") or "").strip()[:400]
        if not headline or not hook:
            return None
        needs = []
        for n in (data.get("needs") or [])[:6]:
            if not isinstance(n, dict):
                continue
            svc = (n.get("service") or "").strip()
            if svc not in SERVICES:
                continue
            if not (n.get("need") or "").strip() \
                    or not (n.get("evidence") or "").strip():
                continue
            needs.append({
                "need": n["need"].strip()[:200],
                "service": svc,
                "service_label": SERVICES[svc]["label"],
                "service_short": SERVICES[svc]["short"],
                "family": SERVICES[svc]["family"],
                "why_now": (n.get("why_now") or "").strip()[:300],
                "evidence": n["evidence"].strip()[:300],
                "url": (n.get("url") or "").strip()[:400],
                "date": (n.get("date") or "").strip()[:10],
                "confidence": (n.get("confidence") or "medium"),
            })
        if not needs:
            return None
        return {
            "headline": headline,
            "function_snapshot":
                (data.get("function_snapshot") or "").strip()[:700],
            "needs": needs,
            "hiring_needs": [h.strip()[:200] for h in
                             (data.get("hiring_needs") or [])[:3]
                             if isinstance(h, str) and h.strip()],
            "meeting_hook": hook,
            "talking_points": [t.strip()[:220] for t in
                               (data.get("talking_points") or [])[:4]
                               if isinstance(t, str) and t.strip()],
            "sources": [{"url": s.get("url", "")[:400],
                         "label": (s.get("label") or "source")[:80]}
                        for s in (data.get("sources") or [])[:10]
                        if isinstance(s, dict) and s.get("url")],
        }
    except Exception:
        return None


# ----------------------------------------------------------------- run

def _candidates() -> list[dict]:
    """Ready + Developing leads, spend-priority order: gate-presented
    first (dossier gate_state), then by score. Leads whose thesis is
    fresh AND whose event set hasn't changed are excluded."""
    from tool import dossier as _doss
    from tool.gate import SCORE_DEVELOPING
    from tool.predictor_pipeline import load_pipeline

    idx = {}
    try:
        idx = _doss._load_index().get("companies") or {}
    except Exception:
        pass
    try:
        from tool.lead_engine import score_lead as _score_lead
        from tool.profiles import active_profile
        _desk = active_profile().key
    except Exception:
        _score_lead, _desk = None, "comms"
    out = []
    for e in (load_pipeline().get("predictors") or {}).values():
        if e.get("status") != "active" or not e.get("pid"):
            continue
        rec = idx.get(e["pid"]) or {}
        presented = rec.get("gate_state") == "presented"
        score = 0
        if _score_lead is not None:
            try:
                from tool.gate import strength_score as _ss
                lead = _score_lead(e, "predictor", _desk) or {}
                score = _ss(lead, {"presented": presented}, e) or 0
            except Exception:
                score = 0
        if not presented and score < SCORE_DEVELOPING:
            continue
        existing = get(e["pid"])
        ev_hash = events_hash(rec.get("events") or e.get("events") or [])
        if existing and existing.get("events_hash") == ev_hash:
            continue   # fresh thesis, nothing new — don't re-spend
        out.append({**e, "_presented": presented, "_ev_hash": ev_hash,
                    "_score": score})
    out.sort(key=lambda x: (not x["_presented"], -(x["_score"] or 0)))
    return out


def run(max_leads: int = MAX_LEADS, runner=None) -> int:
    """Research the top Ready/Developing leads without a current thesis.
    Returns the number of overlays written. Never raises."""
    try:
        runner = runner or _run_model
        written = 0
        cands = _candidates()
        for entry in cands[:max_leads]:
            data = runner(_company_context(entry))
            thesis = _validated(data) if data else None
            if not thesis:
                continue
            thesis["company"] = entry.get("company") or ""
            thesis["researched_at"] = datetime.now(timezone.utc).isoformat()
            thesis["events_hash"] = entry.get("_ev_hash") or ""
            if _write(entry["pid"], thesis):
                written += 1
                log.info("advisory research %s: %d needs, hook: %.60s…",
                         entry.get("company"), len(thesis["needs"]),
                         thesis["meeting_hook"])
        log.info("advisory research: %d theses written (%d candidates)",
                 written, len(cands))
        return written
    except Exception as e:
        log.info("advisory research skipped (%s)", e)
        return 0
