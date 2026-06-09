"""Tests for the compounding company dossiers (tool/dossier.py)."""
import json

import tool.dossier as DOS


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(DOS, "_dir", lambda: tmp_path)


def _entry(pid="tesco", company="Tesco", n_events=1, status="active"):
    return {"pid": pid, "company": company, "status": status,
            "events": [{"trigger_key": "ceo_change",
                        "trigger_label": "CEO change",
                        "evidence": f"Event {i}",
                        "url": f"https://investegate.co.uk/{pid}/{i}",
                        "source": "LSE RNS (Investegate)",
                        "published": f"2026-06-0{i + 1}T09:00:00+00:00",
                        "raw_signal_id": f"sig-{pid}-{i}"}
                       for i in range(n_events)]}


def _gate(presented=True, why=None):
    return {"presented": presented,
            "confidence": "High" if presented else None,
            "reasons": [] if presented else [why or "Watch-grade"],
            "recheck_days": None if presented else 7,
            "investigate": not presented, "evidence": {}, "kill": "",
            "move": ""}


def test_dossier_accumulates_without_duplicating(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert DOS.update_dossiers([_entry(n_events=1)],
                               {"tesco": _gate()}) == 1
    # Same event again next run + one new one: only the new one appends.
    DOS.update_dossiers([_entry(n_events=2)], {"tesco": _gate()})
    idx = json.loads((tmp_path / "_index.json").read_text())
    events = idx["companies"]["tesco"]["events"]
    assert len(events) == 2
    md = DOS.read("tesco")
    assert "Tesco — BD dossier" in md
    assert md.count("CEO change") == 2
    assert "investegate.co.uk" in md


def test_gate_history_records_changes_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    DOS.update_dossiers([_entry()], {"tesco": _gate(presented=False)})
    DOS.update_dossiers([_entry()], {"tesco": _gate(presented=False)})
    DOS.update_dossiers([_entry()], {"tesco": _gate(presented=True)})
    idx = json.loads((tmp_path / "_index.json").read_text())
    hist = idx["companies"]["tesco"]["gate_history"]
    assert [h["state"] for h in hist] == ["queued", "presented"]
    assert "Queued for /investigate" not in DOS.read("tesco")


def test_notes_survive_rerenders(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    DOS.update_dossiers([_entry()], {"tesco": _gate()})
    assert DOS.append_note("tesco", "Confirmed: external CEO, started 12 May.")
    DOS.update_dossiers([_entry()], {"tesco": _gate()})  # re-render
    md = DOS.read("tesco")
    assert "Investigation notes" in md
    assert "external CEO, started 12 May" in md


def test_verdicts_render_and_prune_bounds_files(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    DOS.update_dossiers([_entry()], {"tesco": _gate()},
                        verdicts=[{"rid": "tesco", "verdict": "call_today",
                                   "date": "2026-06-09T10:00:00+00:00"},
                                  {"rid": "other", "verdict": "reject",
                                   "date": "2026-06-09T10:00:00+00:00"}])
    md = DOS.read("tesco")
    assert "AD verdicts" in md and "call_today" in md and "reject" not in md
    # Prune: backdate last_seen far past the window, run again.
    idx = json.loads((tmp_path / "_index.json").read_text())
    idx["companies"]["tesco"]["last_seen"] = "2025-01-01"
    (tmp_path / "_index.json").write_text(json.dumps(idx))
    DOS.update_dossiers([], {})
    idx2 = json.loads((tmp_path / "_index.json").read_text())
    assert "tesco" not in idx2["companies"]
    assert not (tmp_path / "tesco.md").exists()


def test_never_raises_on_garbage(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert DOS.update_dossiers([{"pid": None}, {}, None and {}],
                               None) == 0
    assert DOS.read("nope") == ""
    assert not DOS.append_note("", "x") and not DOS.append_note("p", "  ")
