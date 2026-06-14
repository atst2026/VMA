"""PayGapActionMandate — the first first-class advisory detector.

Reuses the GOV.UK gender-pay-gap dataset the platform already ingests
(`tool.gender_pay_gap`) — zero new fetches, zero credits. The discipline
that keeps it out of the generic-noise trap (ADVISORY_ENGINE.md §11 #1):

  * A standing pay-gap figure is NOT a lead. `gender_pay_gap.edi_angle`
    already refuses to manufacture one, and we honour that — only records
    with a REAL, evidenced gap problem (wide / very wide / filed late, or
    widening year-on-year) are eligible.
  * The COMPELLING EVENT is the statutory window being open — the GPG
    reporting deadline / equality-action-plan cycle. Outside that window
    this detector emits nothing: a gap with no clock is enrichment, not a
    lead. The window also supplies the dated "why now" and routes the
    lead to ED&I (Antoinette / Kate) via the existing service-fit lens.

Economic buyer is the CEO / CHRO (a board-level ED&I risk), not just the
function head — so the gate's SPONSOR/ACCESS dimensions look upward.
"""
from __future__ import annotations

import logging
from datetime import date

from tool.advisory_signals.base import AdvisorySignal

log = logging.getLogger("brief.advisory.paygap")

# The calendar-pulse keys whose open window makes a pay-gap a dated lead.
STATUTORY_PULSE_KEYS = ("equality_pay_reporting_2026", "gender_pay_gap_2026")

# Year-on-year widening (median %, percentage points) that is itself an
# event even mid-window: a gap that got materially worse.
_WIDENING_PP = 2.0


def _active_statutory_keys(today: date) -> set[str]:
    """Which statutory pay-gap pulse windows are open today (the dated
    compelling event). Best-effort; empty set on any failure → no leads."""
    try:
        from tool import calendar_pulses as cp
        open_keys = {p.get("key") for p in cp.active_pulses(today=today)}
        return open_keys & set(STATUTORY_PULSE_KEYS)
    except Exception as e:
        log.info("paygap window check skipped (%s)", e)
        return set()


def _window_for(today: date, active_keys: set[str]):
    """The (start, end) ISO window of the soonest-closing active statutory
    pulse, for the gate's TIMING dimension and the pack's why-now clock."""
    try:
        from tool import calendar_pulses as cp
        rows = [p for p in cp.active_pulses(today=today)
                if p.get("key") in active_keys]
        rows.sort(key=lambda r: r.get("days_left", 9999))
        if rows:
            w = rows[0].get("window", "")
            if " → " in w:
                a, b = w.split(" → ", 1)
                return (a.strip(), b.strip()), rows[0]
    except Exception:
        pass
    return None, None


def pay_gap_action_signals(today: date | None = None, *,
                           records: list[dict] | None = None,
                           active_keys: set[str] | None = None,
                           marketing: bool = False) -> list[AdvisorySignal]:
    """Originate PayGapActionMandate advisory signals for today.

    Returns [] when no statutory window is open (no compelling event) or
    when the GPG index isn't populated (host not yet on the egress
    allowlist) — a clean no-op, never raises.

    `records` / `active_keys` are injectable for testing; in production
    they default to the live GPG index and today's open pulse windows.
    """
    from tool import gender_pay_gap as gpg
    from tool.advisory import service_fit_for

    today = today or date.today()
    keys = active_keys if active_keys is not None else _active_statutory_keys(today)
    if not keys:
        return []  # no statutory clock → a gap is enrichment, not a lead

    window, pulse = _window_for(today, keys)
    pulse_key = (pulse or {}).get("key") or sorted(keys)[0]
    legal = (pulse or {}).get("legal_date", "")
    days_left = (pulse or {}).get("days_left")

    recs = records if records is not None else gpg.all_records()
    mix = [s["key"] for s in service_fit_for([pulse_key])["services"]]

    out: list[AdvisorySignal] = []
    for rec in recs or []:
        angle = gpg.edi_angle(rec, marketing=marketing)
        widened = _widening_pp(rec)
        if not angle and widened is None:
            continue  # no real gap problem — never manufacture a lead

        company = (rec.get("employer") or "").strip()
        if not company:
            continue
        med = rec.get("median")
        late = bool(rec.get("late"))
        yr = rec.get("year")

        pain_bits = []
        if med is not None:
            pain_bits.append(f"median gender pay gap {med:.1f}%")
        if widened is not None:
            pain_bits.append(f"widened {widened:.1f}pp year-on-year")
        if late:
            pain_bits.append("filed after the statutory deadline")
        pain = ("A board-level ED&I exposure: "
                + ", ".join(pain_bits)
                + " — and statutory equality action plans now require named,"
                  " evidenced actions, a capability most functions lack.")

        why_now = ("Statutory pay-gap reporting / equality-action-plan "
                   "window is open"
                   + (f" — {days_left} days to act" if isinstance(days_left, int)
                      else "")
                   + (f" ({legal})" if legal else "") + ".")

        # GOV.UK gender-pay-gap is a primary (registry-grade) source: it
        # attests the PAIN on its own. SPONSOR/ACCESS still need the
        # contact layer before this earns PURSUE (see advisory_gate).
        ev = [{"source": "GOV.UK Gender Pay Gap Service",
               "url": rec.get("url")
               or "https://gender-pay-gap.service.gov.uk/"}]

        # Confidence: a very wide / widening / late gap is a stronger,
        # less deniable signal than a merely-wide one.
        conf = 0.6
        if (med is not None and med >= gpg._VERY_WIDE) or late \
                or widened is not None:
            conf = 0.8

        out.append(AdvisorySignal(
            trigger="PayGapActionMandate",
            company=company,
            service_mix=mix,
            pain=pain,
            buyer_hint="CHRO / People Director (CEO sponsor) — a board-level"
                       " ED&I risk, not only the function head",
            why_now=why_now,
            evidence=ev,
            window=window,
            confidence=conf,
            company_number=(rec.get("number") or "").strip(),
            extra={"pulse_key": pulse_key, "median": med, "late": late,
                   "size_band": rec.get("size") or "", "year": yr,
                   "widened_pp": widened},
        ))
    return out


def _widening_pp(rec: dict):
    """Year-on-year widening in median gap (percentage points), or None.

    The live GPG index holds one year, so production records won't carry a
    prior figure yet (a two-year diff is a Phase-2 enhancement). The hook
    is here and honoured if a record supplies `median_prev`, so a record
    that DID widen is treated as an event even were its current gap modest.
    """
    try:
        cur = rec.get("median")
        prev = rec.get("median_prev")
        if cur is None or prev is None:
            return None
        delta = float(cur) - float(prev)
        return round(delta, 1) if delta >= _WIDENING_PP else None
    except Exception:
        return None
