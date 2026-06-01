# VMA Group Intelligence Platform

An AI-driven platform that turns public, freely-available signals into a ranked
list of business-development opportunities for an account director — surfacing
likely senior-communications hires *before* the role is advertised, and giving an
AD the materials to win the brief.

The platform is organised as three pages, reached from the left rail:
**Market Opportunities Radar** (the daily home), **Personal Assistant**
(on-demand reports), and **BD Calendar** (date-driven moves).

---

## Market Opportunities Radar

The daily home. Auto-populates each weekday; the AD opens it to a ranked
picture of the market, with triage (follow-up / dismiss) on every row.

**Live Jobs** — Each weekday the platform scans every public job source for
fresh senior-comms openings, removes duplicates, and ranks them by fee value ×
signal strength. Each lead opens with a suggested outreach angle, copy-to-
clipboard, and follow-up / dismiss triage. *Net effect: start the day with a
prioritised target list — as much publicly available data as exists, collated
into one place.*
- *Scrapes:* Adzuna (Indeed + a dozen aggregator boards), employers' own ATS
  feeds (Greenhouse, Lever, Ashby, Workable), the LinkedIn logged-off public
  jobs surface, Civil Service Jobs, jobs.ac.uk and Guardian Jobs. UK-primary,
  permanent roles from £40k.

**BD Leads** — A rolling 90-day forward view of business indicators that
reliably precede a comms hire, each with the company, the role we'd expect them
to hire, a strength tier (High / Med / Low), a timing window, the supporting
evidence, and a ready outreach draft (plus an advisory-service angle). To get in
front of the client before the vacancy is public, the scope it covers:
- Leadership change (CEO / CFO / Chair / CHRO-HR / IR), regulator action and
  early probes, profit warnings, crisis events, restructures, IPOs, contract
  losses, and IC-platform RFPs.
- Mergers & acquisitions, split into three distinct opportunities — because they
  don't behave the same way:
  - *Activist investor takes a stake* → a 3–6 month window (reputation defence,
    shareholder messaging).
  - *Private-equity buyout completes* → a 60–120 day window (new-ownership story,
    fast leadership churn — the quickest to act on).
  - *Conventional merger* → a 6–12 month integration window.
- Early "restlessness" indicators on senior comms leaders — signs a leader may
  move within a year, so we can build the relationship early:
  - *Personal-brand activity* (conference speaking, award shortlists and judging
    panels, trade-body committee seats) → a 6–12 month signal.
  - *Non-executive / charity-trustee appointments* → a 12–18 month signal, and
    the strongest of the four.
- Funding rounds — sizeable raises that open a ~6-month senior-comms hiring
  window — are folded in and ranked alongside the predictors.
- When a senior comms leader moves, the cascade logic flags both sides of the
  chain — the seat they vacated (a replacement search) and the team they'll
  reshape at their new employer — and surfaces those here.
- *Scrapes:* GDELT and Google News (company-news intelligence), the London Stock
  Exchange's RNS feed, Companies House officer changes, SEC 8-K filings for US
  parents, UK regulator feeds (FCA, Ofcom, Ofgem, Ofwat, CMA), comms trade press,
  and scale-up / funding news.

**Specialist Signals** — Three niche detectors that stay hidden until they catch
something, so the page only shows them when there's a play to make:
- *Water Special-Administration Watch* — financial-distress / special-
  administration signals at England & Wales water companies, weeks ahead of the
  comms event.
- *Contract-End / Re-Tender Window* — watchlist suppliers nearing contract
  expiry or recompete; the change-and-transition comms window months before any
  contract-loss announcement is public.
- *Mandates Worth Stealing* — comms job ads left open past their per-source stale
  threshold, where the client may now be open to a second agency or an off-piste
  candidate.

---

## Personal Assistant

A simple prompt builds key reports in real-time, off the latest data. Four
on-demand tools:

**Pitch Pack** — Generates a client-ready BD document to upgrade a vacancy into
an exclusive, retained search: company profile, their stated strategic
priorities, a peer market map, the cost of leaving the seat empty, a salary
benchmark and our retained methodology. Built to mirror the client's own
language so the pitch speaks in their words.

**Reverse Match** — Give it a candidate; it searches the market fresh and returns
a ranked list of accounts — live and forecast openings (hot / warm / cold) — that
best fit them, so we can place the people we already know.

**Pre-meeting Brief** — A one-page prep sheet before any client call: key
contacts, recent news, active signals, their strategic priorities, and three
ready conversation openers.

**Manual Sweep** — A catch-up scan (1–60 day window, 14 by default) that re-runs
the full scour for any leads or pre-market signals missed after leave or a busy
patch.

> *Retired from the live UI:* the old standalone **Candidate Watch** roster and
> **Recent Reports** log have been removed — candidate restlessness is now tracked
> inside BD Leads, and reports open directly in a new tab when generated.

---

## BD Calendar

The business-development moves that run on key dates — a month-by-month view of
dated windows driven by statute and the industry calendar. Three cards:

**Placement Windows** — Statutory hiring windows that open on a known calendar
(e.g. the FCA Consumer Duty reporting ramp, sustainability-reporting deadlines,
AGM season), each tied to named target accounts and flagged *Regulatory deadline*
or *Policy timeline*. Goes quiet outside dated windows, by design.

**Events & Networking** — Awards, summits and networking dates worth showing up
to, across the UK and European comms calendar (PRWeek, CIPR, PRCA, IoIC, EACD,
European Excellence), with a "window open" flag when the outreach moment is live.

**Framework Eligibility** — Public-sector frameworks where VMA can bid, with the
eligibility window and a portal verification link. Eligibility and BD groundwork,
not a live lead list.

---

## Where the data comes from — and our compliance position

Everything runs on public or licensed sources: company-news intelligence (GDELT
and Google News); the London Stock Exchange's RNS feed and UK regulator feeds
(FCA, Ofcom, Ofgem, Ofwat, CMA); Companies House officer changes; SEC filings for
US parent companies; public job boards (Adzuna / Indeed, employers' own ATS
feeds, Civil Service Jobs, jobs.ac.uk, Guardian Jobs); comms trade press; scale-up
and funding news; and companies' own annual reports.

We do not automate anyone's LinkedIn account. Any logged-off job-posting data is
the licensed, publicly-visible surface, kept as a separate dataset; and Bright
Data's licensed Web Unlocker is used only to resolve a public profile URL. The
statutory calendar and framework registry are hand-curated from published
government sources.

In short: nothing here exposes us on data-protection or platform-terms grounds.
