"""MPC Outreach Factory.

The "Most Placeable Candidate" workflow inverts reverse_match. Reverse
Match takes a candidate and ranks Sara's account universe by fit; MPC
Factory takes the same input but emits an **outbound hit list** — for
each of the top N accounts, a single-paragraph evidence-cited talking
track that Sara can paste into a LinkedIn message or a calling note.

The hooks aren't generic. Each one is woven from whatever the data
already knows about that account:
  * recent_signal       — latest morning-brief signal (RNS, news, etc.)
  * predictor_signal    — active predictor at the account (with prob)
  * distress_signal     — distress hit at the account (profit warning,
                          activist, restructuring, etc.)
  * leadership_change   — leadership-change in recent_signals
  * peer_signal         — same-sector account had a recent signal
  * generic_fit         — falls through to a sector-fit hook when no
                          live signal exists

A hit list of 20 accounts where the hook is the candidate's value to
the prospect ("your competitor just hired X out of [team]") is the
single highest-leverage move in a dead market. Even if only 2 of the
20 convert to a conversation, that's 2 conversations Sara wouldn't
have otherwise had.

No external API calls. Reads state files only.
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

from tool.distress_signals import filter_distress, category_label

log = logging.getLogger("brief.mpc")

STATE_DIR = Path(__file__).resolve().parent / "state"


@dataclass
class MPCAccountHit:
    """One account on the candidate's hit list."""
    account: str
    score: float
    hook_kind: str           # see module docstring for the menu
    hook: str                # one-paragraph talking track
    evidence_url: str = ""   # link Sara can open to verify
    leadership: list[str] = field(default_factory=list)


@dataclass
class MPCCandidate:
    """Sara's input. All fields free-text."""
    name: str
    current_company: str
    current_title: str
    sectors: list[str] = field(default_factory=list)   # ['banking', 'insurance']
    seniority: str = ""        # 'head of', 'director', 'CCO', etc.
    specialism: str = ""       # 'internal comms', 'IR', 'crisis', 'M&A'
    notes: str = ""            # anything Sara wants to add


# Sector heuristics for the cross-sector hit list. If candidate is at
# HSBC, the strongest hits are at other banks. If candidate is at GSK,
# the strongest hits are at pharma. The seeded accounts cover ~10
# clusters; everything else falls through to "general PLC" matching.
SECTOR_BUCKETS = {
    "banking":        ["HSBC", "NatWest Group", "Lloyds Banking Group", "Barclays",
                       "Standard Chartered"],
    "insurance":      ["Aviva", "Legal & General", "Phoenix Group"],
    "asset_mgmt":     ["Schroders", "Legal & General"],
    "pharma":         ["AstraZeneca", "GSK", "Haleon"],
    "health":         ["Bupa UK", "NHS England", "Haleon"],
    "energy":         ["BP", "Shell", "SSE", "National Grid"],
    "utilities":      ["Severn Trent", "United Utilities", "National Grid", "SSE"],
    "telco":          ["BT Group", "Vodafone"],
    "tech":           ["Microsoft", "Google", "Sage Group"],
    "media_public":   ["BBC"],
    "government":     ["Cabinet Office", "FCA", "NHS England"],
    "charity":        ["Macmillan Cancer Support"],
    "defence":        ["BAE Systems"],
}


