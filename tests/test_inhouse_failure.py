"""Tests for the v2 demand-first additions: the posting ledger
(in-house-failure + hiring-restart detectors), the wayback short-tenure
(mishire) classification, and trigger registration.
"""
from datetime import date, timedelta

from tool.predictive import inhouse_failure as IHF
from tool.predictive import patterns as P
from tool.sources.wayback import split_short_tenure


D0 = date(2026, 1, 1)


def _job(company="Acme Ltd", title="Head of Communications",
         url="https://jobs.example/123"):
    return {"kind": "job", "company": company, "title": title, "url": url}


def _seen_daily(state, days, **kw):
    """Simulate the daily poll seeing the same posting every day."""
    for n in range(days + 1):
        IHF.ingest_jobs([_job(**kw)], today=D0 + timedelta(days=n),
                        state=state)
    return D0 + timedelta(days=days)


# ====================================================================
# 1. Registration: the three v2 keys exist with sane fields.
# ====================================================================
def test_v2_triggers_registered():
    for key in ("inhouse_search_failing", "hiring_restart",
                "mishire_reversal"):
        t = P.BY_KEY.get(key)
        assert t is not None, f"{key} missing from BY_KEY"
        assert t.patterns == [], f"{key} is detector-emitted, not regex"
        assert 0 < t.weight <= 1.0
        lo, hi = t.lead_time_weeks
        assert 0 <= lo < hi


# ====================================================================
# 2. Aged roles: fire at AGED_DAYS, once, only while still live.
# ====================================================================
def test_aged_role_fires_once_at_threshold():
    st = {}
    today = _seen_daily(st, IHF.AGED_DAYS)
    events = IHF.detect_inhouse_failure(today=today, state=st)
    assert len(events) == 1
    ev = events[0]
    assert ev.trigger_key == "inhouse_search_failing"
    assert ev.company == "Acme Ltd"
    assert f"{IHF.AGED_DAYS} days" in ev.evidence
    assert ev.url == "https://jobs.example/123"
    # Same episode never fires twice.
    assert IHF.detect_inhouse_failure(today=today, state=st) == []


def test_aged_role_does_not_fire_early():
    st = {}
    today = _seen_daily(st, IHF.AGED_DAYS - 1)
    assert IHF.detect_inhouse_failure(today=today, state=st) == []


def test_aged_role_must_still_be_live():
    st = {}
    _seen_daily(st, IHF.AGED_DAYS)
    # Last seen well past the RECENT_DAYS window: the ad is gone — the
    # seat may have been filled. No event.
    later = D0 + timedelta(days=IHF.AGED_DAYS + IHF.RECENT_DAYS + 5)
    assert IHF.detect_inhouse_failure(today=later, state=st) == []


# ====================================================================
# 3. Reposts: withdrawn-then-readvertised inside the band fires once.
# ====================================================================
def test_repost_signature_fires():
    st = {}
    IHF.ingest_jobs([_job()], today=D0, state=st)
    gap = IHF.REPOST_GAP_MIN_DAYS + 7
    today = D0 + timedelta(days=gap)
    IHF.ingest_jobs([_job()], today=today, state=st)
    events = IHF.detect_inhouse_failure(today=today, state=st)
    assert len(events) == 1
    assert events[0].trigger_key == "inhouse_search_failing"
    assert "reposted" in events[0].evidence
    assert f"{gap} days" in events[0].evidence
    # Fires once per repost episode.
    assert IHF.detect_inhouse_failure(today=today, state=st) == []


def test_short_gap_is_scrape_jitter_not_repost():
    st = {}
    IHF.ingest_jobs([_job()], today=D0, state=st)
    today = D0 + timedelta(days=IHF.REPOST_GAP_MIN_DAYS - 7)
    IHF.ingest_jobs([_job()], today=today, state=st)
    assert IHF.detect_inhouse_failure(today=today, state=st) == []


