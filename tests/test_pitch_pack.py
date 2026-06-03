"""Tests for the Pitch Pack upgrade:

  * affinity talent universe (a global drinks brand is shown brand houses,
    not grocers) WITHOUT disturbing the ranker's sector/sector-heat,
  * the generic-fallback guard (no FTSE list shown to an off-sector account),
  * the never-blank Section 2 (sector-level context instead of the old
    "check trade press manually" dead-end),
  * the retained fee quoted on TOTAL COMP (one basis, not anchored on base),
  * the function-split cost-of-vacancy (reputational for comms, pipeline for
    marketing; event-anchored when a trigger is supplied),
  * the reconciled 6-week-vs-time-to-productive timeline.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---- affinity talent universe ------------------------------------------

def test_diageo_gets_brand_houses_not_grocers():
    from tool.peers import pitch_peers_for
    pm = pitch_peers_for("Diageo", k=15)
    assert pm["source"] == "affinity"
    assert pm["key"] == "global_consumer_brands"
    peers = pm["peers"]
    assert "Unilever" in peers and "Reckitt" in peers and "Haleon" in peers
    # The demonstrated bug: grocers shown as a drinks brand's move-from set.
    for grocer in ("Greggs", "B&Q", "WH Smith", "Tesco"):
        assert grocer not in peers, f"{grocer} leaked into Diageo's universe"


def test_cohorts_separate_cleanly():
    from tool.peers import pitch_peers_for
    assert pitch_peers_for("Tesco")["key"] == "grocery_retail"
    assert pitch_peers_for("Burberry")["key"] == "premium_luxury"
    assert pitch_peers_for("Barclays")["key"] == "uk_banks"
    assert pitch_peers_for("Schroders")["key"] == "asset_wealth_managers"
    assert pitch_peers_for("Monzo")["key"] == "fintech_challengers"


def test_target_excluded_from_its_own_universe():
    from tool.peers import pitch_peers_for
    assert all(p.lower() != "diageo" for p in pitch_peers_for("Diageo")["peers"])


def test_sector_company_uses_sector_peers():
    # Severn Trent's energy_utilities list is already coherent -> sector, not
    # a forced affinity cohort.
    from tool.peers import pitch_peers_for
    pm = pitch_peers_for("Severn Trent")
    assert pm["source"] == "sector"
    assert pm["key"] == "energy_utilities"


def test_generic_fallback_is_flagged():
    # An off-sector account must be flagged generic so the pack can REFUSE to
    # show an irrelevant FTSE list.
    from tool.peers import pitch_peers_for
    pm = pitch_peers_for("Riverside Housing Association")
    assert pm["source"] == "generic"
    assert pm["key"] is None


# ---- ranker safety: affinity layer must NOT touch sector/sector-heat ----

def test_affinity_does_not_change_ranker_sector():
    from tool.peers import detect_sector, sector_heat_multiplier
    # Diageo still classifies as retail_consumer for the ranker, with its
    # existing heat weight — the affinity layer is Pitch-Pack-only.
    assert detect_sector("Diageo") == "retail_consumer"
    assert sector_heat_multiplier("Diageo") == sector_heat_multiplier("Tesco")


# ---- Section 2: curated breadth + never-blank fallback ------------------

def test_diageo_now_has_curated_priorities():
    from tool.pre_meeting import _load_curated_priorities
    assert _load_curated_priorities("Diageo"), "Diageo should now be curated"


def test_sector_context_is_profile_aware():
    from tool import sector_context
    comms = sector_context.strategic_context("global_consumer_brands", "comms")
    mktg = sector_context.strategic_context("global_consumer_brands", "marketing")
    assert comms and mktg and comms != mktg
    # comms speaks reputation/ESG; marketing speaks brand/growth.
    assert any("ESG" in c or "trust" in c.lower() for c in comms)
    assert any("brand" in m.lower() or "growth" in m.lower() for m in mktg)


def test_sector_context_covers_broad_sectors_and_cohorts():
    from tool import sector_context
    from tool.peers import SECTOR_PEERS, PITCH_AFFINITY_GROUPS
    for key in list(SECTOR_PEERS) + list(PITCH_AFFINITY_GROUPS):
        if key == "international":
            continue  # international has no UK-demand narrative
        assert sector_context.strategic_context(key, "comms"), f"no comms context for {key}"
        assert sector_context.strategic_context(key, "marketing"), f"no mktg context for {key}"


def test_sector_context_unknown_key_is_none():
    from tool import sector_context
    assert sector_context.strategic_context(None) is None
    assert sector_context.strategic_context("not_a_sector") is None


# ---- fee on total comp -------------------------------------------------

def test_fee_quoted_on_total_comp_not_base():
    from tool.pitch_pack import estimate_total_comp
    base_mid = 107_500           # Diageo Head of IC midpoint
    total = estimate_total_comp(base_mid)
    assert total > base_mid       # base + bonus/LTIP uplift
    fee_low = round(0.28 * total, -2)
    fee_high = round(0.33 * total, -2)
    # The old base-only headline was £30,100–£35,500; total-comp lifts it.
    assert fee_low > 0.28 * base_mid
    assert fee_high > 35_500


# ---- function-split cost of vacancy ------------------------------------

def test_cov_comms_is_reputational():
    from tool.pitch_pack import cost_of_vacancy
    cov = cost_of_vacancy("Head of Communications", 110_000, frame="comms")
    assert cov["frame"] == "comms"
    assert "reputational" in cov["headline"].lower()
    assert cov["total"] == sum(v for k, v in cov["lines"].items()
                               if "leaving the seat empty" not in k.lower())
    assert "Cost of leaving the seat empty" in cov["lines"]


def test_cov_marketing_is_pipeline():
    from tool.pitch_pack import cost_of_vacancy
    cov = cost_of_vacancy("Head of Marketing", 110_000, frame="marketing")
    assert cov["frame"] == "marketing"
    h = cov["headline"].lower()
    assert "pipeline" in h or "demand" in h or "growth" in h
    assert "productivity" not in h    # the old generic frame is gone


def test_cov_is_event_anchored_when_trigger_supplied():
    from tool.pitch_pack import cost_of_vacancy
    cov = cost_of_vacancy("Head of Communications", 110_000, frame="comms",
                          trigger_context="half-year results eight weeks out")
    assert "half-year results eight weeks out" in cov["headline"]


def test_cov_window_is_time_to_productive():
    from tool.pitch_pack import cost_of_vacancy, TIME_TO_PRODUCTIVE_WEEKS
    cov = cost_of_vacancy("Head of Communications", 110_000)
    assert cov["weeks"] == TIME_TO_PRODUCTIVE_WEEKS == 18


# ---- end-to-end render invariants --------------------------------------

def _render(target, role="Head of Communications", curated=True, frame="comms"):
    from tool import pitch_pack as pp
    from tool import peers, sector_context
    from tool.pre_meeting import _load_curated_priorities
    pm = peers.pitch_peers_for(target, k=15)
    sal = pp._salary_band(role)
    cov = pp.cost_of_vacancy(role, (sal[0] + sal[1]) // 2, frame=frame)
    cur = _load_curated_priorities(target) if curated else []
    sc = (sector_context.strategic_context(pm["key"], frame)
          if not cur else None)
    ch = {"found": True, "resolved": {"company_number": "X", "company_status": "active"}}
    return pp.render_html(target, role, ch, [], pm["peers"],
                          peers.detect_sector(target), sal, cov, "preview",
                          annual_report=None, curated_priorities=cur,
                          peer_label=pm["label"], peer_source=pm["source"],
                          sector_context=sc), pm


def test_render_never_prints_defeatist_dead_end():
    # A cohort member that ISN'T curated must still get a useful Section 2.
    html, pm = _render("Mondelez", curated=False)
    assert "check trade press manually" not in html
    assert "sector-level" in html.lower()


def test_render_diageo_is_clean_and_total_comp():
    # Head of Internal Communications == the role in the real artifact that
    # showed the £30,100–£35,500 base-only fee; total-comp lifts it.
    html, pm = _render("Diageo", role="Head of Internal Communications")
    assert "check trade press manually" not in html
    assert "Greggs" not in html and "B&Q" not in html
    assert "Unilever" in html
    assert "total comp" in html.lower()
    assert "£36,100" in html and "£42,600" in html   # fee on total comp (was £30,100–£35,500)
    assert "Global consumer brands" in html
    assert "productive start" in html               # timeline reconciled


def test_render_generic_guard_hides_ftse_list():
    html, pm = _render("Riverside Housing Association", curated=False)
    assert pm["source"] == "generic"
    assert "Barclays" not in html and "BP" not in html
    assert "not auto-detected" in html.lower()
