"""The advisory board renderer — grouping, ranking, and the owner/why/chips
lines that make the lane consumable on any surface."""
from datetime import date

from tool import advisory_board as B

TODAY = date(2026, 6, 14)


def _row(company, verdict, conviction, owner="Lucy Cairncross"):
    return {"company": company, "trigger": "PayGapActionMandate",
            "verdict": verdict, "conviction": conviction,
            "why": f"{verdict.lower()} reason",
            "owner": {"owner": owner, "associate":
                      {"name": "Antoinette Willcocks", "firm": "RiverRoad"}},
            "qual": {"pain": 2, "sponsor": 1, "mandate": 2, "timing": 2,
                     "access": 0, "proof": 2, "total": 9}}


def test_empty_board_says_so():
    md = B.render_board([], today=TODAY)
    assert "No advisory leads today" in md


def test_board_groups_and_ranks():
    rows = [_row("Co A", "PURSUE", 80), _row("Co B", "PURSUE", 92),
            _row("Co C", "DEVELOP", 50), _row("Co D", "KILL", 10)]
    md = B.render_board(rows, today=TODAY, cap=5)
    assert "Call-ready (2)" in md and "Developing (1)" in md and "Killed (1)" in md
    # within Call-ready, the higher conviction ranks first
    assert md.index("Co B") < md.index("Co A")
    assert "PURSUE cap 5" in md


def test_board_shows_owner_and_gate_chips():
    md = B.render_board([_row("Co A", "PURSUE", 80)], today=TODAY)
    assert "owner: Lucy Cairncross" in md
    assert "Antoinette Willcocks (RiverRoad)" in md
    assert "gate: PAIN2 SPONSOR1 MANDATE2 TIMING2 ACCESS0 PROOF2 (9/12)" in md
