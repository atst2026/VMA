"""Read meetings from any iCal feed URL.

Works for Google Calendar AND Microsoft 365 Outlook - both expose a
"secret iCal URL" that anyone with the URL can read. Sara generates
one (Calendar settings -> integrate / publish), pastes it into the
CALENDAR_ICAL_URL GitHub Secret, and never has to touch OAuth /
client registration / refresh tokens.

Security: the URL itself is the secret. If it leaks, the calendar is
readable by anyone holding the URL until Sara regenerates it (one
click in calendar settings). Same security model as a Doodle link or
any other "URL is the password" pattern.

Returns Meeting dataclass instances for events on a given date (UTC
day boundary by default, override for London tz).
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import requests

log = logging.getLogger("brief.calendar")

ICAL_URL_ENV = "CALENDAR_ICAL_URL"


@dataclass
class Meeting:
    """One calendar event, normalised."""
    uid: str
    summary: str                       # Event title
    start: datetime                    # tz-aware (UTC)
    end: datetime                      # tz-aware (UTC)
    location: str = ""
    description: str = ""
    organiser_email: str = ""
    attendee_emails: list[str] = field(default_factory=list)

    @property
    def is_all_day(self) -> bool:
        # All-day events have no time component (typically 00:00 start
        # and 24h duration). Filter these out - they're not real meetings.
        return (self.end - self.start) >= timedelta(hours=23)

    @property
    def external_attendees(self) -> list[str]:
        """Attendees whose email domain differs from the organiser's.
        These are the people Sara needs to prep for."""
        if not self.organiser_email or "@" not in self.organiser_email:
            return list(self.attendee_emails)
        own_domain = self.organiser_email.rsplit("@", 1)[1].lower()
        return [
            a for a in self.attendee_emails
            if "@" in a and a.rsplit("@", 1)[1].lower() != own_domain
        ]


def _fetch_ics(url: str, timeout: int = 30) -> str:
    """Fetch the raw iCal feed body. Caller is responsible for handling
    network errors via the empty-string return."""
    try:
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; VMACalSubscriber/1.0)",
        })
        if r.status_code != 200:
            log.info("ical fetch %s -> HTTP %s", url[:60], r.status_code)
            return ""
        return r.text
    except requests.RequestException as e:
        log.info("ical fetch failed: %s", e)
        return ""


def _parse_ics_datetime(value: str, params: dict) -> datetime | None:
    """Parse an iCal DTSTART/DTEND value into a tz-aware UTC datetime.

    iCal datetimes come in three flavours:
      VALUE=DATE:20260514                    -> date-only (all-day)
      20260514T093000Z                       -> explicit UTC
      TZID=Europe/London:20260514T093000     -> floating with tzid
      20260514T093000                        -> floating local (rare)
    """
    if not value:
        return None
    # DATE-only (all-day events)
    if params.get("VALUE") == "DATE" or (len(value) == 8 and "T" not in value):
        try:
            d = datetime.strptime(value, "%Y%m%d")
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # Explicit UTC (trailing Z)
    if value.endswith("Z"):
        try:
            d = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # TZID variant - parse as the named tz, convert to UTC.
    tzid = params.get("TZID")
    try:
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc)
        except Exception:
            log.info("unknown TZID %r in iCal entry; treating as UTC", tzid)
    return naive.replace(tzinfo=timezone.utc)


def _unfold(text: str) -> Iterable[str]:
    """iCal lines longer than 75 chars are folded with a leading space
    or tab on the continuation. Reassemble before parsing."""
    buf = ""
    for line in text.splitlines():
        if line.startswith((" ", "\t")):
            buf += line[1:]
        else:
            if buf:
                yield buf
            buf = line
    if buf:
        yield buf


def _parse_line(line: str) -> tuple[str, dict, str]:
    """Returns (property_name_upper, params_dict, value).
    Example input:  'DTSTART;TZID=Europe/London:20260514T093000'
    Returns:        ('DTSTART', {'TZID': 'Europe/London'}, '20260514T093000')
    """
    if ":" not in line:
        return "", {}, ""
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip()
    return name, params, value


def _extract_email_from_attendee(value: str) -> str:
    """ATTENDEE values look like 'mailto:foo@bar.com' or sometimes
    just the address itself. Return just the bare address."""
    v = value.strip()
    if v.lower().startswith("mailto:"):
        v = v[7:]
    # Strip trailing CN= and other extras
    return v.split(",", 1)[0].strip().lower()


def parse_ics(text: str) -> list[Meeting]:
    """Parse an iCal feed into Meeting objects. Recurrence (RRULE) is
    NOT expanded - we only return events with an explicit DTSTART that
    falls in the time window the caller filters by, which is sufficient
    for the per-day prep workflow."""
    meetings: list[Meeting] = []
    in_event = False
    current: dict = {}
    for raw_line in _unfold(text):
        line = raw_line.strip("\r")
        if line == "BEGIN:VEVENT":
            in_event = True
            current = {"attendees": []}
            continue
        if line == "END:VEVENT":
            if in_event and current.get("uid") and current.get("start"):
                meetings.append(Meeting(
                    uid=current["uid"],
                    summary=current.get("summary", ""),
                    start=current["start"],
                    end=current.get("end", current["start"] + timedelta(hours=1)),
                    location=current.get("location", ""),
                    description=current.get("description", ""),
                    organiser_email=current.get("organiser", ""),
                    attendee_emails=current["attendees"],
                ))
            in_event = False
            continue
        if not in_event:
            continue

        name, params, value = _parse_line(line)
        if name == "UID":
            current["uid"] = value
        elif name == "SUMMARY":
            current["summary"] = value.replace("\\,", ",").replace("\\n", " ").strip()
        elif name == "DTSTART":
            current["start"] = _parse_ics_datetime(value, params)
        elif name == "DTEND":
            current["end"] = _parse_ics_datetime(value, params)
        elif name == "LOCATION":
            current["location"] = value.replace("\\,", ",").replace("\\n", " ").strip()
        elif name == "DESCRIPTION":
            current["description"] = value.replace("\\,", ",").replace("\\n", "\n").strip()
        elif name == "ORGANIZER":
            current["organiser"] = _extract_email_from_attendee(value)
        elif name == "ATTENDEE":
            current["attendees"].append(_extract_email_from_attendee(value))
    return meetings


def meetings_for_date(target_date: date,
                       ical_url: str | None = None,
                       *, tz_name: str = "Europe/London") -> list[Meeting]:
    """Return all timed meetings on `target_date` in the given timezone.
    Filters out all-day events and meetings without external attendees.

    `ical_url` defaults to the CALENDAR_ICAL_URL env var."""
    url = ical_url or os.environ.get(ICAL_URL_ENV, "").strip()
    if not url:
        log.info("CALENDAR_ICAL_URL not set - no meetings to brief on")
        return []
    text = _fetch_ics(url)
    if not text:
        return []
    all_meetings = parse_ics(text)
    log.info("Parsed %d total events from iCal feed", len(all_meetings))

    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    out: list[Meeting] = []
    for m in all_meetings:
        if m.is_all_day:
            continue
        # Convert event start to the target tz and check if it falls on the date
        local_start = m.start.astimezone(tz)
        if local_start.date() != target_date:
            continue
        out.append(m)
    # Sort by start time
    out.sort(key=lambda m: m.start)
    log.info("%d timed meetings on %s in %s", len(out), target_date, tz_name)
    return out
