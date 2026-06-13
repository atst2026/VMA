"""Exclusionary job-spec scan — the ED&I advisory conversation-opener.

VMA Group's ED&I / neuroinclusion advisory (RiverRoad, Where To Look)
sells an inclusive-language and candidate-journey review. The cheapest,
most concrete way into that conversation is the buyer's own job ads: a
senior brief whose wording skews heavily masculine-coded, or that carries
no accessibility / accommodation statement at all, is an evidence-anchored
hook ("your recent senior comms ad reads masculine-coded and has no
adjustment statement").

Deterministic, free, no model, no network — a lexicon scan over the job
ad text the engine already ingests (Adzuna carries the ad body in
`summary`; every source carries the title). It is ENRICHMENT, never a
lead: a masculine-coded ad does not create a signal, it adds a second,
advisory reading to a company already in the pipeline.

Hard guardrail (in line with the no-invented-claims rule): the output is a
CONVERSATION-OPENER, never a published verdict. Lexicons are blunt — the
wording is framed for Sara to verify against the live ad before she raises
it, never as a finished claim about the employer.

The coded-word lists are the open Gaucher, Friesen & Kay (2011) /
gender-decoder stems; matching is prefix-on-token, exactly as those tools
do (so "compet" matches "competitive", "competition", "compete").
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.job_spec")

# Masculine- and feminine-coded word STEMS (the open gender-decoder list,
# from Gaucher, Friesen & Kay 2011). A token is "coded" when it starts
# with one of these stems.
_MASCULINE_STEMS: tuple[str, ...] = (
    "active", "adventur", "aggress", "ambitio", "analy", "assert", "athlet",
    "autonom", "battle", "boast", "challeng", "champion", "compet", "confiden",
    "courag", "decid", "decision", "decisive", "defend", "determin", "domina",
    "driven", "fearless", "fight", "force", "greedy", "headstrong", "hierarch",
    "hostil", "impulsive", "independen", "individual", "intellect", "lead",
    "logic", "objective", "opinion", "outspoken", "persist", "principle",
    "reckless", "self-confiden", "self-relian", "self-sufficien",
    "selfconfiden", "selfrelian", "selfsufficien", "stubborn", "superior",
    "unreasonab",
)
_FEMININE_STEMS: tuple[str, ...] = (
    "agree", "affectionate", "child", "cheer", "collab", "commit", "communal",
    "compassion", "connect", "considerate", "cooperat", "co-operat", "depend",
    "emotiona", "empath", "feel", "flatterable", "gentle", "honest",
    "interpersonal", "interdependen", "interpersona", "inter-personal",
    "inter-dependen", "inter-persona", "kind", "kinship", "loyal", "modesty",
    "nag", "nurtur", "pleasant", "polite", "quiet", "respon", "sensitiv",
    "submissive", "support", "sympath", "tender", "together", "trust",
    "understand", "warm", "whin", "enthusias", "inclusive", "yield", "share",
    "sharin",
)

# Accessibility / accommodation / neuroinclusion language. Its PRESENCE is
# the second hook: a senior ad with a real body but none of this is the
# opener for the neuroinclusive candidate-journey review.
_INCLUSION_RX = re.compile(
    r"\b(?:reasonable adjustment|accommodation|accessib|neurodivers|"
    r"neuroinclus|neuro-inclus|disab|flexible working|flexible-working|"
    r"hybrid working|job ?share|we welcome applications|"
    r"under-?represented|equal opportunit|inclusiv|diversity|"
    r"screen reader|workplace adjustment|access need)\b",
    re.IGNORECASE,
)

_TOKEN_RX = re.compile(r"[a-z][a-z'\-]+", re.IGNORECASE)

# A single coded word proves nothing; require a clear, defensible skew
# before the opener is worth raising.
_MIN_MASCULINE = 4          # at least this many masculine-coded terms…
_MASCULINE_MARGIN = 4       # …and this far ahead of feminine-coded terms.

# "No inclusion language" only counts as a hook when there was a real ad
# BODY to judge — title-only sources (whose `summary` is the location) must
# never trip it. We only accumulate body text from summaries long enough to
# be a description rather than a place name.
_MIN_REAL_BODY = 120        # per-ad: a summary this long is a real body
_MIN_BODY_CHARS = 400       # company total of real body text to judge on


def _coded(tokens: list[str], stems: tuple[str, ...]) -> list[str]:
    """The tokens that start with one of the coded stems."""
    out: list[str] = []
    for t in tokens:
        if any(t.startswith(s) for s in stems):
            out.append(t)
    return out


def scan_text(text: str) -> dict:
    """Lexicon scan of one blob of ad text. Returns the coded terms found
    and whether any inclusion/accommodation language is present. Pure;
    never raises on bad input."""
    text = text or ""
    tokens = [m.group(0).lower() for m in _TOKEN_RX.finditer(text)]
    masc = _coded(tokens, _MASCULINE_STEMS)
    fem = _coded(tokens, _FEMININE_STEMS)
    return {
        "masculine": masc,
        "feminine": fem,
        "masculine_count": len(masc),
        "feminine_count": len(fem),
        "has_inclusion_language": bool(_INCLUSION_RX.search(text)),
    }


def _build_angle(agg: dict, scan: dict, fn: str,
                 masculine_skew: bool, no_inclusion: bool) -> dict:
    """Assemble the conversation-opener dict for one company. Mirrors the
    shape of gender_pay_gap.edi_angle (label/cls/line/short/url)."""
    ads = agg["ads"]
    bits: list[str] = []
    if masculine_skew:
        sample = ", ".join(sorted(set(scan["masculine"]))[:4])
        bits.append(f"wording skews masculine-coded (e.g. {sample})")
    if no_inclusion:
        bits.append("no accessibility / accommodation statement")
    finding = " and ".join(bits)
    line = (
        "Conversation-opener, not a published claim — verify against the "
        f"live ad first. Across {ads} recent {fn} ad"
        f"{'s' if ads != 1 else ''}, {finding}. An evidence-anchored hook "
        "for VMA's ED&I / neuroinclusion advisory: an inclusive-language "
        "and candidate-journey review (RiverRoad / Where To Look)."
    )
    return {
        "label": "ED&I ANGLE (job spec)",
        "cls": "edi-mid",
        "line": line,
        "short": "ED&I",
        "ads": ads,
        "masculine_terms": sorted(set(scan["masculine"]))[:8],
        "has_inclusion_language": scan["has_inclusion_language"],
        "url": agg["url"],
    }


def edi_job_spec_angle(signals: list[dict] | None) -> dict[str, dict]:
    """Per-company ED&I conversation-openers from the morning's job ads.

    Groups job signals by employer, scans the combined ad text, and emits
    an opener ONLY for a defensible skew — a clear masculine-coded lean, or
    no accessibility/accommodation language across ads with real body text.
    Title-only sources can't trip the 'no inclusion language' hook (there is
    no body to judge). Never raises; returns {} on bad input.
    """
    try:
        from tool.profiles import active_profile
        is_marketing = active_profile().key == "marketing"
    except Exception:
        is_marketing = False
    fn = "marketing" if is_marketing else "comms"

    by_company: dict[str, dict] = {}
    for s in signals or []:
        if not isinstance(s, dict) or s.get("kind") != "job":
            continue
        company = (s.get("company") or "").strip()
        if not company:
            continue
        title = s.get("title") or ""
        # `summary` carries the ad body on Adzuna; on other sources it is the
        # location only — short, so it never reaches _MIN_REAL_BODY and so
        # can't (wrongly) trip the no-inclusion hook on its own.
        body = s.get("summary") or ""
        agg = by_company.setdefault(company.lower(), {
            "company": company, "text": [], "body_chars": 0,
            "ads": 0, "url": s.get("url") or "",
        })
        agg["text"].append(f"{title} {body}")
        if len(body) >= _MIN_REAL_BODY:
            agg["body_chars"] += len(body)
        agg["ads"] += 1
        if not agg["url"]:
            agg["url"] = s.get("url") or ""

    out: dict[str, dict] = {}
    for agg in by_company.values():
        scan = scan_text(" ".join(agg["text"]))
        masc, fem = scan["masculine_count"], scan["feminine_count"]
        masculine_skew = masc >= _MIN_MASCULINE and (masc - fem) >= _MASCULINE_MARGIN
        no_inclusion = (agg["body_chars"] >= _MIN_BODY_CHARS
                        and not scan["has_inclusion_language"])
        if not (masculine_skew or no_inclusion):
            continue
        out[agg["company"]] = _build_angle(agg, scan, fn,
                                           masculine_skew, no_inclusion)
    return out


def _store_path():
    return state_dir() / "job_spec_edi.json"


def scan_and_store(signals: list[dict] | None) -> int:
    """Morning-pipeline entry point: compute the per-company openers from
    today's job signals and persist them for the dashboard / advisory
    surfaces. Returns the number of companies flagged. Never raises."""
    try:
        angles = edi_job_spec_angle(signals)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "companies": angles,
        }
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=0, default=str))
        log.info("Job-spec ED&I scan: %d companies flagged", len(angles))
        return len(angles)
    except Exception as e:
        log.info("job-spec ED&I scan failed: %s", e)
        return 0


def load_job_spec_flags() -> dict[str, dict]:
    """Read-only accessor for the persisted per-company openers (keyed by
    company display name). {} if the scan hasn't run. Never raises."""
    try:
        raw = json.loads(_store_path().read_text())
        return raw.get("companies", {}) if isinstance(raw, dict) else {}
    except Exception:
        return {}
