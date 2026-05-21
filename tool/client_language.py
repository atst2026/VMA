"""Client-Language Mirroring — surface a target's own recurring vocabulary
from public corporate communications and offer it as drop-in language for
the Pitch Pack.

Evidence (Retrained Search case study): the *same* methodology, framed in
the client's own language, converts materially better. The Pitch Pack
already extracts annual-report strategic quotes; this is the missing layer
that mines those quotes + recent press for the client's distinctive,
repeated phrasing so Sara can echo it verbatim.

Pure-Python, dependency-free, no external calls and no new scrape — it
operates only on public-comms text the Pitch Pack already fetches.
"""
from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")

# Function words — never the start/end of a phrase worth mirroring.
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "as", "at", "by", "from", "into", "over", "under", "this",
    "that", "these", "those", "it", "its", "we", "our", "us", "their",
    "they", "you", "your", "is", "are", "was", "were", "be", "been",
    "being", "has", "have", "had", "will", "would", "can", "could",
    "should", "may", "might", "must", "do", "does", "did", "not", "no",
    "more", "most", "than", "then", "also", "which", "who", "what",
    "when", "where", "how", "all", "any", "each", "both", "such", "so",
    "up", "out", "about", "across", "per", "via", "while", "during",
}

# Corporate / report / finance boilerplate — present in every report, so
# not distinctive client vocabulary. Excluded from phrases and terms.
_BOILERPLATE = {
    "annual", "report", "reports", "financial", "statement", "statements",
    "year", "ended", "ending", "company", "companies", "group", "plc",
    "limited", "ltd", "board", "director", "directors", "result", "results",
    "page", "pages", "note", "notes", "million", "billion", "thousand",
    "revenue", "revenues", "profit", "profits", "loss", "dividend",
    "dividends", "shareholder", "shareholders", "committee", "committees",
    "remuneration", "governance", "audit", "auditor", "auditors",
    "chairman", "chair", "chief", "executive", "officer", "interim",
    "half", "full", "quarter", "fiscal", "ifrs", "gaap", "ebitda",
    "cent", "percent", "per", "pounds", "sterling", "currency", "tax",
    "assets", "liabilities", "cash", "flow", "balance", "sheet", "income",
    "accounts", "accounting", "consolidated", "statutory", "plc.",
}

# Vocabulary a client uses to describe *itself* — the most valuable to
# mirror. Phrases / terms containing one of these get a relevance boost.
_VALUE_HINTS = {
    "purpose", "purpose-led", "purposeful", "sustainability", "sustainable",
    "responsible", "responsibly", "resilience", "resilient", "colleague",
    "colleagues", "customer", "customers", "client", "clients", "community",
    "communities", "inclusion", "inclusive", "diversity", "innovation",
    "innovative", "transformation", "transform", "growth", "trust",
    "integrity", "wellbeing", "transition", "decarbonisation", "decarbonise",
    "stakeholder", "stakeholders", "culture", "values", "ambition",
    "ambitious", "strategy", "strategic", "long-term", "value", "mission",
    "net-zero", "society", "people", "talent", "engagement", "service",
    "quality", "safety", "performance", "leadership", "vision",
}


def _sentences(texts: list[str]) -> list[str]:
    out: list[str] = []
    for t in texts:
        if not t:
            continue
        for s in re.split(r"(?<=[.!?])\s+", str(t).strip()):
            s = s.strip()
            if 12 <= len(s) <= 320:
                out.append(s)
    return out


def _tokens(sentence: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(sentence)]


def _ok_edge(tok: str) -> bool:
    """A phrase may not start or end with a stop/boilerplate token."""
    return tok not in _STOP and tok not in _BOILERPLATE


def _value_boost(tokens) -> float:
    return 1.0 if any(t in _VALUE_HINTS for t in tokens) else 0.0


def mirror_phrases(texts: list[str], top_n: int = 8) -> list[dict]:
    """Return the client's distinctive, repeated phrasing as drop-in lines.

    Each item: {"phrase", "count", "kind": "phrase"|"term", "example"}.
    `example` is the client's own sentence using it — what Sara mirrors.
    Robust to a thin corpus (a handful of quotes) and a rich one (full
    press + report text)."""
    sents = _sentences(texts)
    if not sents:
        return []

    phrase_counts: Counter[str] = Counter()
    phrase_scores: dict[str, float] = {}
    phrase_example: dict[str, str] = {}
    term_counts: Counter[str] = Counter()
    term_example: dict[str, str] = {}

    for sent in sents:
        toks = _tokens(sent)
        # Multi-word phrases (trigrams then bigrams) of contiguous tokens.
        for n in (3, 2):
            for i in range(len(toks) - n + 1):
                win = toks[i:i + n]
                if not (_ok_edge(win[0]) and _ok_edge(win[-1])):
                    continue
                if any(w in _BOILERPLATE for w in win):
                    continue
                if all(w in _STOP for w in win):
                    continue
                phrase = " ".join(win)
                phrase_counts[phrase] += 1
                phrase_scores[phrase] = (phrase_counts[phrase]
                                         + 0.5 * (n - 2)        # prefer longer
                                         + _value_boost(win))
                phrase_example.setdefault(phrase, sent)
        # Content unigrams (fallback vocabulary for thin corpora).
        for w in toks:
            if len(w) >= 5 and _ok_edge(w):
                term_counts[w] += 1
                term_example.setdefault(w, sent)

    results: list[dict] = []
    seen_words: set[str] = set()

    # 1) Repeated multi-word phrases first (the strongest mirror material).
    ranked_phrases = sorted(
        phrase_counts,
        key=lambda p: (phrase_scores.get(p, 0), len(p)),
        reverse=True,
    )
    for p in ranked_phrases:
        if len(results) >= top_n:
            break
        if phrase_counts[p] < 2 and _value_boost(p.split()) == 0:
            continue  # single-occurrence, non-value phrase = weak
        words = set(p.split())
        if words & seen_words:
            continue  # avoid near-duplicate overlapping phrases
        seen_words |= words
        results.append({"phrase": p, "count": phrase_counts[p],
                        "kind": "phrase", "example": phrase_example[p]})

    # 2) Fill remaining slots with distinctive single terms.
    ranked_terms = sorted(
        term_counts,
        key=lambda w: (term_counts[w] + _value_boost([w]) * 2),
        reverse=True,
    )
    for w in ranked_terms:
        if len(results) >= top_n:
            break
        if w in seen_words:
            continue
        if term_counts[w] < 2 and w not in _VALUE_HINTS:
            continue
        seen_words.add(w)
        results.append({"phrase": w, "count": term_counts[w],
                        "kind": "term", "example": term_example[w]})

    return results