def _normalise(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def _detect_sectors(candidate: MPCCandidate) -> list[str]:
    """Infer sector tags from `current_company` + free-text notes. Sara
    can also pass explicit `sectors` which override inference."""
    if candidate.sectors:
        return [s.lower() for s in candidate.sectors]
    haystack = " ".join([
        candidate.current_company or "",
        candidate.notes or "",
        candidate.specialism or "",
    ]).lower()
    tags: list[str] = []
    sector_hints = {
        "banking":      ["bank", "hsbc", "natwest", "lloyds", "barclays", "santander",
                         "monzo", "starling", "revolut"],
        "insurance":    ["insur", "aviva", "phoenix", "legal & general", "l&g", "prudential"],
        "asset_mgmt":   ["asset management", "fund manager", "schroders", "blackrock", "vanguard"],
        "pharma":       ["pharma", "biotech", "astrazeneca", "gsk", "glaxo", "haleon", "pfizer"],
        "health":       ["health", "nhs", "bupa", "hospital", "clinical"],
        "energy":       ["oil", "gas", "energy", "bp", "shell", "centrica", "octopus",
                         "national grid", "sse"],
        "utilities":    ["utilit", "water", "severn", "united utilit", "thames water"],
        "telco":        ["telco", "telecom", "bt group", "vodafone", "ee ", "virgin media"],
        "tech":         ["tech", "software", "saas", "microsoft", "google", "amazon",
                         "stripe", "sage", "cloud"],
        "media_public": ["bbc", "channel 4", "itv", "publish"],
        "government":   ["civil service", "cabinet office", "treasury", "whitehall",
                         "fca", "ofcom", "regulator"],
        "charity":      ["charity", "macmillan", "cancer research", "mind", "barnardo"],
        "defence":      ["bae", "defence", "rolls-royce", "babcock", "qinetiq"],
    }
    for tag, hints in sector_hints.items():
        if any(h in haystack for h in hints):
            tags.append(tag)
    return tags


def _account_seeded(name: str, contacts: dict) -> bool:
    """True if `name` is in the contacts seeded watchlist."""
    target = _normalise(name)
    for k in contacts:
        if _normalise(k) == target:
            return True
    return False


def _leadership_for(name: str, contacts: dict) -> list[str]:
    target = _normalise(name)
    for k, v in contacts.items():
        if _normalise(k) != target:
            continue
        if not isinstance(v, dict):
            return []
        # contacts shape: {role: [{name, source, verified_at}, ...]}
        out = []
        for role, entries in v.items():
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict) and first.get("name"):
                    out.append(f"{role}: {first['name']}")
        return out[:5]
    return []


def _word_match(target: str, *fields: str) -> bool:
    """Word-boundary substring match of `target` (already normalised)
    against each of the given fields. Three rules, in order:

      A. target appears whole-word inside field
         ('hsbc' inside 'hsbc holdings plc' → True;
          'hsbc' inside 'ahsbc industries'  → False — no whitespace
          boundary)
      B. field appears whole-word inside target
         (short-form signal company 'natwest' against long-form
          account 'natwest group')
      C. target's first word appears whole-word inside field
         (account 'natwest group' against a signal that mentions
          'natwest' alongside other text)
    """
    if not target:
        return False
    target_pat = re.compile(r"(?:^|\s)" + re.escape(target) + r"(?:\s|$)")
    words = target.split()
    first_word_pat = None
    if len(words) > 1:
        first_word_pat = re.compile(r"(?:^|\s)" + re.escape(words[0]) + r"(?:\s|$)")
    for f in fields:
        if not f:
            continue
        if target_pat.search(f):
            return True
        # Rule B: short-form field substring of long-form target
        f_clean = f.strip()
        if f_clean:
            field_pat = re.compile(r"(?:^|\s)" + re.escape(f_clean) + r"(?:\s|$)")
            if field_pat.search(target):
                return True
        if first_word_pat and first_word_pat.search(f):
            return True
    return False


def _signals_for(name: str, signals: list[dict]) -> list[dict]:
    target = _normalise(name)
    matches = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        company = _normalise(s.get("company") or "")
        title   = _normalise(s.get("title") or "")
        if _word_match(target, company, title):
            matches.append(s)
    return matches


def _predictors_for(name: str, predictors: list[dict]) -> list[dict]:
    """Match by word-boundary substring so a predictor stored under the
    canonical name ('HSBC HOLDINGS PLC') still matches the dashboard's
    short form ('HSBC') and vice versa."""
    target = _normalise(name)
    return [
        p for p in predictors
        if isinstance(p, dict)
        and p.get("status") != "dismissed"
        and _word_match(target, _normalise(p.get("company") or ""))
    ]


