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
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


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


# ---- §7 reframe, backstage cleanup, currency ---------------------------

def test_no_proof_section_or_gate():
    # Section 7 "Track record / proof" and the DRAFT gate were removed — the
    # pack no longer depends on hand-maintained VMA credentials data.
    html, _ = _render("Diageo", role="Head of Internal Communications")
    assert "Track record" not in html
    assert "vma_proof.json" not in html
    assert "DRAFT — NOT FOR CLIENT" not in html


def test_no_client_language_mirror_section():
    html, _ = _render("Diageo", role="Head of Internal Communications")
    assert "Client language to mirror" not in html
    assert "lift these recurring phrases" not in html.lower()
    assert "2b." not in html


def test_section2_has_no_pipeline_confessions():
    for tgt, cur in [("Diageo", True), ("Mondelez", False)]:
        html, _ = _render(tgt, curated=cur)
        low = html.lower()
        assert "extraction unavailable" not in low
        assert "check trade press manually" not in low
        assert "no annual report quotes available" not in low


def test_section8_targets_real_competition():
    html, _ = _render("Diageo", role="Head of Internal Communications")
    assert "Why external retained search now" in html
    low = html.lower()
    assert "in-house" in low                       # not the contingent-only fight
    assert "discretion" in low or "confidential" in low
    assert "wait until it picks up" in low         # the do-nothing reframe


def test_diageo_curated_is_current():
    from tool.pre_meeting import _load_curated_priorities
    pr = " ".join(_load_curated_priorities("Diageo")).lower()
    assert "accelerate" in pr            # current cost programme
    assert "dave lewis" in pr            # current CEO
    assert "destocking shock" not in pr  # stale 2023-24 framing removed


