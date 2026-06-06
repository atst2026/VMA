#!/usr/bin/env python3
"""Canonical company identity — the single source of truth that turns a raw,
untrusted company NAME into a verified identity object.

Why this exists
---------------
Logo correctness failed for years because resolution started from a fuzzy
company-name string and guessed everything downstream (search, TLD probes,
same-named entities). This module eliminates that: a pitch pack can only be
generated for a company we can resolve to a CANONICAL IDENTITY, and logo
resolution (tool/logo_service) keys off the identity's VERIFIED DOMAIN /
verified logo URL — never the raw name.

Guarantees
----------
* Resolution is EXACT only — by internal id, canonical name, a registered
  alias, or the normalised slug of those. There is no fuzzy / partial /
  "closest" matching, so a name can never resolve to the wrong company.
* An unknown company raises ``UnknownCompanyError`` — the caller must fail,
  not guess. Adding a company is a one-line, human-verified registry entry
  (name + domain), which is the only sanctioned "enrichment" path.

Each entry is a ``Company`` with:
  id      stable internal unique id (the slug; never changes once shipped)
  name    canonical display name
  domain  the company's verified official domain (required wherever possible)
  logo_url  OPTIONAL verified logo asset (the company's own/hosted file). When
            present it is the highest-confidence source and is used before any
            domain-derived source.
  aliases   extra exact spellings / trading names / acronyms that map here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class UnknownCompanyError(Exception):
    """Raised when a company name cannot be resolved to a canonical identity.
    Logo resolution (and pitch-pack generation) MUST fail rather than guess."""


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    domain: str | None = None
    logo_url: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


def slugify(value: str) -> str:
    """Normalise a name/id to its alphanumeric slug, e.g.
    'Oxford Quantum Circuits' -> 'oxfordquantumcircuits', 'M&S' -> 'ms'."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# ======================================================================
