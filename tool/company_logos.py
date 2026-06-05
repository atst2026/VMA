#!/usr/bin/env python3
"""Authoritative, offline-first company-logo registry + local override.

The pitch-pack cover must carry the TARGET company's real logo. Live web
resolution (search -> site scrape -> logo service) is inherently fuzzy: it can
land a *same-named* company's site, grab a partner / award badge instead of the
brand mark, or return nothing at all. Those are exactly the three reported cover
failures (wrong company, right company-wrong logo, missing logo). This module is
the DETERMINISTIC source of truth that sits IN FRONT of that fuzzy path:

  * a LOCAL OVERRIDE folder ``tool/assets/company_logos/<slug>.<ext>``: drop a
    verified logo file for any account and it is used verbatim, offline, every
    time — the unequivocal guarantee for the accounts Sara actually pitches. No
    network, no heuristics, no chance of a wrong logo.

  * a curated REGISTRY that pins each known account's EXACT official domain (and
    optionally an authoritative logo asset), so the logo is fetched from the
    RIGHT company's site / logo service and a same-named entity can never be
    substituted.

Everything here is pure / offline (the only network is the caller's best-effort
fetch from the pinned domain). Adding an account is a one-line registry entry or
a single dropped file. Nothing in this module raises; callers treat a miss as
"fall through to the live resolver".
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("company_logos")

# Where a human drops verified per-company logo files. A file named for the
# company's slug (e.g. "diageo.png", "oxfordquantumcircuits.svg") wins outright.
OVERRIDE_DIR = Path(__file__).resolve().parent / "assets" / "company_logos"

# Accepted logo file extensions, best (vector / lossless) first.
_EXTS = (".svg", ".png", ".webp", ".jpg", ".jpeg", ".gif")


def slugify(name: str) -> str:
    """Collapse a company name to its alphanumeric slug — the same normalisation
    logo_finder uses, so "Oxford Quantum Circuits" -> "oxfordquantumcircuits"
    and "L'Oréal" -> "loral". Used to key both the override files and the
    registry."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ======================================================================
