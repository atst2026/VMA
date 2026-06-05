"""Surface-level fixes: entity resolution (dedup) + a synthesised play."""
import tool.dashboard as d


# ---- entity resolution ----
def test_dedupe_collapses_acronym_and_fullname():
    rows = [{"company": "OQC", "_opp": 2.0},
            {"company": "Oxford Quantum Circuits", "_opp": 3.0},
            {"company": "Oxford Quantum Computing", "_opp": 1.0},
            {"company": "Tesco", "_opp": 5.0}]
    out = d._dedupe_rows(rows)
    names = [r["company"] for r in out]
    assert "Tesco" in names
    oqcish = [n for n in names if "oxford" in n.lower() or n == "OQC"]
    assert len(oqcish) == 1                       # three variants -> one row
    assert oqcish[0] in ("Oxford Quantum Circuits", "Oxford Quantum Computing")
    # keeps the highest-opp row's data
    kept = [r for r in out if r["company"] == oqcish[0]][0]
    assert kept["_opp"] == 3.0


def test_dedupe_acronym_skips_stopwords():
    # "Department for Work and Pensions" -> dwp, collapses with the acronym row
    rows = [{"company": "DWP", "_opp": 2.0},
            {"company": "Department for Work and Pensions", "_opp": 3.0}]
    out = d._dedupe_rows(rows)
    assert len(out) == 1


def test_dedupe_parent_child_alias():
    # the recruitment arm collapses into its parent body
    rows = [{"company": "Department for Work and Pensions", "_opp": 3.0},
            {"company": "Government Recruitment Service", "_opp": 1.0},
            {"company": "Tesco", "_opp": 5.0}]
    out = d._dedupe_rows(rows)
    names = [r["company"] for r in out]
    assert "Tesco" in names
    assert "Government Recruitment Service" not in names   # merged into DWP
    assert len(out) == 2


def test_dedupe_keeps_distinct_companies():
    rows = [{"company": "Tesco", "_opp": 2}, {"company": "Sainsbury's", "_opp": 1},
            {"company": "BP", "_opp": 3}, {"company": "Currys", "_opp": 4}]
    out = d._dedupe_rows(rows)
    assert len(out) == 4


# ---- the play synthesises from the lead's own signals ----
def test_opener_synthesises_cluster_lead():
    op = d.draft_outreach_for_predictor({
        "company": "DWP", "predicted_role": "Head of Internal Communications",
        "window_label": "4-12 weeks",
        "events": [{"trigger_key": "job_ad_cluster", "trigger_label": "Job-ad cluster",
                    "evidence": "2+ mid-level comms, no senior yet"}]})
    assert "DWP" in op
    assert ("mid level" in op.lower() or "mid-level" in op.lower())
    assert "4-12 weeks" in op                     # the window is used
    assert "—" not in op                          # no em dashes
    assert "brochure" not in op.lower() and "coffee" not in op.lower()


def test_opener_synthesises_leadership_lead():
    op = d.draft_outreach_for_predictor({
        "company": "Currys", "predicted_role": "Chief Marketing Officer",
        "window_label": "6-12 weeks",
        "events": [{"trigger_key": "ceo_change", "trigger_label": "CEO change",
                    "evidence": "new CEO appointed"}]})
    assert "Currys" in op and "leadership change" in op.lower()
    assert "—" not in op


def test_opener_falls_back_without_company():
    op = d.draft_outreach_for_predictor({"events": []})
    assert isinstance(op, str) and op  # default, never blank


# ---- the dossier row carries only the slimmed-down fields --------------
def test_mr_lead_fields_carries_sources_and_opener():
    from datetime import datetime, timezone, timedelta
    from tool import lead_engine as LE
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    pred = {"company": "Tesco", "account_tier": "watchlist", "last_seen": fresh,
            "outreach": "Hi (Name), ...",
            "events": [
                {"trigger_key": "chro_change", "trigger_label": "CHRO change",
                 "url": "companieshouse.gov.uk", "source": "companieshouse.gov.uk",
                 "published": fresh, "evidence": ""},
                {"trigger_key": "job_ad_cluster", "trigger_label": "Job-ad cluster",
                 "url": "ft.com", "source": "ft.com", "published": fresh, "evidence": ""},
            ]}
    pred["lead"] = LE.score_lead(pred)
    f = d._mr_lead_fields(pred)
    assert f["opener"]                                            # Draft opener has text
    assert f["stack"] and any(t.get("url") for t in f["stack"])   # View sources has URLs
    # the removed "fodder" fields no longer ship to the client
    for gone in ("prize", "chaseBy", "competitive", "proof", "objection", "corro", "sig",
                 "whoToCall", "access", "scale", "outcome", "fit", "fitWhy", "relationship"):
        assert gone not in f


def test_seat_resolves_per_desk_at_render_time():
    # The same trigger must resolve to a desk-correct seat (a comms seat must
    # never leak onto the Marketing Radar).
    from tool import predictor_pipeline as PP
    assert PP.role_for_trigger_keys({"ceo_change"}, desk="comms") == "Head of Communications"
    mkt = PP.role_for_trigger_keys({"ceo_change"}, desk="marketing")
    assert "Marketing" in mkt or "CMO" in mkt
    assert mkt != "Head of Communications"


def test_board_orders_band_first_not_by_legacy_opp():
    # A Nurture must never sit below a Monitor, even if the Monitor has a far
    # higher legacy opportunity value (Opus's sort anomaly).
    from datetime import datetime, timezone, timedelta
    from tool import lead_engine as LE
    def iso(n): return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()
    def ev(k, d, u, l): return {"trigger_key": k, "trigger_label": l, "url": u,
                                "source": u, "published": iso(d), "evidence": ""}
    rows = [
        {"company": "WatchCo", "account_tier": "watchlist", "_legacy_opp": 9.0,
         "events": [ev("profit_warning", 10, "thisismoney.co.uk", "Profit warning")]},
        {"company": "NurtureCo", "account_tier": "watchlist", "_legacy_opp": 1.0,
         "events": [ev("ceo_change", 5, "companieshouse.gov.uk", "CEO change")]},
    ]
    for r in rows:
        r["lead"] = LE.score_lead(r, "predictor", "comms")
    rows.sort(key=lambda r: ((r.get("lead") or {}).get("strength_rank") or 0.4,
                             r.get("_legacy_opp") or 0.0), reverse=True)
    assert rows[0]["company"] == "NurtureCo"          # band beats legacy opp
    assert rows[0]["lead"]["action"] != "monitor"
    assert rows[1]["lead"]["action"] == "monitor"


def test_why_now_explains_rather_than_repeats():
    comms = d._why_now("ceo_change", False, "Chief Communications Officer", "6-12 weeks")
    assert "comms" in comms.lower() and len(comms) > 40   # a real thesis, not "CEO change"
    assert comms != "CEO change"
    assert "—" not in comms and "–" not in comms          # house style
    mkt = d._why_now("funding", True, "CMO", "3-6 months")
    assert "marketing" in mkt.lower()
    assert d._why_now("totally_unknown_key", False, "X", "soon")  # fallback never blank
