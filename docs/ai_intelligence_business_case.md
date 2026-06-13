# VMA AI — Business Case for Funding the Intelligence Engine

**One line:** We have already built (and briefly run) an AI research analyst that works every priority lead the night before — reading the company, naming the decision-maker, finding the angle and writing the meeting prep. It is fully built and tested. It is switched off for one reason only: it needs an Anthropic API budget. This paper sets out what it does and why it pays for itself many times over.

---

## 1. The problem it solves

Our free engine is genuinely good at one thing: **finding that an opportunity exists**. Every night it scours Companies House, the stock exchange (RNS), the regulators, the news and the job boards, and surfaces companies showing the signals that precede a senior communications or marketing hire — a new CEO, a funding round, a restructure, a reposted role that won't fill.

What it **cannot** do for free is tell you what is actually *true* about that specific company, *who* to call, or *how* to win the meeting. That is reasoning and live research, not data-fetching — and it is exactly the work that separates a researched approach from a cold call.

The visible symptom today: every lead card reads like a **template**. It correctly says "this looks like a growth situation, here is the generic playbook and the fee at stake," but it says roughly the same thing for every company of that type. An Account Director looking at it can tell it is pattern-matching, not research — and that is precisely the half of the job the AI engine was built to do.

## 2. What the build actually does

It is, in effect, **a junior Account Director who never sleeps** — doing the two hours of desk research a good AD would do before a first meeting, for every priority lead, every night. Concretely, it produces four things:

**a. The account thesis** — *what is really going on here.* The AI reads the company's own leadership pages, careers page, latest annual report and recent press, plus everything our engine has already accumulated, and writes an evidence-cited read: the genuine needs we can serve, **each tied to dated, sourced proof about that specific company**, mapped across our *full* service catalogue — not just placement, but organisation design, benchmarking, coaching and ED&I. Anything it cannot ground in evidence, it is instructed to leave out. *Be specific or be silent.*

**b. The meeting hook and 5-minute prep** — *how to win the conversation.* The single opener that proves we did the work; three diagnostic questions that build rapport while exposing the gap; the two objections we are most likely to hit and the honest counter to each; and what a scoped engagement looks like if they say yes. The AD walks into the call genuinely prepared.

**c. The named decision-maker, with a verified email** — *who to actually contact.* For live vacancies, the AI works out who owns *this* hire at *this* employer today — the right legal entity, the right seat, the right current person (actively checking they haven't moved on) — and finds their published email, so outreach lands on the right desk instead of a generic inbox.

**d. The investigation verdict** — *is this even real?* It independently verifies the trigger and actively tries to **kill** the lead — incumbent already in post, role already filled, hiring freeze, wrong company. A killed lead never wastes an AD's call. A confirmed lead arrives with the economic buyer and the route in already mapped.

## 3. Before and after — a real example

A lead currently on the live board: **Kingfisher** (the B&Q / Screwfix group), surfaced on a share-allotment filing.

**Today (free engine):** "Capital raise → growth → likely a team build. Lead with a retained build-out. Fee at stake £30k–£93k." Accurate in shape, generic in substance — the same words any growth-signal lead receives. No named contact. No knowledge of Kingfisher's actual situation.

**With the intelligence engine:** the AI reads Kingfisher's actual annual report and newsroom, establishes what the share issue was really for, maps who currently leads Corporate Affairs and how big the team is, identifies the specific gap and need with dated evidence, names the person to approach, and writes the exact opener and the three questions to ask. The card stops being a template and becomes a brief.

That difference — generic vs. specific, cold vs. researched — is the entire conversion argument in the BD literature, and it is the gap this build closes.

## 4. Why it cannot be done for free

Everything that can be free, already is, and stays free: all the data sources, the signal detection, the deterministic playbook, the company-website contact harvesting, the gender pay-gap and benchmarking intelligence, the dashboard. **The only paid dependency in the entire system is the AI reasoning itself** — because reading an annual report and judging what it means for *us* is intelligence, not a database lookup. There is no free substitute for it.

## 5. The commercial case

- **It directly feeds the desk's currency: client BD meetings.** The desk runs on booked meetings (the KPI is multiple a week). This engine manufactures the researched, first-mover, evidence-led approach that converts — at scale, every night, across the whole board.
- **It unlocks a revenue line we are currently blind to.** Half our specialism is advisory — organisation design, benchmarking, coaching, ED&I. The free engine surfaces placement signals only. The AI is what spots the advisory engagement: higher-value, stickier, no candidate-delivery risk, and the lower-barrier sale that *opens the door to the retained search* on the same account.
- **It buys back AD time.** It replaces 1–2 hours of manual desk research per account. That time goes back into selling and meetings.
- **It makes the board trustworthy.** Research either grounds a lead or kills it — so ADs stop wasting calls on dressed-up thin signals and start trusting the system enough to act on it.

## 6. Cost, and the controls that bound it

This was engineered from the start to be **cheap and controllable**, not open-ended:

- **Budgeted per night** — a fixed, small number of theses and contact look-ups per run, worked in priority order (the leads most likely to convert first).
- **Cached, not repeated** — a thesis is reused for three weeks and only re-run when the company's evidence actually changes. We never pay twice for the same intelligence.
- **Two independent spend lanes** — we can run *contacts only* (just the named people), *full intelligence*, or off — a single setting.
- **A model dial** — it can run on the top-tier model for maximum quality or drop to a cheaper tier that cuts the bill substantially with a modest quality trade-off.

**Indicative cost:** at the current budgets, on the order of a few hundred to roughly £1,000–£1,200 per month at full tilt, tunable down to a few hundred a month via the cheaper-model dial. (Exact figures should be confirmed against live Anthropic Console pricing; a detailed per-lead costing has been run separately.)

## 7. The return

A single permanent senior placement earns a fee of roughly **£15,000–£28,000** (≈18.5–22% of an £80k–£130k salary). The **entire annual cost of this engine is less than one such fee.** If, across a whole year, it converts even **one** additional meeting into one additional placement — or surfaces one advisory project we would otherwise never have seen — it has paid for itself, and everything beyond that is margin. It is working every lead, every night, for the price of a fraction of one deal.

## 8. The ask

Fund an Anthropic API budget (a capped monthly spend, set wherever finance is comfortable) and switch the two lanes back on. **There is nothing to build** — the engine is written, tested and was already running; it was paused only to avoid spend before this decision was made. The moment the budget is live, every priority lead on the board starts arriving as a researched brief with a named contact and a meeting plan, instead of a template.

---

### Appendix — proof this is real, not a pitch

The capability is implemented and version-controlled in our own codebase:

- **Account thesis engine** — runs live web research per lead, returns a structured, evidence-cited thesis whose recommended services are locked to our real service catalogue (the model cannot invent products), with hard validation that drops anything ungrounded. Outputs the headline, function snapshot, evidenced needs, meeting hook, talking points and full meeting prep.
- **Investigation pass** — independent trigger verification with an instruction to play sceptic and kill weak leads; returns a typed verdict plus economic buyer, champion path, incumbent and agency propensity.
- **Named-contact research** — per-vacancy decision-maker identification with entity/seat/person discipline, dated-evidence acceptance, and published-email capture, written into the same contact store every other source uses.
- **Spend governance** — a central gate with the two lanes (`contacts` / full) and a per-model cost dial, so spend is bounded and steerable from day one.

All of it degrades to a clean no-op without an API key — which is exactly why it is currently dark, and exactly what funding turns on.