def _build_hook(candidate: MPCCandidate, account: str,
                signals: list[dict], predictors: list[dict],
                distress: list[dict],
                leadership: list[str]) -> tuple[str, str, str]:
    """Returns (hook_kind, hook_text, evidence_url). Selects the
    strongest live signal at the account and weaves it into a one-
    paragraph outbound talking track grounded in the candidate's
    profile.
    """
    name = candidate.name
    cur_co = candidate.current_company
    cur_title = candidate.current_title
    specialism = candidate.specialism or "comms"

    # 1. Distress signal — highest-priority hook in a dead market
    if distress:
        d = distress[0]
        cat_label = category_label(d.get("_distress_category", ""))
        title = (d.get("title") or "").strip()
        url = (d.get("url") or "")
        return (
            "distress_signal",
            f"Hook into the {cat_label.lower()} at {account} (\"{title[:100]}\"). "
            f"Frame {name} ({cur_title} at {cur_co}) as the {specialism} "
            f"operator they need *right now* — sub-text: when peers are running "
            f"distress comms cycles, this is exactly the profile that has done it "
            f"under pressure. Lead with the news, not the candidate.",
            url,
        )

    # 2. Active predictor — second-highest priority
    if predictors:
        p = predictors[0]
        events = p.get("events") or []
        trigger = (events[0].get("trigger_label") if events else "") or "recent trigger"
        prob = p.get("probability", "?")
        window = p.get("window_label") or "soon"
        return (
            "predictor_signal",
            f"VMA's predictor scores {account} at {prob}% likelihood of a comms "
            f"hire within {window} — the trigger is {trigger.lower()}. "
            f"{name} ({cur_title} at {cur_co}) fits the profile: surface them now, "
            f"before the brief becomes public and three competitors are in.",
            "",
        )

    # 3. Leadership change in recent signals
    leadership_change = next(
        (s for s in signals if (s.get("kind") or "").lower() in ("appointment", "leadership_change")),
        None,
    )
    if leadership_change:
        title = (leadership_change.get("title") or "").strip()
        url = (leadership_change.get("url") or "")
        return (
            "leadership_change",
            f"{account} just had a leadership move (\"{title[:90]}\"). New leaders "
            f"reshape their teams within 6 months — get {name} ({cur_title}, {cur_co}) "
            f"in front of them in the first 90 days while team-build is on the agenda.",
            url,
        )

    # 4. Other recent signal (RNS, news, regulator)
    if signals:
        s = signals[0]
        title = (s.get("title") or "").strip()
        url = (s.get("url") or "")
        return (
            "recent_signal",
            f"Use {account}'s recent disclosure as the opener: \"{title[:110]}\". "
            f"Pivot to {name} ({cur_title} at {cur_co}) as a {specialism} hire who's "
            f"already done this exact rhythm at scale. Specific > generic — reference "
            f"the disclosure, not a sector platitude.",
            url,
        )

    # 5. Generic sector-fit fallback
    cur_co_short = cur_co.split()[0] if cur_co else "current employer"
    return (
        "generic_fit",
        f"No live signal at {account}, but the structural fit is strong: {name} runs "
        f"{specialism} at {cur_co_short}, which is operationally similar. Lead with "
        f"a 'thought you'd want to know they're open to a conversation' message — "
        f"low-friction, plants the seed for when they next refresh comms.",
        "",
    )