# Curated registry: company slug -> {domain, aliases?, logo?}
#
#   domain   the EXACT official website domain. This is the lever that stops
#            "wrong company entirely": resolution uses this verbatim instead of
#            guessing, so the logo is fetched from the real company's site /
#            logo service.
#   aliases  extra spellings that should map to the same entry (trading names,
#            "<name> plc", acronyms, common variants).
#   logo     OPTIONAL authoritative logo asset, tried before any scrape:
#              "local:<filename>"  a file committed under OVERRIDE_DIR
#              "https://..."        a stable hosted asset
#              "wikimedia:<File>"   a Wikimedia Commons file (Special:FilePath)
#            Omit it to simply resolve the logo from the pinned domain.
#
# Only domains held with high confidence are listed — a WRONG domain here would
# itself produce a wrong logo, so the bar for inclusion is "certain", and the
# long tail is served by the live resolver (now made conservative) or a dropped
# override file.
# ======================================================================
REGISTRY: dict[str, dict] = {
    # ---- FMCG / consumer ------------------------------------------------
    "belron": {"domain": "belron.com"},
    "diageo": {"domain": "diageo.com", "aliases": ["diageo plc"]},
    "unilever": {"domain": "unilever.com", "aliases": ["unilever plc"]},
    "haleon": {"domain": "haleon.com"},
    "reckitt": {"domain": "reckitt.com", "aliases": ["reckitt benckiser", "rb"]},
    "nestle": {"domain": "nestle.com", "aliases": ["nestlé", "nestle uk"]},
    "loreal": {"domain": "loreal.com", "aliases": ["l'oreal", "l'oréal", "loreal uk"]},
    "pepsico": {"domain": "pepsico.com"},
    "cocacola": {"domain": "coca-cola.com", "aliases": ["coca-cola", "coca cola"]},
    "mars": {"domain": "mars.com"},
    "mondelez": {"domain": "mondelezinternational.com", "aliases": ["mondelez"]},
    # ---- Retail ---------------------------------------------------------
    "tesco": {"domain": "tesco.com", "aliases": ["tesco plc"]},
    "sainsburys": {"domain": "sainsburys.co.uk", "aliases": ["sainsbury's", "j sainsbury"]},
    "marksandspencer": {"domain": "marksandspencer.com",
                        "aliases": ["marks & spencer", "marks and spencer", "m&s"]},
    "johnlewis": {"domain": "johnlewis.com",
                  "aliases": ["john lewis", "john lewis partnership"]},
    "kingfisher": {"domain": "kingfisher.com", "aliases": ["kingfisher plc"]},
    "next": {"domain": "next.co.uk", "aliases": ["next plc"]},
    "ocado": {"domain": "ocadogroup.com", "aliases": ["ocado", "ocado group"]},
    "greggs": {"domain": "greggs.co.uk", "aliases": ["greggs plc"]},
    "burberry": {"domain": "burberry.com"},
    # ---- Financial services --------------------------------------------
    "barclays": {"domain": "barclays.com", "aliases": ["barclays plc", "barclays uk"]},
    "hsbc": {"domain": "hsbc.com", "aliases": ["hsbc uk", "hsbc holdings"]},
    "natwest": {"domain": "natwest.com", "aliases": ["natwest group", "rbs"]},
    "lloyds": {"domain": "lloydsbankinggroup.com",
               "aliases": ["lloyds", "lloyds bank", "lloyds banking group"]},
    "aviva": {"domain": "aviva.com", "aliases": ["aviva plc"]},
    "legalandgeneral": {"domain": "legalandgeneral.com",
                        "aliases": ["legal & general", "legal and general", "l&g"]},
    "prudential": {"domain": "prudentialplc.com", "aliases": ["prudential", "pru"]},
    "santanderuk": {"domain": "santander.co.uk", "aliases": ["santander uk", "santander"]},
    "monzo": {"domain": "monzo.com"},
    "revolut": {"domain": "revolut.com"},
    # ---- Energy / utilities --------------------------------------------
    "bp": {"domain": "bp.com", "aliases": ["bp plc"]},
    "shell": {"domain": "shell.com", "aliases": ["shell plc", "royal dutch shell"]},
    "centrica": {"domain": "centrica.com", "aliases": ["british gas"]},
    "severntrent": {"domain": "severntrent.co.uk", "aliases": ["severn trent"]},
    "unitedutilities": {"domain": "unitedutilities.com", "aliases": ["united utilities"]},
    "nationalgrid": {"domain": "nationalgrid.com", "aliases": ["national grid"]},
    "sse": {"domain": "sse.com", "aliases": ["sse plc"]},
    # ---- Pharma / healthcare -------------------------------------------
    "gsk": {"domain": "gsk.com", "aliases": ["glaxosmithkline"]},
    "astrazeneca": {"domain": "astrazeneca.com", "aliases": ["astra zeneca"]},
    # ---- Telecoms / media ----------------------------------------------
    "vodafone": {"domain": "vodafone.com", "aliases": ["vodafone uk", "vodafone group"]},
    "bt": {"domain": "bt.com", "aliases": ["bt group", "british telecom"]},
    "virginmediao2": {"domain": "virginmediao2.co.uk", "aliases": ["virgin media o2", "vmo2"]},
    "virginmedia": {"domain": "virginmedia.com", "aliases": ["virgin media"]},
    "sky": {"domain": "sky.com", "aliases": ["sky uk", "sky group"]},
    "itv": {"domain": "itv.com", "aliases": ["itv plc"]},
    "bbc": {"domain": "bbc.co.uk", "aliases": ["british broadcasting corporation"]},
    "wpp": {"domain": "wpp.com", "aliases": ["wpp plc"]},
    "relx": {"domain": "relx.com", "aliases": ["relx group", "reed elsevier"]},
    "pearson": {"domain": "pearson.com", "aliases": ["pearson plc"]},
    "informa": {"domain": "informa.com", "aliases": ["informa plc"]},
    # ---- Industrials / transport / aerospace ---------------------------
    "rollsroyce": {"domain": "rolls-royce.com", "aliases": ["rolls-royce", "rolls royce"]},
    "baesystems": {"domain": "baesystems.com", "aliases": ["bae systems", "bae"]},
    "gknautomotive": {"domain": "gknautomotive.com", "aliases": ["gkn automotive", "gkn"]},
    "astonmartin": {"domain": "astonmartin.com",
                    "aliases": ["aston martin", "aston martin lagonda"]},
    "jaguarlandrover": {"domain": "jaguarlandrover.com", "aliases": ["jaguar land rover", "jlr"]},
    "heathrow": {"domain": "heathrow.com", "aliases": ["heathrow airport"]},
    "iag": {"domain": "iairgroup.com",
            "aliases": ["international airlines group", "international consolidated airlines"]},
    "britishairways": {"domain": "britishairways.com", "aliases": ["british airways", "ba"]},
    "riotinto": {"domain": "riotinto.com", "aliases": ["rio tinto"]},
    "glencore": {"domain": "glencore.com"},
    "compassgroup": {"domain": "compass-group.com", "aliases": ["compass group", "compass"]},
    "whitbread": {"domain": "whitbread.com", "aliases": ["premier inn"]},
    # ---- Professional services -----------------------------------------
    "deloitte": {"domain": "deloitte.com"},
    "ey": {"domain": "ey.com", "aliases": ["ernst & young", "ernst and young"]},
    "kpmg": {"domain": "kpmg.com"},
    "pwc": {"domain": "pwc.com", "aliases": ["pricewaterhousecoopers", "pwc uk"]},
    "arup": {"domain": "arup.com"},
    # ---- Banking (international, seen on the VMA roster) -----------------
    "abnamro": {"domain": "abnamro.com", "aliases": ["abn amro"]},
    "hilton": {"domain": "hilton.com", "aliases": ["hilton hotels"]},
    # ---- Deep-tech / startup BD radar (the hard cases) -----------------
    "oqc": {"domain": "oqc.tech", "aliases": ["oxford quantum circuits"]},
    "geordieai": {"domain": "geordie.ai", "aliases": ["geordie ai", "geordie"]},
    "quantinuum": {"domain": "quantinuum.com"},
    "riverlane": {"domain": "riverlane.com"},
    "wayve": {"domain": "wayve.ai"},
    "synthesia": {"domain": "synthesia.io"},
    "graphcore": {"domain": "graphcore.ai"},
    "darktrace": {"domain": "darktrace.com"},
}


