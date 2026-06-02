"""Trigger -> ordered role-priority chain.

Refines linkedin_resolver.ROLE_FOR_PREDICTOR_TRIGGER's single-role
mapping into an ordered list of role slots the resolver can walk through,
so a missing CCO falls back to GC/CHRO/CEO rather than skipping straight
to a generic search URL.

Specific points the chain encodes:
  - IPO/listing: CFO is primary signatory but GC is the second one at
    pre-IPO firms (regulated-disclosure responsibility). CEO is third.
  - Regulator action: not split by financial/operational subtype (v1).
    Single chain CCO -> GC -> CHRO -> CEO handles both reasonably.
  - Head of IC: decision-maker is rarely the CEO. CHRO -> CCO ->
    Head of Corporate Affairs, with CEO only as fourth fallback for
    very flat orgs.
  - Comms leader departure: still CHRO-first, CEO second; the role is
    the same shape regardless of trigger evidence path.
"""
from __future__ import annotations

# Trigger key -> ordered role-slot priority list. Use the FIRST slot
# that has a fresh ContactEntry; fall back through the list otherwise.
TRIGGER_ROLE_CHAIN: dict[str, tuple[str, ...]] = {
    # Comms leader has departed -> CHRO holds the brief, CEO is involved
    # at FTSE-listed and PE-backed firms alike.
    "comms_leader_departure":  ("chro", "cco", "ceo"),

    # IC platform RFP / case-study / adjacent job ad -> Head of IC role
    # follows. CEO almost never signs off; CHRO is the call.
    "ic_platform_rfp":         ("chro", "head_of_ic", "cco", "head_of_corporate_affairs", "ceo"),

    # Pre-IPO / listing -> CFO is primary, GC second (regulated-disclosure
    # signatory), CEO third.
    "ipo_listing":             ("cfo", "gc", "ceo"),

    # CEO change -> CHRO leads C-suite review; the new CEO will be hands-on
    # within 6 months.
    "ceo_change":              ("chro", "cco", "ceo"),

    # M&A -> CCO at the acquirer is the primary, Head of Corp Affairs at
    # both sides; CHRO is integration-side fallback.
    "mna":                     ("cco", "head_of_corporate_affairs", "chro", "ceo"),

    # Regulator action -> CCO leads crisis comms, GC is the second
    # signatory for any regulator-facing hire, CHRO for non-financial
    # operational matters, CEO last resort.
    "regulator_action":        ("cco", "gc", "chro", "ceo"),
    # Early probe / crisis event -> same crisis-comms routing as a
    # material regulator action (CCO leads, GC second signatory).
    "regulator_probe_early":   ("cco", "gc", "chro", "ceo"),
    "crisis_event":            ("cco", "gc", "chro", "ceo"),
    # Profit warning -> IR Director leads the market narrative, CCO for
    # reputation, CFO as the numbers owner.
    "profit_warning":          ("ir_director", "cco", "cfo", "ceo"),

    # Material contract loss -> CCO reposition + reputation defence.
    "contract_loss":           ("cco", "head_of_comms", "ceo"),

    # Chair change -> chair-CEO-CPO triangle handles director-level
    # comms moves. CHRO first, CEO second.
    "chair_change":            ("chro", "ceo", "cco"),

    # CFO change -> drives investor-narrative refresh -> Head of IR
    # paired with Corporate Affairs. CFO themselves first.
    "cfo_change":              ("cfo", "ir_director", "cco", "ceo"),

    # New Head of IR -> the new IR-D first; paired Corporate Affairs
    # hire follows.
    "ir_director_change":      ("ir_director", "cfo", "cco"),

    # New CHRO -> the new CHRO themselves first (often actively
    # reshaping the function on arrival).
    "chro_change":              ("chro", "ceo", "cco"),

    # Restructure / strategic review -> CHRO leads, then CCO for
    # narrative work.
    "restructure":             ("chro", "cco", "ceo"),

    # Press velocity spike -> indicates a comms team running hot;
    # CCO/Head of Comms is the call.
    "press_velocity_spike":    ("cco", "head_of_comms", "chro"),

    # Job-ad cluster -> CHRO/HR Director is the buyer; CCO is the
    # eventual reporting line for senior comms hires.
    "job_ad_cluster":          ("chro", "cco", "head_of_comms"),
}

