"""Pipeline triage — subtractive intelligence.

Sara pastes her current active conversations / open mandates as
free-text (one per line, or comma-separated with metadata). The module
scores each entry honestly:

  alive       — has had specific recent movement
  stalled     — fixable: missing one named decision or one named action
  cold        — no progress signals, time has passed
  dead        — should be removed from the pipeline

The scoring is heuristic and the reasoning is shown verbatim so Sara
can override. Pattern matches:

  alive triggers     — "shortlist", "interview booked", "offer", "feedback in"
  stalled triggers   — "waiting on", "haven't heard", "follow up", "chase",
                       "they said they'd come back", "sign-off"
  cold triggers      — explicit week-counts ("3 weeks", "month") with no
                       progress verbs
  dead triggers      — "ghosting", "no response", "non-engaged", "moved on",
                       "stopped replying"

The output is NOT advice on whether to keep working an account — it
returns the *most likely state* with stated reasoning so Sara can
choose what to act on first. Bottleneck in a dead market is attention.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, asdict


@dataclass
class TriageRow:
    raw: str                 # the line Sara pasted, untouched
    label: str               # 'alive' | 'stalled' | 'cold' | 'dead' | 'unclear'
    score: int               # 0-100; 100 = most alive
    reasoning: str           # one-sentence justification
    next_action: str         # one concrete proposed move


_ALIVE = [
    (r"\b(?:shortlist(?:ed)?|on (?:the )?shortlist)\b",            "shortlist activity"),
    (r"\binterview(?:s)? (?:booked|scheduled|confirmed|next week|tomorrow)\b",
                                                                   "interview booked"),
    (r"\boffer (?:made|out|verbal|accepted|extended)\b",           "offer in motion"),
    (r"\bsigned (?:contract|terms|engagement)\b",                  "contract signed"),
    (r"\breference checks?\b",                                     "reference stage"),
    (r"\b(?:second|third|final) (?:round|stage|interview)\b",      "late-stage interview"),
    (r"\bfeedback (?:in|received|positive)\b",                     "feedback received"),
    (r"\bmet (?:yesterday|last week|this week|on \w+day)\b",       "recent face-time"),
    (r"\bdiscussing terms\b",                                      "terms under discussion"),
    (r"\bcoffee (?:tomorrow|next week|this week)\b",               "imminent meeting"),
]

_STALLED = [
    (r"\bwaiting on\b",                                            "waiting on someone named"),
    (r"\bhaven'?t heard\b",                                        "silence — but identifiable contact"),
    (r"\bfollow[\s-]?up\b",                                        "needs follow-up action"),
    (r"\bchase\b",                                                 "chase pending"),
    (r"\bsaid (?:they'd|they would|he'd|she'd) (?:come back|get back|reply|respond)\b",
                                                                   "promised reply outstanding"),
    (r"\bsign[\s-]?off\b",                                         "internal sign-off pending"),
    (r"\bbudget approval\b",                                       "budget approval pending"),
    (r"\bon hold\b",                                               "explicitly on hold"),
    (r"\bdelayed\b",                                               "explicit delay"),
    (r"\bdeferred\b",                                              "deferred"),
    (r"\bpushed (?:back|out)\b",                                   "timeline pushed"),
]

_COLD = [
    (r"\b(?:\d+|\d+\s*-\s*\d+|few|several|couple of) (?:weeks?|months?) (?:ago|since)\b",
                                                                   "time without progress"),
    (r"\bsince (?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
                                                                   "named month back"),
    (r"\bnothing (?:has )?moved\b",                                "no movement"),
    (r"\bno (?:update|news|progress)\b",                           "no updates"),
    (r"\bquiet\b",                                                 "quiet pattern"),
]

_DEAD = [
    (r"\bghosting\b",                                              "ghosting"),
    (r"\bghosted\b",                                               "ghosted"),
    (r"\bno (?:response|reply)\b",                                 "no response"),
    (r"\bstopped (?:replying|responding|engaging)\b",              "stopped engaging"),
    (r"\b(?:they|he|she|client|candidate|employer|company|business|hirer) moved on\b",
                                                                   "moved on"),
    (r"\bnot interested\b",                                        "stated not interested"),
    (r"\bhired (?:someone|internally)\b",                          "hired elsewhere"),
    (r"\b(?:filled|closed) (?:the )?(?:brief|role|mandate)\b",     "brief closed"),
    (r"\bwithdrew\b",                                              "candidate withdrew"),
    (r"\bdropped out\b",                                           "dropped out"),
]


_NEXT_ACTIONS = {
    "alive":   "Keep the momentum: schedule the next concrete touchpoint in the next 48h. Don't lose pace.",
    "stalled": "Name the one decision or one person blocking it. Send a single specific question, not a 'just checking in'.",
    "cold":    "Either set a hard internal deadline (chase by X date or remove) or do a fresh-angle re-open ('saw your news on X — sparked this').",
    "dead":    "Remove from active pipeline. Re-pipeline as cold lead for Q3 (or earlier if a trigger fires).",
    "unclear": "Add one line of detail: who you last spoke to, what they said, when. Triage needs context.",
}


def _match(patterns, text):
    out = []
    for rx, label in patterns:
        if re.search(rx, text, re.IGNORECASE):
            out.append(label)
    return out


def triage_line(raw: str) -> TriageRow:
    text = (raw or "").strip()
    if not text:
        return TriageRow(raw="", label="unclear", score=0,
                         reasoning="Empty line.",
                         next_action=_NEXT_ACTIONS["unclear"])

    alive_hits   = _match(_ALIVE, text)
    stalled_hits = _match(_STALLED, text)
    cold_hits    = _match(_COLD, text)
    dead_hits    = _match(_DEAD, text)

    # Dead is dominant — if any dead signal fires, label dead regardless
    # of other movement (because "ghosting after offer" is still dead).
    if dead_hits:
        label = "dead"
        score = 5
        reasoning = "Dead signal: " + ", ".join(dead_hits[:2]) + "."
    elif alive_hits and not stalled_hits and not cold_hits:
        label = "alive"
        score = 85
        reasoning = "Active signals: " + ", ".join(alive_hits[:2]) + "."
    elif alive_hits and stalled_hits:
        label = "stalled"
        score = 55
        reasoning = ("Mixed: alive signal (" + alive_hits[0] +
                     ") but blocker (" + stalled_hits[0] + "). Treat as stalled.")
    elif stalled_hits:
        label = "stalled"
        score = 40
        reasoning = "Stalled signal: " + ", ".join(stalled_hits[:2]) + "."
    elif cold_hits:
        label = "cold"
        score = 20
        reasoning = "Cold signal: " + ", ".join(cold_hits[:2]) + "."
    elif alive_hits:
        label = "alive"
        score = 75
        reasoning = "Movement signal: " + ", ".join(alive_hits[:2]) + "."
    else:
        label = "unclear"
        score = 30
        reasoning = "No movement or stall signals — needs more detail."

    return TriageRow(raw=raw, label=label, score=score,
                     reasoning=reasoning,
                     next_action=_NEXT_ACTIONS[label])


def triage_pipeline(text: str) -> list[TriageRow]:
    """Accepts free-text. Splits on newlines OR semicolons. Empty
    lines are filtered. Returns a list of TriageRow sorted by score
    descending (most alive first → most dead last so Sara works
    top-down)."""
    if not text:
        return []
    # Use newline as primary separator; semicolon only as secondary so
    # Sara can paste either format.
    lines: list[str] = []
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ";" in chunk and len(chunk) > 80:
            for piece in chunk.split(";"):
                piece = piece.strip()
                if piece:
                    lines.append(piece)
        else:
            lines.append(chunk)
    rows = [triage_line(l) for l in lines]
    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


def triage_to_json(rows: list[TriageRow]) -> dict:
    out_rows = [asdict(r) for r in rows]
    counts = {"alive": 0, "stalled": 0, "cold": 0, "dead": 0, "unclear": 0}
    for r in rows:
        counts[r.label] = counts.get(r.label, 0) + 1
    return {"rows": out_rows, "counts": counts, "total": len(rows)}
