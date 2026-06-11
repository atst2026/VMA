"""RNS enquiries-block CAPTURE — the warm-up half of the diffing signal.

Every RNS announcement ends with an enquiries block naming the issuer's
IR/comms contacts and usually their financial PR agency. Diffing those
names between successive announcements from the same issuer is a
registry-grade people-change signal sitting exactly on VMA's IR and
corporate comms patch — but you cannot diff one announcement. This
module is deliberately just the capture half: archive the raw block
text per announcement per issuer from day one, so when the parser and
differ land (the real build — the blocks are wildly inconsistent free
text), history already exists and the diff fires immediately instead of
six weeks later.

Store: tool/state/rns_contact_blocks.json
  {issuer_key: {"company": str,
                "blocks": [{"at", "url", "block"} ...newest-first, capped]}}
Budget: at most CAP fetches per run, RNS-kind signals only, URLs never
re-fetched. Never raises.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from tool.state_paths import state_dir

log = logging.getLogger("brief.rns_contacts")

MAX_BLOCKS_PER_ISSUER = 12
FETCH_CAP_PER_RUN = 15
BLOCK_CHARS = 1500

# The enquiries block opens with one of these headings in virtually
# every RNS house style.
_ENQ_RX = re.compile(
    r"(enquiries:?|for further information|further enquiries|"
    r"media contacts?:?|investor relations contacts?:?|contacts?:)"
    r"(.{0,%d})" % BLOCK_CHARS, re.I | re.S)
_RNS_HOST_RX = re.compile(
    r"investegate\.co\.uk|londonstockexchange\.com|rns\b", re.I)
_TAG_RX = re.compile(r"<[^>]+>")


def _file():
    return state_dir() / "rns_contact_blocks.json"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _load() -> dict:
    try:
        f = _file()
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1))


def extract_block(html_or_text: str | None) -> str | None:
    """Best-effort: the enquiries block as plain text, or None."""
    if not html_or_text:
        return None
    text = _TAG_RX.sub(" ", html_or_text)
    text = re.sub(r"\s+", " ", text)
    m = _ENQ_RX.search(text)
    if not m:
        return None
    block = (m.group(1) + m.group(2)).strip()
    # A real block names people/organisations — demand some substance.
    return block if len(block) > 60 else None


def _default_fetch(url: str) -> str | None:
    try:
        from tool.sources._http import get
        r = get(url, timeout=15)
        return r.text if r is not None and getattr(r, "status_code", 0) == 200 else None
    except Exception:
        return None


def capture_from_signals(signals: list[dict], fetch=None,
                         cap: int = FETCH_CAP_PER_RUN) -> int:
    """Archive enquiries blocks for today's RNS-kind signals. Returns the
    number of new blocks stored. Never raises."""
    try:
        fetch = fetch or _default_fetch
        store = _load()
        seen_urls = {b.get("url") for rec in store.values()
                     for b in rec.get("blocks", [])}
        now = datetime.now(timezone.utc).isoformat()
        fetched = stored = 0
        for sig in signals or []:
            if fetched >= cap:
                break
            if not isinstance(sig, dict):
                continue
            url = (sig.get("url") or "").strip()
            company = (sig.get("company") or "").strip()
            if (not url or not company or url in seen_urls
                    or (sig.get("kind") != "rns"
                        and not _RNS_HOST_RX.search(url))):
                continue
            fetched += 1
            block = extract_block(fetch(url))
            if not block:
                continue
            key = _norm(company)
            rec = store.setdefault(key, {"company": company, "blocks": []})
            rec["blocks"].insert(0, {"at": now, "url": url, "block": block})
            rec["blocks"] = rec["blocks"][:MAX_BLOCKS_PER_ISSUER]
            seen_urls.add(url)
            stored += 1
        if stored:
            _save(store)
        log.info("rns_contacts: %d enquiries blocks archived "
                 "(%d pages fetched)", stored, fetched)
        return stored
    except Exception as e:
        log.info("rns_contacts capture skipped (%s)", e)
        return 0


# ---- Published-email parsing (the second half of the build) -----------
# The blocks archived above name the issuer's IR/comms contacts and very
# often print their work email verbatim — a registry-grade, citable
# source for the SEND OUTREACH email layer. Parsing is conservative:
# every address comes back with the URL it was published at and a
# name hint from the surrounding text, and the caller decides what is
# strong enough to store.

_EMAIL_RX = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-']*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Capitalised two/three-word runs in the text before the address — the
# pattern enquiries blocks almost always follow ("Jane Smith, Head of
# Communications  jane.smith@issuer.com"). The LAST person-like run
# before the address wins (the company name and the role title sit
# earlier in the same clause and must lose).
_NAME_RUN_RX = re.compile(
    r"[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,2}")
# Tokens that mark a capitalised run as a role / org / boilerplate, not
# a person ("Severn Trent Plc", "Media Relations", "Investor Contact").
_NOT_A_NAME_TOKENS = frozenset(
    t.lower() for t in (
        "Head", "Director", "Chief", "Group", "Media", "Investor",
        "Relations", "Communications", "Corporate", "Affairs", "Press",
        "Office", "Enquiries", "Contact", "Contacts", "Manager",
        "Officer", "Plc", "Limited", "Ltd", "Holdings", "Financial",
        "Public", "Further", "Information", "Tel", "Telephone", "Email",
    ))
# Obvious non-person mailboxes; still returned (a press office inbox is
# a legitimate fallback recipient) but never attributed to a name.
_GENERIC_LOCAL = (
    "press", "media", "enquiries", "info", "ir", "investor",
    "communications", "comms", "pressoffice", "media.relations", "pr",
)


def _name_hint(text_before: str) -> str:
    tail = (text_before or "").strip()[-100:]
    best = ""
    for m in _NAME_RUN_RX.finditer(tail):
        words = m.group(0).split()
        if any(w.lower() in _NOT_A_NAME_TOKENS for w in words):
            continue
        best = m.group(0).strip()
    return best


def _company_tokens(company: str) -> set:
    return {t for t in _norm(company).split() if len(t) >= 3}


def published_emails(company: str) -> list[dict]:
    """All addresses ever printed in `company`'s archived enquiries
    blocks, newest block first, deduped on the address:

      [{email, name_hint, generic, in_house, url, at}]

    `in_house` is a heuristic: the address's domain shares a token with
    the company name (jane@firstgroup.com for FirstGroup). Agencies'
    addresses (their financial PR firm) come back in_house=False so the
    caller can prefer the issuer's own people. Never raises."""
    try:
        rec = _load().get(_norm(company))
        if not rec:
            return []
        toks = _company_tokens(company)
        out, seen = [], set()
        for blk in rec.get("blocks") or []:
            text = blk.get("block") or ""
            for m in _EMAIL_RX.finditer(text):
                email = m.group(0).strip(".,;:'")
                low = email.lower()
                if low in seen:
                    continue
                seen.add(low)
                local, _, domain = low.partition("@")
                domain_core = _norm(domain.rsplit(".", 1)[0]
                                    if "." in domain else domain)
                generic = any(local == g or local.startswith(g + ".")
                              or local.startswith(g + "-")
                              for g in _GENERIC_LOCAL)
                in_house = any(t in domain_core.replace(" ", "")
                               for t in toks)
                out.append({
                    "email": email,
                    "name_hint": "" if generic
                                 else _name_hint(text[:m.start()]),
                    "generic": generic,
                    "in_house": in_house,
                    "url": blk.get("url") or "",
                    "at": blk.get("at") or "",
                })
        return out
    except Exception as e:
        log.info("rns_contacts published_emails skipped (%s)", e)
        return []
