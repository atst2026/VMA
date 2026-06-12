"""Compounding company dossiers — the engine's long-term memory.

The blueprint's highest-leverage architectural decision: every company in
the pipeline gets a living dossier that accumulates every signal ever
seen (with dates and sources), every gate decision, every AD verdict and
every investigation note — so the next look at a company starts from
history instead of zero, and slow-burn stories (three signals over five
months) stop being invisible.

Two layers:
  state/dossiers/_index.json   — machine source of truth: per-pid event
                                 log (deduped), gate history, last-seen.
  state/dossiers/<pid>.md      — the human/agent view, REGENERATED from
                                 the index each run (never hand-edited).
  state/dossiers/<pid>.notes.md — free-text appended by /investigate;
                                 included verbatim in the rendered view
                                 and never touched by the renderer.

Written once per morning-brief run (update_dossiers), bounded by a
180-day last-seen prune. Non-fatal everywhere — a dossier failure can
never cost a brief.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tool.state_paths import state_dir

log = logging.getLogger("brief.dossier")

PRUNE_DAYS = 180
MAX_GATE_HISTORY = 30


def _dir() -> Path:
    return Path(str(state_dir())) / "dossiers"


def _index_path() -> Path:
    return _dir() / "_index.json"


def _load_index() -> dict:
    try:
        d = json.loads(_index_path().read_text())
        if isinstance(d, dict) and isinstance(d.get("companies"), dict):
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        log.info("dossier index unreadable (%s) — starting fresh", e)
    return {"version": 1, "companies": {}}


def _save_index(idx: dict) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(_index_path())


def _event_id(e: dict) -> str:
    return (e.get("raw_signal_id")
            or f"{e.get('trigger_key')}|{(e.get('published') or '')[:10]}|{e.get('url') or ''}")


def _fold_entry(rec: dict, entry: dict, gate: dict | None,
                today: str) -> None:
    """Fold one pipeline entry's current state into its index record."""
    rec["company"] = entry.get("company") or rec.get("company") or ""
    rec["last_seen"] = today
    rec["status"] = entry.get("status") or rec.get("status") or "active"
    seen = rec.setdefault("event_ids", [])
    events = rec.setdefault("events", [])
    for e in entry.get("events") or []:
        if not isinstance(e, dict):
            continue
        eid = _event_id(e)
        if eid in seen:
            continue
        seen.append(eid)
        events.append({"id": eid,
                       "date": (e.get("published") or "")[:10],
                       "key": e.get("trigger_key") or "",
                       "label": e.get("trigger_label") or "",
                       "evidence": (e.get("evidence") or "")[:400],
                       "source": e.get("source") or "",
                       "url": e.get("url") or ""})
    if gate is not None:
        hist = rec.setdefault("gate_history", [])
        state = "presented" if gate.get("presented") else "queued"
        why = (gate.get("confidence") or "; ".join(gate.get("reasons") or []))
        last = hist[-1] if hist else None
        # Only record state CHANGES (plus the first entry) — a stable lead
        # doesn't need 30 identical lines.
        if not last or last.get("state") != state or last.get("why") != why:
            hist.append({"date": today, "state": state, "why": why,
                         "recheck_days": gate.get("recheck_days")})
            del hist[:-MAX_GATE_HISTORY]
        rec["gate_state"] = state
        rec["gate_why"] = why
        rec["recheck_days"] = gate.get("recheck_days")
        rec["needs_investigation"] = bool(gate.get("investigate"))
        qual = gate.get("qual") or {}
        if qual.get("budget_why"):
            rec["budget_score"] = qual.get("budget")
            rec["budget_why"] = qual["budget_why"]