# Alias / slug indexes, built once at import.
def _build_indexes() -> tuple[dict[str, dict], dict[str, dict]]:
    by_slug: dict[str, dict] = {}
    by_alias: dict[str, dict] = {}
    # Pass 1: canonical slugs win outright.
    for slug, entry in REGISTRY.items():
        by_slug[slug] = entry
        by_alias[slug] = entry
    # Pass 2: aliases fill in, but never clobber a canonical slug / alias already
    # claimed by another entry (first registered wins; a collision is logged).
    for slug, entry in REGISTRY.items():
        for alias in entry.get("aliases", ()):
            akey = (alias or "").strip().lower()
            aslug = slugify(alias)
            for index, key in ((by_alias, akey), (by_slug, aslug)):
                if not key:
                    continue
                if key in index and index[key] is not entry:
                    log.info("registry alias %r collides with an existing entry; "
                             "keeping the first", alias)
                    continue
                index[key] = entry
    return by_slug, by_alias


_BY_SLUG, _BY_ALIAS = _build_indexes()


def lookup(company: str) -> dict | None:
    """The registry entry for a company by slug or alias, or None. Matching is
    exact on the normalised slug / lowercased alias, so "Diageo", "Diageo PLC"
    and "diageo" all hit the same entry but an unrelated name never does."""
    if not company:
        return None
    slug = slugify(company)
    if slug and slug in _BY_SLUG:
        return _BY_SLUG[slug]
    key = company.strip().lower()
    return _BY_ALIAS.get(key)


def registry_domain(company: str) -> str | None:
    """The pinned official domain for a known company, or None."""
    e = lookup(company)
    return e.get("domain") if e else None


def registry_logo_url(company: str) -> str | None:
    """An authoritative hosted logo URL pinned for the company, or None. A
    "local:" asset is served by ``local_logo`` (offline), not here."""
    e = lookup(company)
    if not e:
        return None
    lg = (e.get("logo") or "").strip()
    if lg.startswith(("http://", "https://")):
        return lg
    if lg.startswith("wikimedia:"):
        return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                + quote(lg[len("wikimedia:"):]) + "?width=512")
    return None


def _read_file(path: Path) -> bytes | None:
    try:
        if path.is_file():
            data = path.read_bytes()
            return data if data else None
    except Exception as e:  # unreadable / permission — treat as a miss
        log.info("override read failed %s: %s", path, e)
    return None


def local_logo(company: str) -> tuple[bytes | None, str]:
    """A human-verified logo for this company from the local override folder, or
    (None, ""). This is the unequivocal path: when a file is present it is used
    verbatim — no network, no heuristics — so the cover is guaranteed correct.

    Resolution:
      1. a registry entry whose ``logo`` is "local:<filename>";
      2. a file named for the company slug — <slug>.<ext> — under OVERRIDE_DIR.
    Returns (bytes, "local:<filename>")."""
    if not company:
        return None, ""

    entry = lookup(company)
    if entry:
        lg = (entry.get("logo") or "").strip()
        if lg.startswith("local:"):
            path = OVERRIDE_DIR / lg[len("local:"):]
            data = _read_file(path)
            if data:
                return data, f"local:{path.name}"

    slug = slugify(company)
    if slug:
        for ext in _EXTS:
            path = OVERRIDE_DIR / f"{slug}{ext}"
            data = _read_file(path)
            if data:
                return data, f"local:{path.name}"
    return None, ""
