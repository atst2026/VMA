"""Objection & negotiation coach.

Sara types in a short situation ("client says our 22% fee is too high",
"candidate just said 'I'm not sure'", "competitor matched salary"). The
coach matches against the VMA-specific playbook in tool.config and
returns the top matching situation(s) with three angles each.

Zero LLM dependency, zero state. Pure pattern match on the playbook.
The value isn't AI per se — it's that the playbook is curated VMA
language (cost-to-replace, 80% counter-offer-leavers, sub-shortlist
exclusivity) so Sara doesn't reinvent the talk track every time.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict

from tool.config import OBJECTION_PLAYBOOK


@dataclass
class ObjectionResponse:
    matched_situation: str
    angles: list[str] = field(default_factory=list)
    match_confidence: float = 0.0    # 0-1


def coach(situation: str, top_n: int = 2) -> list[ObjectionResponse]:
    """Return the top-N best-matching playbook situations for the
    pasted text. Each match gets a confidence score = number of pattern
    keywords hit / 6 (capped at 1.0).

    If no playbook entry hits, returns a single generic-prompt response
    that asks Sara to specify the situation more concretely.

    Resilient to malformed playbook entries: a bad regex or missing
    key skips the entry and continues, rather than crashing the whole
    coach() call."""
    text = (situation or "").strip().lower()
    if not text:
        return []
    out: list[ObjectionResponse] = []
    for entry in OBJECTION_PLAYBOOK:
        if not isinstance(entry, dict):
            continue
        pat   = entry.get("pattern")
        label = entry.get("label")
        angles = entry.get("angles") or []
        if not pat or not label or not angles:
            continue
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            continue
        m = rx.search(text)
        if not m:
            continue
        # Crude confidence: number of distinct keywords from the
        # situation that the pattern's intended for, hit in the input.
        confidence = min(1.0, len(m.group(0).split()) / 6)
        out.append(ObjectionResponse(
            matched_situation=label,
            angles=list(angles),
            match_confidence=round(confidence, 2),
        ))

    if not out:
        # Strict regexes missed. Fall back to keyword/intent scoring so
        # natural phrasings of the SAME 7 situations still resolve
        # ("they think we're expensive" → fee pushback) without an LLM.
        # A multi-word phrase hit, or >=2 distinct keyword hits, is
        # enough; a single short generic token ("offer", "cost") is not.
        scored: list[tuple[int, float, ObjectionResponse]] = []
        for entry in OBJECTION_PLAYBOOK:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            angles = entry.get("angles") or []
            kws = entry.get("keywords") or []
            if not label or not angles or not kws:
                continue
            hit_terms = [k for k in kws if isinstance(k, str) and k and k in text]
            if not hit_terms:
                continue
            strong = any((" " in k or "-" in k or len(k) >= 6) for k in hit_terms)
            if len(hit_terms) < 2 and not strong:
                continue
            # Fuzzy confidence, deliberately capped below strict-regex
            # matches so the UI signals it's an inferred match.
            conf = round(min(0.6, 0.25 + 0.12 * len(hit_terms)), 2)
            scored.append((len(hit_terms), conf,
                           ObjectionResponse(matched_situation=label,
                                             angles=list(angles),
                                             match_confidence=conf)))
        if scored:
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            return [r for _, _, r in scored][:top_n]

    if not out:
        return [ObjectionResponse(
            matched_situation="No exact match in playbook",
            angles=[
                "Pin the specific objection before answering: 'when you say X, "
                "what specifically is driving that?' Most objections are surface-"
                "level proxies for a deeper concern (control, trust, fit).",
                "Match the medium: if they raised this in writing, respond in "
                "writing with the structured answer. If they raised it on a call, "
                "answer on a call. Channel-switching loses sub-text.",
                "Have one fact, one frame, one ask. Don't argue. Concede whatever "
                "is true, reframe what's missing, propose the smallest next step.",
            ],
            match_confidence=0.0,
        )]
    out.sort(key=lambda r: r.match_confidence, reverse=True)
    return out[:top_n]


def coach_to_json(responses: list[ObjectionResponse]) -> list[dict]:
    return [asdict(r) for r in responses]
