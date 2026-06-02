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
    (the per-request override in a web request, else the VMA_PROFILE env var).
    """
    key = (profile_key or active_profile().key).strip().lower()
    root = _STATE_ROOT if key == DEFAULT_PROFILE_KEY else _STATE_ROOT / key
    root.mkdir(parents=True, exist_ok=True)
    return root


class _LazyStateDir:
    """A stand-in for ``state_root()`` that re-resolves on *every* use.

    A module-level ``STATE_DIR = state_dir()`` therefore follows the active
    profile at the moment it's used — which is what lets the single dashboard
    process serve both desks (the profile is request-scoped). In a
    single-profile process (the brief, or a per-profile deploy) it simply
    resolves to that one profile's dir every time. All the existing
    ``STATE_DIR / "x.json"`` / ``STATE_DIR.mkdir(...)`` call sites keep working
    unchanged.
    """
    __slots__ = ()

    def __truediv__(self, other):
        # Return a LAZY path, not a concrete one, so a module-level
        # `FOO_FILE = STATE_DIR / "x.json"` re-resolves the active profile on
        # every use instead of freezing to the import-time desk. Without
        # this, the single dashboard process (default comms) served the
        # marketing desk comms data for every module that defines its file
        # path at import (predictor_pipeline, lead_status, cascade, …).
        return _LazyStatePath(str(other))

    def __fspath__(self):
        return str(state_root())

    def __str__(self):
        return str(state_root())

    def __repr__(self):
        return f"<state_dir {state_root()}>"

    def __eq__(self, other):
        return state_root() == other

    def __getattr__(self, name):
        # Delegate everything else (.mkdir, .exists, .glob, .parent, …) to the
        # freshly-resolved Path for the active profile.
        return getattr(state_root(), name)


class _LazyStatePath:
    """A lazy file path *under* the active profile's state dir. It re-resolves
    ``state_root() / <rel>`` on EVERY operation, so a module-level constant
    like ``FOO_FILE = STATE_DIR / "x.json"`` follows the per-request profile
    in the shared dashboard process rather than freezing to the desk that was
    active at import time. Single-profile processes (the briefs) resolve to
    that one profile every time, unchanged."""
    __slots__ = ("_rel",)

    def __init__(self, rel: str):
        self._rel = rel

    def _resolved(self) -> Path:
        return state_root() / self._rel

    def __truediv__(self, other):
        return _LazyStatePath(str(Path(self._rel) / other))

    def __fspath__(self):
        return str(self._resolved())

    def __str__(self):
        return str(self._resolved())

    def __repr__(self):
        return f"<state_path {self._resolved()}>"

    def __eq__(self, other):
        return self._resolved() == other

    def __getattr__(self, name):
        # Delegate .exists / .read_text / .write_text / .open / .with_suffix /
        # .parent / … to the freshly-resolved Path for the active profile.
        return getattr(self._resolved(), name)


def state_dir() -> _LazyStateDir:
    """A lazy STATE_DIR for module-level use (see _LazyStateDir)."""
    return _LazyStateDir()
