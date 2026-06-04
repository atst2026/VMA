"""Permanent BD-lead suppression (remove entirely)."""
from tool import bd_tombstone as T


def test_add_normalises_and_is_tombstoned(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TOMB_FILE", tmp_path / "bd_tombstone.json")
    monkeypatch.setattr(T, "STATE_DIR", tmp_path)
    assert not T.is_tombstoned("Oxford Quantum Circuits")
    assert T.add("Oxford Quantum Circuits!")
    data = T.get_all()
    # normalised match: punctuation/case/spacing-insensitive
    assert T.is_tombstoned("oxford quantum circuits", data)
    assert T.is_tombstoned("Oxford  Quantum   Circuits")
    assert not T.is_tombstoned("Tesco", data)


def test_blank_company_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TOMB_FILE", tmp_path / "bd_tombstone.json")
    monkeypatch.setattr(T, "STATE_DIR", tmp_path)
    assert not T.add("")
    assert not T.add(None)
    assert T.get_all() == {}


def test_persists_display_name_and_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TOMB_FILE", tmp_path / "bd_tombstone.json")
    monkeypatch.setattr(T, "STATE_DIR", tmp_path)
    T.add("Currys plc")
    rec = T.get_all()["currys plc"]
    assert rec["company"] == "Currys plc" and rec["ts"]
