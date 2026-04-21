#!/usr/bin/env python3
"""Generate a sample brief with synthetic data — lets Sara see the email
shape before the real sources run from her machine or GitHub Actions.

Run:
    python3 tool/sample_brief.py               # preview
    python3 tool/sample_brief.py test          # send to amirt12@hotmail.com
    python3 tool/sample_brief.py send          # send to stehrani@vmagroup.com

Needs RESEND_API_KEY for the send modes.
"""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

from tool import config
from tool.email_send import send
from tool.ranking import rank
from tool.render import render_html, render_plaintext

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


SAMPLE_SIGNALS = [
    {
        "id": "sample1", "source": "LSE RNS (Investegate)", "kind": "rns",
        "title": "NatWest Group plc — Directorate Change",
        "url": "https://www.investegate.co.uk/announcement/rns/natwest-group--nwg/directorate-change/9123456",
        "published": "2026-04-21T07:02:00+00:00", "company": "NatWest Group",
        "geo": "UK",
        "summary": "Board announces Chief Communications Officer departure after 4 years; succession process to begin.",
        "weight": 1.3,
    },
    {
        "id": "sample2", "source": "Adzuna (Indeed + aggregators)", "kind": "job",
        "title": "Head of Corporate Communications — AstraZeneca",
        "url": "https://www.adzuna.co.uk/jobs/details/4200001",
        "published": "2026-04-21T08:45:00+00:00", "company": "AstraZeneca",
        "geo": "UK",
        "summary": "Permanent. £110k–£135k. London/Cambridge hybrid. Reports to CCO.",
        "weight": 1.0,
    },
    {
        "id": "sample3", "source": "FCA News", "kind": "regulator",
        "title": "FCA fines a UK retail bank £85m over historic mis-selling",
        "url": "https://www.fca.org.uk/news/press-releases/2026-04-fine",
        "published": "2026-04-20T16:10:00+00:00", "company": "",
        "geo": "UK",
        "summary": "Enforcement action likely to trigger comms restructure and external PR counsel refresh.",
        "weight": 1.0,
    },
    {
        "id": "sample4", "source": "Greenhouse (monzo)", "kind": "job",
        "title": "Internal Communications Director",
        "url": "https://boards.greenhouse.io/monzo/jobs/123456",
        "published": "2026-04-21T09:00:00+00:00", "company": "monzo",
        "geo": "UK",
        "summary": "London — internal comms leadership for 3,000-person bank.",
        "weight": 1.0,
    },
    {
        "id": "sample5", "source": "GDELT", "kind": "leadership_change",
        "title": "Unilever appoints new Chief Communications Officer",
        "url": "https://www.reuters.com/business/unilever-new-cco-2026-04-21",
        "published": "2026-04-21T06:30:00+00:00", "company": "Unilever",
        "geo": "UK",
        "summary": "External hire from a US-listed peer. Incoming CCO historically rebuilds team inside 9 months.",
        "weight": 1.0,
    },
    {
        "id": "sample6", "source": "CorpComms Magazine", "kind": "trade_press",
        "title": "Centrica restructures Corporate Affairs function ahead of Q2",
        "url": "https://www.corpcommsmagazine.co.uk/centrica-2026-04",
        "published": "2026-04-20T14:00:00+00:00", "company": "Centrica", "geo": "UK",
        "summary": "Centrica's Corporate Affairs Director confirms split of Internal + External Comms into two leads; recruitment to follow.",
        "weight": 1.0,
    },
    {
        "id": "sample7", "source": "UK Find a Tender", "kind": "procurement",
        "title": "Department for Transport — Strategic Communications Partner Framework",
        "url": "https://www.find-tender.service.gov.uk/Notice/2026-DFT-COMMS",
        "published": "2026-04-21T00:00:00+00:00", "company": "Department for Transport",
        "geo": "UK",
        "summary": "£12m ceiling, 4-year framework. Prime/sub partner entry point.",
        "weight": 0.8,
    },
    {
        "id": "sample8", "source": "SEC EDGAR", "kind": "filing",
        "title": "8-K — Haleon plc — Departure of Chief Corporate Affairs Officer",
        "url": "https://www.sec.gov/Archives/edgar/data/haleon/000088420126",
        "published": "2026-04-20T21:15:00+00:00", "company": "Haleon", "geo": "UK",
        "summary": "Item 5.02 departure disclosure; interim CCAO appointed while external search runs.",
        "weight": 1.0,
    },
    {
        "id": "sample9", "source": "Campaign", "kind": "trade_press",
        "title": "Diageo's Global PR Director to step down by June",
        "url": "https://www.campaignlive.co.uk/diageo-pr-2026",
        "published": "2026-04-19T11:20:00+00:00", "company": "Diageo", "geo": "UK",
        "summary": "Move creates a global PR Director opening — contender-market clustered in London.",
        "weight": 1.0,
    },
    {
        "id": "sample10", "source": "LinkedIn Jobs (public)", "kind": "job",
        "title": "Communications Director, EMEA — Palo Alto Networks",
        "url": "https://www.linkedin.com/jobs/view/3987654321",
        "published": "2026-04-21T05:00:00+00:00", "company": "Palo Alto Networks",
        "geo": "UK", "summary": "London based, EMEA scope, enterprise tech.",
        "weight": 1.1,
    },
    {
        "id": "sample11", "source": "PRWeek UK", "kind": "trade_press",
        "title": "Rolls-Royce hires ex-BAE head of corporate affairs",
        "url": "https://www.prweek.com/rolls-royce-2026", "published": "2026-04-20T08:30:00+00:00",
        "company": "Rolls-Royce", "geo": "UK",
        "summary": "BAE Systems' Head of Corporate Affairs moves to Rolls-Royce; BAE role now open.",
        "weight": 1.0,
    },
    {
        "id": "sample12", "source": "Bright Data (LinkedIn public)", "kind": "linkedin_batch",
        "title": "LinkedIn sweep: senior comms opens in FS (UK)",
        "url": "https://www.linkedin.com/jobs/search/?keywords=head%20of%20communications%20financial%20services",
        "published": "2026-04-21T08:00:00+00:00", "company": "", "geo": "UK",
        "summary": "34 live senior-level FS comms posts surfaced in the last 24h — top candidates: HSBC, Lloyds, LGIM, Phoenix.",
        "weight": 0.9,
    },
]


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "preview").lower()
    ranked = rank(SAMPLE_SIGNALS)
    now = datetime.now()
    now_str = now.strftime("%A %d %B %Y · %H:%M")
    covered = "Mon 20 Apr → Tue 21 Apr (sample data — real run uses live sources)"
    html = render_html(
        ranked,
        {"LSE RNS": 1, "Adzuna": 1, "FCA": 1, "Greenhouse": 1, "GDELT": 1,
         "CorpComms": 1, "Find a Tender": 1, "SEC EDGAR": 1, "Campaign": 1,
         "LinkedIn Jobs": 1, "PRWeek UK": 1, "Bright Data": 1},
        now_str, covered,
    )
    text = render_plaintext(ranked, now_str, covered)

    (Path(__file__).resolve().parent / "state" / "sample_brief.html").write_text(html)
    (Path(__file__).resolve().parent / "state" / "sample_brief.txt").write_text(text)

    if mode in ("send", "test"):
        to = config.TEST_RECIPIENT if mode == "test" else config.RECIPIENT
        subject = f"[SAMPLE] Sara's Morning Brief — {now.strftime('%a %d %b')}"
        result = send(to, subject, html, text)
        if not result.get("ok"):
            print("Send failed:", result)
            return 2
        print(f"✓ Sample sent to {to} — status {result.get('status')}")
        return 0

    print(text)
    print(f"\n[HTML preview written to tool/state/sample_brief.html]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