def test_very_long_gap_starts_new_episode():
    st = {}
    IHF.ingest_jobs([_job()], today=D0, state=st)
    today = D0 + timedelta(days=IHF.REPOST_GAP_MAX_DAYS + 10)
    IHF.ingest_jobs([_job()], today=today, state=st)
    # Not a repost — a genuinely new opening. Age restarts from today.
    assert IHF.detect_inhouse_failure(today=today, state=st) == []
    co = st["companies"][IHF._norm_company("Acme Ltd")]
    role = co["roles"][IHF._norm_title("Head of Communications")]
    assert role["first_seen"] == today.isoformat()


# ====================================================================
# 4. Hiring restart: first posting after a long company silence.
# ====================================================================
def test_hiring_restart_fires_once_after_long_silence():
    st = {}
    IHF.ingest_jobs([_job(title="Comms Manager")], today=D0, state=st)
    today = D0 + timedelta(days=IHF.RESTART_GAP_DAYS + 20)
    IHF.ingest_jobs([_job(title="Head of Communications")], today=today,
                    state=st)
    events = IHF.detect_hiring_restart(today=today, state=st)
    assert len(events) == 1
    assert events[0].trigger_key == "hiring_restart"
    assert events[0].company == "Acme Ltd"
    assert IHF.detect_hiring_restart(today=today, state=st) == []


def test_no_restart_for_short_silence_or_new_company():
    st = {}
    IHF.ingest_jobs([_job()], today=D0, state=st)
    mid = D0 + timedelta(days=IHF.RESTART_GAP_DAYS - 30)
    IHF.ingest_jobs([_job()], today=mid, state=st)
    assert IHF.detect_hiring_restart(today=mid, state=st) == []
    # A company seen for the first time has no silence to break.
    IHF.ingest_jobs([_job(company="Brand New Co")], today=mid, state=st)
    assert IHF.detect_hiring_restart(today=mid, state=st) == []


# ====================================================================
# 5. Entity resolution: suffix + qualifier variants track as one.
# ====================================================================
def test_company_and_title_normalisation_merge_variants():
    st = {}
    IHF.ingest_jobs([_job(company="Acme Ltd",
                          title="Head of Communications")],
                    today=D0, state=st)
    IHF.ingest_jobs([_job(company="ACME Limited",
                          title="Head of Communications (London)")],
                    today=D0 + timedelta(days=1), state=st)
    assert len(st["companies"]) == 1
    co = next(iter(st["companies"].values()))
    assert len(co["roles"]) == 1


# ====================================================================
# 6. Ledger hygiene: stale roles and dead companies are pruned.
# ====================================================================
def test_pruning_bounds_the_ledger():
    st = {}
    IHF.ingest_jobs([_job(company="Old Co")], today=D0, state=st)
    later = D0 + timedelta(days=IHF.ROLE_PRUNE_DAYS + 5)
    IHF.ingest_jobs([_job(company="Fresh Co")], today=later, state=st)
    old = st["companies"].get(IHF._norm_company("Old Co"))
    assert old is not None and old["roles"] == {}
    gone = D0 + timedelta(days=IHF.COMPANY_PRUNE_DAYS + 5)
    IHF.ingest_jobs([_job(company="Fresh Co")], today=gone, state=st)
    assert IHF._norm_company("Old Co") not in st["companies"]


# ====================================================================
# 7. Wayback short-tenure (mishire) classification.
# ====================================================================
def test_split_short_tenure_classifies_recent_joiner():
    departed = {"Jane Smith", "Tom Jones"}
    long_ago = {"Jane Smith", "Maria Garcia"}
    ordinary, short = split_short_tenure(departed, long_ago)
    assert ordinary == {"Jane Smith"}      # present 18 months ago
    assert short == {"Tom Jones"}          # joined inside the window


def test_split_short_tenure_never_fabricates_without_old_snapshot():
    departed = {"Jane Smith"}
    assert split_short_tenure(departed, None) == ({"Jane Smith"}, set())
    assert split_short_tenure(departed, set()) == ({"Jane Smith"}, set())
