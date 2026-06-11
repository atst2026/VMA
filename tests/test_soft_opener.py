"""Build 1 (demand-side): the soft opener and the call-ammo pack.

The opener's contract is DISCIPLINE: the engine knows the trigger, the
predicted seat and the window, and the opener must reveal none of them —
acknowledge change vaguely, offer sector insight, ask for a conversation.
"""
from tool.dashboard import draft_outreach_for_predictor
import tool.call_ammo as CA


def _pred(key, label, company="IMI", seat="Corporate Affairs Director"):
    return {"company": company, "predicted_role": seat,
            "window_label": "8-24 weeks",
            "events": [{"trigger_key": key, "trigger_label": label,
                        "published": "2026-06-01T00:00:00+00:00"}]}


def _assert_gives_nothing_away(text, seat, label):
    low = text.lower()
    assert seat.lower() not in low              # never the predicted seat
    assert label.lower() not in low             # never the trigger label
    assert "window" not in low and "weeks" not in low   # never the clock
    assert "retained" not in low and "mandate" not in low  # never the pitch
    assert "shortlist" not in low and "search" not in low
    # and it makes the script's two moves:
    assert "conversation" in low                # the low-stakes ask
    assert "who's moving" in low or "sector" in low  # the insight offer


def test_leadership_opener_is_vague_change():
    t = draft_outreach_for_predictor(_pred("ceo_change", "CEO change"))
    _assert_gives_nothing_away(t, "Corporate Affairs Director", "CEO change")
    assert "things are changing at IMI" in t


def test_capital_opener_congratulates_without_specifics():
    t = draft_outreach_for_predictor(
        _pred("secured_financing", "Share allotment (capital raise)"))
    _assert_gives_nothing_away(t, "Corporate Affairs Director",
                               "Share allotment (capital raise)")
    assert "exciting period" in t
    assert "allotment" not in t.lower() and "raise" not in t.lower()


def test_crisis_opener_is_brief_and_kind():
    t = draft_outreach_for_predictor(_pred("crisis_event", "Crisis event"))
    assert "a lot on at IMI" in t and "keep this short" in t
    assert "crisis" not in t.lower()


def test_public_hiring_may_be_acknowledged():
    t = draft_outreach_for_predictor(_pred("job_ad_cluster", "Hiring cluster"))
    assert "building out the team" in t
    assert "cluster" not in t.lower()


# ---- call ammo ---------------------------------------------------------
def test_ammo_names_live_peer_moves_and_fills_with_sector_context(monkeypatch):
    import tool.peers as P
    monkeypatch.setattr(P, "detect_sector", lambda c: "water")
    monkeypatch.setattr(P, "peers_for",
                        lambda c, k=15: (["Severn Trent", "United Utilities"],
                                         "water"))
    monkeypatch.setattr(CA, "_predictors", lambda: {
        "p1": {"company": "Severn Trent", "events": [
            {"trigger_label": "CFO change",
             "published": "2026-06-01T00:00:00+00:00"}]}})
    import tool.sector_context as SC
    monkeypatch.setattr(SC, "strategic_context",
                        lambda key, prof: ["Curated water-sector driver."])
    out = CA.sector_insights("Thames Water")
    assert any("Severn Trent (cfo change, Jun)" in b for b in out)
    assert any("Curated water-sector driver." in b for b in out)
    assert len(out) <= 3


def test_ammo_never_raises_on_unknown_company():
    assert isinstance(CA.sector_insights("Zzyzx Nonexistent Ltd"), list)