def test_text_render_sections_and_no_mirror():
    from tool import pitch_pack as pp, peers
    from tool.pre_meeting import _load_curated_priorities
    role = "Head of Internal Communications"
    pm = peers.pitch_peers_for("Diageo", k=15)
    sal = pp._salary_band(role)
    cov = pp.cost_of_vacancy(role, (sal[0] + sal[1]) // 2)
    ch = {"found": True, "resolved": {"company_number": "X", "company_status": "active"}}
    txt = pp.render_text("Diageo", role, ch, [], pm["peers"],
                         peers.detect_sector("Diageo"), sal, cov,
                         annual_report=None,
                         curated_priorities=_load_curated_priorities("Diageo"),
                         peer_label=pm["label"], peer_source=pm["source"])
    assert "TRACK RECORD" not in txt                       # proof section gone
    assert "7. WHY EXTERNAL RETAINED SEARCH NOW" in txt
    assert "8. RISK-MITIGATION TERMS" in txt
    assert "CLIENT LANGUAGE TO MIRROR" not in txt


# ---- persona, evidence-or-hedge ----------------------------------------

def test_persona_opener_maps_and_defaults():
    from tool.pitch_pack import _persona_opener
    assert "finance" in _persona_opener("cfo", "Head of Communications", "Diageo").lower()
    assert "chief executive" in _persona_opener("ceo", "X", "Y").lower()
    assert "in-house" in _persona_opener("hrd", "X", "Y").lower()   # alias hrd->hr
    assert _persona_opener("", "X", "Y") == ""
    assert _persona_opener("nonsense", "X", "Y") == ""


def test_persona_renders_in_pack():
    from tool import pitch_pack as pp, peers
    from tool.pre_meeting import _load_curated_priorities
    role = "Head of Internal Communications"
    pm = peers.pitch_peers_for("Diageo", k=15)
    sal = pp._salary_band(role)
    cov = pp.cost_of_vacancy(role, (sal[0] + sal[1]) // 2)
    ch = {"found": True, "resolved": {"company_number": "X", "company_status": "active"}}
    html = pp.render_html("Diageo", role, ch, [], pm["peers"],
                          peers.detect_sector("Diageo"), sal, cov, "preview",
                          curated_priorities=_load_curated_priorities("Diageo"),
                          peer_label=pm["label"], peer_source=pm["source"], persona="ceo")
    assert "chief executive" in html.lower()


def test_cov_labels_are_evidenced():
    from tool.pitch_pack import cost_of_vacancy, INTERIM_DAY_RATE_GBP
    keys = " ".join(cost_of_vacancy("Head of Communications", 110_000,
                                    frame="comms")["lines"].keys()).lower()
    assert f"@ ~£{INTERIM_DAY_RATE_GBP}/day".lower() in keys   # interim rate shown
    assert "conservative 30%" in keys                          # bad-hire floor framed


def test_salary_hedged_when_unsourced():
    html, _ = _render("Diageo", role="Head of Internal Communications")
    assert "confirm against VMA's latest salary benchmarking" in html


def test_diageo_dividend_reset_current():
    from tool.pre_meeting import _load_curated_priorities
    pr = " ".join(_load_curated_priorities("Diageo")).lower()
    assert "dividend" in pr      # the Feb-2026 reset, now reflected


# ---- final pass: persona variants, fee-vs-cost, §8 event, §2 currency ----

def test_all_four_persona_variants_distinct():
    from tool.pitch_pack import _persona_opener
    outs = {p: _persona_opener(p, "Head of Communications", "Diageo", 36000, 42000, 101700)
            for p in ("ceo", "cfo", "incoming", "hr")}
    assert all(outs.values())                 # all four produce copy
    assert len(set(outs.values())) == 4       # and all distinct
    assert "chief executive" in outs["ceo"].lower()
    assert "team" in outs["incoming"].lower()
    assert "in-house" in outs["hr"].lower()


def test_cfo_persona_pays_off_fee_vs_cost():
    from tool.pitch_pack import _persona_opener
    out = _persona_opener("cfo", "Head of Communications", "Diageo",
                          36100, 42600, 101700)
    assert "£36,100" in out and "£101,700" in out   # the juxtaposition, in the opener
    assert "under half" in out


def test_fee_vs_cost_line_present_for_every_reader():
    # The juxtaposition is in §3 regardless of persona.
    html, _ = _render("Diageo", role="Head of Internal Communications")
    assert "Put together:" in html
    assert "£101,700" in html and "an empty seat costs" in html


def test_fee_vs_cost_phrase_ratio_bands():
    from tool.pitch_pack import _fee_vs_cost_phrase
    assert "under half" in _fee_vs_cost_phrase(36100, 42600, 101700)
    assert _fee_vs_cost_phrase(0, 0, 0) == ""


def test_section8_event_wired_when_trigger_supplied():
    from tool import pitch_pack as pp, peers
    from tool.pre_meeting import _load_curated_priorities
    role = "Head of Internal Communications"
    pm = peers.pitch_peers_for("Diageo", k=15)
    sal = pp._salary_band(role)
    cov = pp.cost_of_vacancy(role, (sal[0] + sal[1]) // 2)
    ch = {"found": True, "resolved": {"company_number": "X", "company_status": "active"}}
    ev = "a new-CEO Strategy Update to shareholders and employees on 6 August 2026"
    html = pp.render_html("Diageo", role, ch, [], pm["peers"],
                          peers.detect_sector("Diageo"), sal, cov, "preview",
                          curated_priorities=_load_curated_priorities("Diageo"),
                          peer_label=pm["label"], peer_source=pm["source"],
                          trigger_context=ev)
    assert "6 August 2026" in html and "own it" in html


def test_diageo_section2_corrections():
    from tool.pre_meeting import _load_curated_priorities
    pr = " ".join(_load_curated_priorities("Diageo"))
    assert "1 January 2026" in pr        # CEO start date
    assert "6 August 2026" in pr         # Strategy Update timing (wires §8)
    assert "c.$300m" in pr               # in-year savings (not just the $625m total)
    assert "c.$625m" in pr               # programme total, kept alongside
    assert "halved the dividend" in pr   # stated precisely, not "reset"
    assert "50-cent floor" in pr


# ---- cross-profile: must work for BOTH comms and marketing -------------

def test_curated_priorities_shared_across_desks():
    # Company strategic priorities are shared account data — they must resolve
    # for any desk (the bug: marketing read its namespaced dir and found none).
    from tool.pre_meeting import _load_curated_priorities
    assert _load_curated_priorities("Diageo")        # works in the default (comms) run


def test_graceful_degradation_no_blank_section2():
    # A non-marquee name pypdf can't parse, not curated -> sector context if a
    # cohort/sector resolves, else an honest prep note; never the old dead-end.
    html, pm = _render("Pets at Home", role="Head of Communications", curated=False)
    assert "check trade press manually" not in html.lower()
    assert "Why this matters now" in html            # not blank


def test_marketing_profile_end_to_end():
    """Render path under VMA_PROFILE=marketing (separate process, since the
    profile is fixed at import). Everything must be marketing-shaped."""
    code = (
        "import tool.pitch_pack as pp\n"
        "from tool.pre_meeting import _load_curated_priorities\n"
        "role = pp._DEFAULT_ROLE\n"
        "assert role == 'Head of Marketing', role\n"
        "cov = pp.cost_of_vacancy(role, 110000)\n"
        "assert cov['frame'] == 'marketing'\n"
        "h = cov['headline'].lower()\n"
        "assert ('pipeline' in h or 'growth' in h or 'demand' in h)\n"
        "assert 'productivity' not in h\n"
        "assert _load_curated_priorities('Diageo'), 'curated must load under marketing'\n"
        "assert 'brand' in pp._persona_opener('ceo', role, 'Diageo').lower()\n"
        "assert (85000, 130000, 'head of marketing') == pp._salary_band(role)\n"
        "print('MARKETING_OK')\n"
    )
    env = {**os.environ, "VMA_PROFILE": "marketing", "PYTHONPATH": _REPO}
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       cwd=_REPO, capture_output=True, text=True)
    assert "MARKETING_OK" in r.stdout, (r.stdout + "\n" + r.stderr)
