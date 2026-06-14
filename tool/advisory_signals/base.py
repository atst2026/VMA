"""The typed AdvisorySignal every advisory detector emits.

Deliberately lean and JSON-serialisable (the platform's row-dict
convention): a detector produces these, `tool.advisory_gate` consumes
them, and `tool.evidence_pack` renders them. It carries the facts a
reasoned verdict needs — the evidenced pain, the likely buyer, the dated
"why now", the evidence trail, the predicted service mix — but holds NO
score: ranking is the gate's job (a verdict, not an additive sum).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class AdvisorySignal:
    """One originated advisory-demand signal.

    trigger        detector class, e.g. "PayGapActionMandate"
    company        employer name
    service_mix    ranked VMA service keys (tool.advisory.SERVICES)
    pain           the evidenced functional pain, one line
    buyer_hint     the likely economic buyer ROLE (not a named person)
    why_now        the dated compelling event
    evidence       [{source, url}] — drives source-independence grading
    window         (start_iso, end_iso) of the live action window, or None
    confidence     0-1 detector confidence in the signal itself
    company_number Companies House number when known (helps the resolver)
    extra          detector-specific payload (band, gap %, pulse key …)
    """
    trigger: str
    company: str
    service_mix: list[str] = field(default_factory=list)
    pain: str = ""
    buyer_hint: str = ""
    why_now: str = ""
    evidence: list[dict] = field(default_factory=list)
    window: tuple[str, str] | None = None
    confidence: float = 0.5
    company_number: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def n_source_families(self) -> int:
        """Distinct evidence sources (registrable host, else source label)
        — the source-independence count the gate reads."""
        fams = set()
        for e in self.evidence or []:
            url = (e.get("url") or "").strip()
            host = ""
            if url:
                host = urlparse(url).netloc.lower()
                host = host[4:] if host.startswith("www.") else host
            fams.add(host or (e.get("source") or "").strip().lower())
        fams.discard("")
        return len(fams)

    def as_events(self) -> list[dict]:
        """Evidence shaped as the {source, url} events `tool.gate`'s
        source_evidence() grades — so advisory reuses the house grader
        (gov.uk / Companies House / RNS = primary) unchanged."""
        return [{"source": e.get("source") or "", "url": e.get("url") or ""}
                for e in (self.evidence or [])]

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger,
            "company": self.company,
            "service_mix": list(self.service_mix or []),
            "pain": self.pain,
            "buyer_hint": self.buyer_hint,
            "why_now": self.why_now,
            "evidence": [dict(e) for e in (self.evidence or [])],
            "window": list(self.window) if self.window else None,
            "confidence": self.confidence,
            "company_number": self.company_number,
            "extra": dict(self.extra or {}),
        }
