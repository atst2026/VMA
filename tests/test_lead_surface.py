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