def build_hit_list(candidate: MPCCandidate,
                   top_n: int = 20,
                   contacts_path: Path | None = None,
                   signals_path: Path | None = None,
                   predictors_path: Path | None = None,
                   distress_path: Path | None = None) -> list[MPCAccountHit]:
    """Build a per-account hit list for the candidate.

    Top-N is the number of accounts to score (default 20 per the
    critique). Accounts are taken from hiring_contacts.json's Tier-A
    universe (30 accounts). The list is ranked by hook strength —
    distress > predictor > leadership change > recent signal > generic.

    Distress hooks are sourced from latest_distress.json (the raw
    pre-rank scour, classified) — NOT from latest_signals.json, which
    rank() has already stripped of every non-comms-keyword signal.
    Without this, distress hooks would almost never fire on real data.
    """
    contacts_path   = contacts_path or STATE_DIR / "hiring_contacts.json"
    signals_path    = signals_path or STATE_DIR / "latest_signals.json"
    predictors_path = predictors_path or STATE_DIR / "latest_predictive.json"
    distress_path   = distress_path or STATE_DIR / "latest_distress.json"

    contacts: dict = {}
    signals: list[dict] = []
    predictors: list[dict] = []
    distress_all: list[dict] = []
    try:
        if contacts_path.exists():
            contacts = json.loads(contacts_path.read_text()) or {}
    except Exception as e:
        log.info("hiring_contacts load failed: %s", e)
    try:
        if signals_path.exists():
            signals = json.loads(signals_path.read_text()) or []
    except Exception as e:
        log.info("latest_signals load failed: %s", e)
    try:
        if predictors_path.exists():
            predictors = json.loads(predictors_path.read_text()) or []
    except Exception as e:
        log.info("latest_predictive load failed: %s", e)
    try:
        if distress_path.exists():
            distress_all = json.loads(distress_path.read_text()) or []
    except Exception as e:
        log.info("latest_distress load failed: %s", e)

    sectors = _detect_sectors(candidate)

    # Build the candidate universe: same-sector accounts first, then
    # other Tier-A accounts. Excludes the candidate's current employer.
    same_sector_accounts: list[str] = []
    seen: set[str] = set()
    for tag in sectors:
        for a in SECTOR_BUCKETS.get(tag, []):
            if _normalise(a) == _normalise(candidate.current_company):
                continue
            if a not in seen:
                seen.add(a)
                same_sector_accounts.append(a)

    other_accounts: list[str] = []
    for a in contacts:
        if a.startswith("_"):
            continue
        if a in seen:
            continue
        if _normalise(a) == _normalise(candidate.current_company):
            continue
        other_accounts.append(a)

    candidate_universe = same_sector_accounts + other_accounts

    # Distress feed is pre-classified upstream. If we somehow got an
    # unclassified list (old artifact), classify defensively.
    if distress_all and not (
        isinstance(distress_all[0], dict) and "_distress_score" in distress_all[0]
    ):
        # Already a distress feed; don't re-apply the account gate (the
        # per-account _signals_for narrowing below handles relevance).
        distress_all = filter_distress(distress_all, require_account=False)

    hits: list[MPCAccountHit] = []
    for account in candidate_universe:
        acc_signals = _signals_for(account, signals)
        # Distress hooks come from the dedicated feed, matched per
        # account — NOT from filter_distress(acc_signals), which was
        # starved because acc_signals is the comms-filtered set.
        acc_distress = _signals_for(account, distress_all)
        acc_predictors = _predictors_for(account, predictors)
        leadership = _leadership_for(account, contacts)
        kind, hook, url = _build_hook(
            candidate, account, acc_signals, acc_predictors, acc_distress, leadership
        )
        # Scoring: distress (1.0), predictor (0.9), leadership change (0.7),
        # recent signal (0.5), generic (0.2). Same-sector accounts get
        # +0.2 boost so the top of the list is sector-relevant.
        base_score = {
            "distress_signal":    1.0,
            "predictor_signal":   0.9,
            "leadership_change":  0.7,
            "recent_signal":      0.5,
            "generic_fit":        0.2,
        }[kind]
        score = base_score + (0.2 if account in same_sector_accounts else 0.0)

        hits.append(MPCAccountHit(
            account=account,
            score=round(score, 2),
            hook_kind=kind,
            hook=hook,
            evidence_url=url,
            leadership=leadership,
        ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_n]


def hit_list_to_json(hits: list[MPCAccountHit]) -> list[dict]:
    return [asdict(h) for h in hits]
