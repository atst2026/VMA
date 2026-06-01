"""Per-profile state directory resolution.

Each specialism keeps its runtime state (leads, triage, predictor pipeline,
candidate watch …) in its own directory, so two profiles running off the
same codebase never read or overwrite each other's data.

The default/legacy profile (comms) lives at the historical root,
``tool/state/`` — so the live comms tool is completely unaffected: same
files, same paths, same dashboard-state branch. Every other profile gets a
namespaced sub-directory, ``tool/state/<key>/``.

Which profile a process serves is set by VMA_PROFILE (see tool/profiles/).
"""
from __future__ import annotations

from pathlib import Path

from tool.profiles import DEFAULT_PROFILE_KEY, active_profile

_STATE_ROOT = Path(__file__).resolve().parent / "state"


def state_root(profile_key: str | None = None) -> Path:
    """Directory holding a profile's state.

    comms / default → the legacy root ``tool/state/`` (unchanged);
    any other profile → ``tool/state/<key>/``. Created on demand.

    Pass an explicit ``profile_key``, or omit it to use the active profile
    selected by the VMA_PROFILE env var.
    """
    key = (profile_key or active_profile().key).strip().lower()
    root = _STATE_ROOT if key == DEFAULT_PROFILE_KEY else _STATE_ROOT / key
    root.mkdir(parents=True, exist_ok=True)
    return root
