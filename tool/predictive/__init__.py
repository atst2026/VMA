"""Pre-advert signal detection — surfaces UK companies likely to post a
senior comms role in the next 3–9 months, before the job is advertised.

Architecture (all files in this package):
    patterns.py   — regex + phrase library per trigger type
    detector.py   — scans fetched RSS/GDELT items, emits trigger events
    cluster.py    — job-ad cluster detection with 30-day state
    stacker.py    — groups events by company, counts stacks
    ranker.py     — score = trigger_weight x stack_mult x company_tier x freshness
    render.py     — "Pre-advert signals" email section
"""