def _render_md(pid: str, rec: dict, verdicts: list[dict]) -> str:
    company = rec.get("company") or pid
    lines = [f"# {company} — BD dossier",
             "",
             f"_Updated {rec.get('last_seen', '')} · status: "
             f"{rec.get('status', 'active')} · gate: "
             f"{rec.get('gate_state', 'unknown')}"
             + (f" ({rec.get('gate_why')})" if rec.get("gate_why") else "")
             + (f" · recheck in {rec['recheck_days']}d"
                if rec.get("recheck_days") else "")
             + "_",
             ""]
    if rec.get("needs_investigation"):
        lines += ["**Queued for /investigate.**", ""]
    lines += ["## Signal timeline", ""]
    for e in sorted(rec.get("events") or [], key=lambda x: x.get("date") or ""):
        src = e.get("source") or ""
        url = e.get("url") or ""
        link = f" ([{src or 'source'}]({url}))" if url else (f" ({src})" if src else "")
        lines.append(f"- **{e.get('date') or '????-??-??'}** — "
                     f"{e.get('label') or e.get('key')}: "
                     f"{e.get('evidence') or '—'}{link}")
    lines += ["", "## Gate history", ""]
    for h in rec.get("gate_history") or []:
        rk = f" · recheck {h['recheck_days']}d" if h.get("recheck_days") else ""
        lines.append(f"- {h.get('date')}: {h.get('state')} — {h.get('why')}{rk}")
    if rec.get("budget_why"):
        _bscore = rec.get("budget_score")
        _blabel = ("Funded" if _bscore == 2
                   else ("Developing" if _bscore == 1 else "Constrained"))
        lines += ["", f"## Budget — {_blabel}", "", rec["budget_why"]]
    pid_verdicts = [v for v in verdicts if v.get("rid") == pid]
    if pid_verdicts:
        lines += ["", "## AD verdicts", ""]
        for v in pid_verdicts[-10:]:
            lines.append(f"- {(v.get('date') or '')[:10]}: {v.get('verdict')}")
    # Living team map + agency-relationship history (both accumulate
    # independently of the dossier; render whatever is on file).
    try:
        from tool import team_map as _tm
        tm_lines = _tm.summary_lines(company)
    except Exception:
        tm_lines = []
    if tm_lines:
        lines += ["", "## Team map (leadership page)", ""] + tm_lines
    try:
        from tool import agency_relationships as _ar
        ar_lines = _ar.summary_lines(company)
    except Exception:
        ar_lines = []
    if ar_lines:
        lines += ["", "## Agency relationships", ""] + ar_lines
    # Account thesis — the AI-researched, evidence-cited read of what
    # this company NEEDS and VMA can plug (advisory_research overlay).
    # When fresh it replaces the generic service-fit section below; the
    # static mix remains the fallback so the dossier never goes silent.
    thesis = None
    try:
        from tool import advisory_research as _advres
        thesis = _advres.get(pid)
    except Exception:
        thesis = None
    if thesis and thesis.get("needs"):
        when = (thesis.get("researched_at") or "")[:10]
        lines += ["", f"## Account thesis — researched {when}", "",
                  f"**{thesis.get('headline')}**", ""]
        if thesis.get("function_snapshot"):
            lines += [thesis["function_snapshot"], ""]
        lines.append("### What they need (and what VMA sells into it)")
        lines.append("")
        for n in thesis["needs"]:
            cite = f" ([source]({n['url']}))" if n.get("url") else ""
            when_e = f", {n['date']}" if n.get("date") else ""
            lines.append(
                f"- **{n.get('service_label') or n.get('service')}** — "
                f"{n.get('need')} _{n.get('why_now')}_ "
                f"(evidence: {n.get('evidence')}{when_e}{cite}; "
                f"confidence {n.get('confidence')})")
        if thesis.get("hiring_needs"):
            lines += ["", "### Hiring needs", ""]
            lines += [f"- {h}" for h in thesis["hiring_needs"]]
        lines += ["", "### The meeting hook", "",
                  f"> {thesis.get('meeting_hook')}"]
        if thesis.get("talking_points"):
            lines += ["", "### Talking points", ""]
            lines += [f"- {t}" for t in thesis["talking_points"]]
        if thesis.get("sources"):
            lines += ["", "### Sources", ""]
            lines += [f"- [{s.get('label') or 'source'}]({s['url']})"
                      for s in thesis["sources"]]
    else:
        # Service-fit fallback — the static playbook voted across the
        # FULL accumulated signal history: a slow-burn story of three
        # signals over five months gets a combined mix no single event
        # would surface.
        try:
            from tool.advisory import service_fit_for
            keys = [e.get("key") for e in rec.get("events") or []
                    if isinstance(e, dict) and e.get("key")]
            fit = service_fit_for(keys) if keys else None
        except Exception:
            fit = None
        if fit and fit.get("services"):
            lines += ["", "## Service fit — what VMA can sell here", ""]
            for s in fit["services"]:
                lines.append(f"- **{s.get('label')}** — {s.get('reason')}")
            if fit.get("budget_note"):
                lines += ["", f"_{fit['budget_note']}_"]
    notes = _dir() / f"{pid}.notes.md"
    if notes.exists():
        try:
            lines += ["", "## Investigation notes", "", notes.read_text().strip()]
        except Exception:
            pass
    return "\n".join(lines) + "\n"


def update_dossiers(entries: list[dict], gates: dict[str, dict] | None = None,
                    verdicts: list[dict] | None = None) -> int:
    """Fold today's pipeline state into the dossiers. Returns the number
    updated. Never raises."""
    try:
        gates = gates or {}
        verdicts = verdicts if verdicts is not None else []
        today = datetime.now(timezone.utc).date().isoformat()
        idx = _load_index()
        companies = idx["companies"]
        touched = 0
        for entry in entries or []:
            pid = entry.get("pid")
            if not pid:
                continue
            rec = companies.setdefault(pid, {})
            _fold_entry(rec, entry, gates.get(pid), today)
            touched += 1
        # Prune long-dead records (and their rendered files).
        cutoff = datetime.now(timezone.utc)
        for pid in list(companies):
            last = companies[pid].get("last_seen") or ""
            try:
                age = (cutoff.date()
                       - datetime.fromisoformat(last).date()).days
            except ValueError:
                age = PRUNE_DAYS + 1
            if age > PRUNE_DAYS:
                companies.pop(pid, None)
                for suffix in (".md", ".notes.md"):
                    try:
                        (_dir() / f"{pid}{suffix}").unlink(missing_ok=True)
                    except OSError:
                        pass
        _save_index(idx)
        for pid, rec in companies.items():
            try:
                (_dir() / f"{pid}.md").write_text(
                    _render_md(pid, rec, verdicts), encoding="utf-8")
            except Exception as e:
                log.info("dossier %s render failed: %s", pid, e)
        log.info("dossiers: %d entries folded, %d companies on file",
                 touched, len(companies))
        return touched
    except Exception as e:
        log.info("dossier update skipped (%s)", e)
        return 0


def read(pid: str) -> str:
    """The rendered dossier text for a company (the /investigate command's
    starting context). Empty string when none exists."""
    try:
        return (_dir() / f"{pid}.md").read_text(encoding="utf-8")
    except Exception:
        return ""


def append_note(pid: str, note: str) -> bool:
    """Append a dated free-text note (used by /investigate). The notes file
    survives re-renders verbatim."""
    if not pid or not (note or "").strip():
        return False
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(d / f"{pid}.notes.md", "a", encoding="utf-8") as f:
        f.write(f"\n### {stamp}\n\n{note.strip()}\n")
    return True