# The registry. id == slug of the canonical name. Domains are held with high
# confidence; a WRONG domain here would defeat the whole system, so the bar for
# an entry is "verified". Extend it with one line per company.
# (logo_url is pinned only where a specific verified asset is known.)
# ======================================================================
_COMPANIES: tuple[Company, ...] = (
    # ---- Deep-tech / startup BD radar (the hard cases) -----------------
    Company("oqc", "OQC", "oqc.tech",
            logo_url="https://oqc.tech/wp-content/uploads/2024/09/OQC-Logo-White.svg",
            aliases=("oxford quantum circuits",)),
    Company("geordieai", "Geordie AI", "geordie.ai", aliases=("geordie",)),
    Company("quantinuum", "Quantinuum", "quantinuum.com"),
    Company("riverlane", "Riverlane", "riverlane.com"),
    Company("wayve", "Wayve", "wayve.ai"),
    Company("synthesia", "Synthesia", "synthesia.io"),
    Company("graphcore", "Graphcore", "graphcore.ai"),
    Company("darktrace", "Darktrace", "darktrace.com"),
    Company("monzo", "Monzo", "monzo.com"),
    Company("revolut", "Revolut", "revolut.com"),
    # ---- FMCG / consumer ----------------------------------------------
    Company("belron", "Belron", "belron.com"),
    Company("diageo", "Diageo", "diageo.com", aliases=("diageo plc",)),
    Company("unilever", "Unilever", "unilever.com", aliases=("unilever plc",)),
    Company("haleon", "Haleon", "haleon.com"),
    Company("reckitt", "Reckitt", "reckitt.com", aliases=("reckitt benckiser",)),
    Company("nestle", "Nestlé", "nestle.com", aliases=("nestle", "nestle uk")),
    Company("loreal", "L'Oréal", "loreal.com", aliases=("l'oreal", "loreal", "loreal uk")),
    Company("pepsico", "PepsiCo", "pepsico.com"),
    Company("cocacola", "Coca-Cola", "coca-cola.com", aliases=("coca cola",)),
    Company("mars", "Mars", "mars.com"),
    Company("mondelez", "Mondelez", "mondelezinternational.com",
            aliases=("mondelez international",)),
    # ---- Retail -------------------------------------------------------
    Company("tesco", "Tesco", "tesco.com", aliases=("tesco plc",)),
    Company("sainsburys", "Sainsbury's", "sainsburys.co.uk", aliases=("j sainsbury",)),
    Company("marksandspencer", "Marks & Spencer", "marksandspencer.com",
            aliases=("marks and spencer", "m&s", "marks & spencer")),
    Company("johnlewis", "John Lewis Partnership", "johnlewis.com",
            aliases=("john lewis",)),
    Company("kingfisher", "Kingfisher", "kingfisher.com", aliases=("kingfisher plc",)),
    Company("next", "Next", "next.co.uk", aliases=("next plc",)),
    Company("ocado", "Ocado", "ocadogroup.com", aliases=("ocado group",)),
    Company("greggs", "Greggs", "greggs.co.uk", aliases=("greggs plc",)),
    Company("burberry", "Burberry", "burberry.com"),
    # ---- Financial services -------------------------------------------
    Company("barclays", "Barclays", "barclays.com", aliases=("barclays plc", "barclays uk")),
    Company("hsbc", "HSBC", "hsbc.com", aliases=("hsbc uk", "hsbc holdings")),
    Company("natwest", "NatWest", "natwest.com", aliases=("natwest group", "rbs")),
    Company("lloyds", "Lloyds Banking Group", "lloydsbankinggroup.com",
            aliases=("lloyds", "lloyds bank")),
    Company("aviva", "Aviva", "aviva.com", aliases=("aviva plc",)),
    Company("legalandgeneral", "Legal & General", "legalandgeneral.com",
            aliases=("legal and general", "l&g")),
    Company("prudential", "Prudential", "prudentialplc.com", aliases=("pru",)),
    Company("santanderuk", "Santander UK", "santander.co.uk", aliases=("santander uk",)),
    # ---- Energy / utilities -------------------------------------------
    Company("bp", "BP", "bp.com", aliases=("bp plc",)),
    Company("shell", "Shell", "shell.com", aliases=("shell plc",)),
    Company("centrica", "Centrica", "centrica.com", aliases=("british gas",)),
    Company("severntrent", "Severn Trent", "severntrent.co.uk"),
    Company("unitedutilities", "United Utilities", "unitedutilities.com"),
    Company("nationalgrid", "National Grid", "nationalgrid.com"),
    Company("sse", "SSE", "sse.com", aliases=("sse plc",)),
    # ---- Pharma / healthcare ------------------------------------------
    Company("gsk", "GSK", "gsk.com", aliases=("glaxosmithkline",)),
    Company("astrazeneca", "AstraZeneca", "astrazeneca.com", aliases=("astra zeneca",)),
    # ---- Telecoms / media ---------------------------------------------
    Company("vodafone", "Vodafone", "vodafone.com", aliases=("vodafone uk", "vodafone group")),
    Company("bt", "BT", "bt.com", aliases=("bt group", "british telecom")),
    Company("virginmediao2", "Virgin Media O2", "virginmediao2.co.uk", aliases=("vmo2",)),
    Company("virginmedia", "Virgin Media", "virginmedia.com"),
    Company("sky", "Sky", "sky.com", aliases=("sky uk", "sky group")),
    Company("itv", "ITV", "itv.com", aliases=("itv plc",)),
    Company("bbc", "BBC", "bbc.co.uk", aliases=("british broadcasting corporation",)),
    Company("wpp", "WPP", "wpp.com", aliases=("wpp plc",)),
    Company("relx", "RELX", "relx.com", aliases=("relx group", "reed elsevier")),
    Company("pearson", "Pearson", "pearson.com", aliases=("pearson plc",)),
    Company("informa", "Informa", "informa.com", aliases=("informa plc",)),
    # ---- Industrials / transport / aerospace --------------------------
    Company("rollsroyce", "Rolls-Royce", "rolls-royce.com", aliases=("rolls royce",)),
    Company("baesystems", "BAE Systems", "baesystems.com", aliases=("bae",)),
    Company("gknautomotive", "GKN Automotive", "gknautomotive.com", aliases=("gkn",)),
    Company("astonmartin", "Aston Martin", "astonmartin.com",
            aliases=("aston martin lagonda",)),
    Company("jaguarlandrover", "Jaguar Land Rover", "jaguarlandrover.com", aliases=("jlr",)),
    Company("heathrow", "Heathrow", "heathrow.com", aliases=("heathrow airport",)),
    Company("iag", "International Airlines Group", "iairgroup.com", aliases=("iag",)),
    Company("britishairways", "British Airways", "britishairways.com", aliases=("ba",)),
    Company("riotinto", "Rio Tinto", "riotinto.com"),
    Company("glencore", "Glencore", "glencore.com"),
    Company("compassgroup", "Compass Group", "compass-group.com", aliases=("compass",)),
    Company("whitbread", "Whitbread", "whitbread.com", aliases=("premier inn",)),
    # ---- Professional services ----------------------------------------
    Company("deloitte", "Deloitte", "deloitte.com"),
    Company("ey", "EY", "ey.com", aliases=("ernst & young", "ernst and young")),
    Company("kpmg", "KPMG", "kpmg.com"),
    Company("pwc", "PwC", "pwc.com", aliases=("pricewaterhousecoopers",)),
    Company("arup", "Arup", "arup.com"),
    # ---- International (on the VMA roster) -----------------------------
    Company("abnamro", "ABN AMRO", "abnamro.com", aliases=("abn amro",)),
    Company("hilton", "Hilton", "hilton.com", aliases=("hilton hotels",)),
)


def _build_index() -> dict[str, Company]:
    idx: dict[str, Company] = {}

    def _claim(key: str, company: Company) -> None:
        key = (key or "").strip().lower()
        if not key:
            return
        existing = idx.get(key)
        if existing is not None and existing is not company:
            raise ValueError(
                f"company-identity key collision on {key!r}: "
                f"{existing.id} vs {company.id}")
        idx[key] = company

    for c in _COMPANIES:
        for key in (c.id, c.name, *c.aliases):
            _claim(key, c)            # exact (lowercased)
            _claim(slugify(key), c)   # slug
    return idx


_INDEX = _build_index()


def resolve(name_or_id: str) -> Company:
    """Resolve a raw company name (or id/alias) to its canonical identity by
    EXACT match only. Raises UnknownCompanyError if it isn't in the registry —
    callers must fail rather than guess."""
    if not name_or_id or not name_or_id.strip():
        raise UnknownCompanyError("empty company name")
    key = name_or_id.strip().lower()
    hit = _INDEX.get(key) or _INDEX.get(slugify(name_or_id))
    if hit is None:
        raise UnknownCompanyError(
            f"{name_or_id!r} is not a known company. Add a verified entry "
            f"(name + domain) to tool/company_identity.py to enable it.")
    return hit


def is_known(name_or_id: str) -> bool:
    try:
        resolve(name_or_id)
        return True
    except UnknownCompanyError:
        return False
