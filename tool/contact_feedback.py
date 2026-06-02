"""Positive + corrective feedback on resolved contacts — the data source
for the success metric (spec §4.5: "% of surfaced contacts marked correct
person").

Complements contact_flags (the negative "wrong person" *suppression*) with
the labels the metric needs:

  correct    — Sara confirms the surfaced person is the right contact
  responded  — the contact replied (a strong positive label)
  moved      — the person has left the seat
  wrong      — wrong person (also recorded here for the metric)

For the headline rate, ``correct`` / ``responded`` count as "correct
person" and ``wrong`` counts as "not"; ``moved`` is excluded from the rate
(they were the right seat-holder at surfacing — they've just since left)
but DOES drive suppression so the resolver stops naming them.

Same durable-state pattern as contact_flags: an atomic local write plus
github_state.push_async to the dashboard-state branch + boot hydrate, so
feedback survives Render redeploys. Per-desk namespaced via state_paths.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from tool.state_paths import state_dir

STATE_DIR = state_dir()
FEEDBACK_FILE = STATE_DIR / "contact_feedback.json"

VALID_SIGNALS = ("correct", "responded", "moved", "wrong")
# Labels that count toward the headline accuracy rate, and which way.
_POSITIVE = {"correct", "responded"}
_NEGATIVE = {"wrong"}
# §4.5: the metric isn't meaningful below this many labelled contacts.
MIN_VOLUME_FLOOR = 50

try:
    import fcntl
    _HAVE_FCNTL = True
except ImportError:
    _HAVE_FCNTL = False

_LOCK = threading.Lock()


@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = FEEDBACK_FILE.with_suffix(".lock")
    with _LOCK:
        fd = None
        if _HAVE_FCNTL:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def get_feedback() -> dict:
    """{f"{company}::{slot}": {"name","signal","at"}} — latest label per
    contact-slot."""
    if not FEEDBACK_FILE.exists():
        return {}
    try:
        d = json.loads(FEEDBACK_FILE.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def record(company: str, slot: str, name: str, signal: str) -> bool:
    """Store the latest label for (company, slot). Returns False on an
    unknown signal. moved/wrong also drive the suppression flag so the
    resolver stops surfacing that name."""
    signal = (signal or "").strip().lower()
    if not (company and slot and name) or signal not in VALID_SIGNALS:
        return False
    key = f"{company}::{slot}"
    with _locked():
        data = get_feedback()
        data[key] = {
            "name": name,
            "signal": signal,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        payload = json.dumps(data, indent=2)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp",
            dir=str(STATE_DIR), delete=False,
        )
        try:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, str(FEEDBACK_FILE))
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
    # moved / wrong -> also suppress, reusing the existing flag mechanism so
    # there's one suppression source of truth.
    if signal in ("moved", "wrong"):
        try:
            from tool import contact_flags
            contact_flags.flag(company, slot, name)
        except Exception:
            pass
    try:
        from tool import github_state
        github_state.push_async("tool/state/contact_feedback.json", payload,
                                f"state: contact feedback ({signal})")
    except Exception:
        pass
    return True


def accuracy_metric(window_days: int | None = None) -> dict:
    """The §4.5 headline metric over captured labels for this desk.

    Returns {labelled, correct, incorrect, moved, rate, meets_floor}.
    `rate` is None until at least one accuracy-bearing label exists;
    `meets_floor` reflects the 50-label minimum-volume rule.
    """
    data = get_feedback()
    cutoff = None
    if window_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    correct = incorrect = moved = 0
    for rec in data.values():
        if cutoff is not None:
            try:
                if datetime.fromisoformat(rec.get("at", "")) < cutoff:
                    continue
            except Exception:
                pass
        sig = rec.get("signal")
        if sig in _POSITIVE:
            correct += 1
        elif sig in _NEGATIVE:
            incorrect += 1
        elif sig == "moved":
            moved += 1

    labelled = correct + incorrect
    rate = (correct / labelled) if labelled else None
    return {
        "labelled": labelled,
        "correct": correct,
        "incorrect": incorrect,
        "moved": moved,
        "rate": rate,
        "meets_floor": labelled >= MIN_VOLUME_FLOOR,
    }