# Default chain when a trigger key isn't recognised.
DEFAULT_CHAIN = ("cco", "head_of_comms", "chro", "ceo")

# Pretty titles for surfacing to Sara. The role-slot is internal-only;
# what shows in the dashboard is this human-readable label.
ROLE_SLOT_DISPLAY = {
    "ceo":                       "Chief Executive Officer",
    "chair":                     "Chair",
    "cfo":                       "Chief Financial Officer",
    "cco":                       "Chief Communications Officer",
    "chro":                      "Chief People Officer",
    "gc":                        "General Counsel",
    "head_of_comms":             "Head of Communications",
    "head_of_corporate_affairs": "Head of Corporate Affairs",
    "head_of_ic":                "Head of Internal Communications",
    "ir_director":               "Head of Investor Relations",
}

# Profile override (FIRST DRAFT). Comms keeps the live chains above;
# marketing routes the same events to marketing seats (cmo / head_of_brand
# / head_of_marketing), keeping the universal C-suite fallbacks. Marketing
# contacts aren't seeded yet, so an unseeded slot falls through to a role
# search for the right title — which is exactly what we want for marketing.
from tool.profiles import active_profile as _active_profile
if _active_profile().key == "marketing":
    TRIGGER_ROLE_CHAIN = {
        "comms_leader_departure":  ("cmo", "head_of_marketing", "head_of_brand", "ceo"),
        "ic_platform_rfp":         ("head_of_marketing", "cmo", "ceo"),
        "ipo_listing":             ("cmo", "head_of_brand", "cfo", "ceo"),
        "ceo_change":              ("cmo", "head_of_marketing", "ceo"),
        "mna":                     ("cmo", "head_of_brand", "ceo"),
        "regulator_action":        ("cmo", "head_of_marketing", "ceo"),
        "regulator_probe_early":   ("cmo", "head_of_marketing", "ceo"),
        "crisis_event":            ("cmo", "head_of_marketing", "ceo"),
        "profit_warning":          ("cmo", "cfo", "ceo"),
        "contract_loss":           ("cmo", "head_of_marketing", "ceo"),
        "chair_change":            ("cmo", "ceo"),
        "cfo_change":              ("cfo", "cmo", "ceo"),
        "ir_director_change":      ("ir_director", "cmo", "ceo"),
        "chro_change":             ("chro", "cmo", "ceo"),
        "restructure":             ("cmo", "head_of_marketing", "ceo"),
        "press_velocity_spike":    ("cmo", "head_of_marketing"),
        "job_ad_cluster":          ("head_of_marketing", "cmo", "ceo"),
    }
    DEFAULT_CHAIN = ("cmo", "head_of_marketing", "head_of_brand", "ceo")
    ROLE_SLOT_DISPLAY = {
        **ROLE_SLOT_DISPLAY,
        "cmo":               "Chief Marketing Officer",
        "head_of_marketing": "Head of Marketing",
        "head_of_brand":     "Head of Brand",
        "head_of_growth":    "Head of Growth",
    }


def role_priority_for_trigger(trigger_key: str) -> tuple[str, ...]:
    """Return the ordered role-slot priority list for a given trigger."""
    return TRIGGER_ROLE_CHAIN.get(trigger_key, DEFAULT_CHAIN)


def pick_contact_for_trigger(card, trigger_key: str):
    """Walk the priority chain for `trigger_key`, return the first fresh
    ContactEntry on `card`. Returns (entry, role_slot_used) or
    (None, role_slot_first_in_chain) if nothing fresh.

    Stale entries are NOT returned — better to fall back to the Recruiter
    search than name someone who may have left.
    """
    if card is None:
        chain = role_priority_for_trigger(trigger_key)
        return None, chain[0]
    chain = role_priority_for_trigger(trigger_key)
    for slot in chain:
        entry = card.get(slot)
        if entry and entry.is_fresh() and entry.meets_named_confidence():
            return entry, slot
    return None, chain[0]


def display_title_for_slot(role_slot: str) -> str:
    return ROLE_SLOT_DISPLAY.get(role_slot, role_slot.replace("_", " ").title())
