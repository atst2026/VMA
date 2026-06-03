"""Sector-level strategic context — the graceful, never-blank fallback for
the Pitch Pack's "Why this matters now" section.

The bespoke ideal for Section 2 is a quote lifted from the target's own
annual report. That extraction genuinely fails for some marquee names
(heavily-designed FTSE PDFs that pypdf can't parse cleanly), and not every
account is on the hand-curated priorities list. The OLD behaviour then
dead-ended at "check trade press manually" — a blank, defeatist section on
exactly the prestige targets where the pack most needs to look sharp.

This module supplies the final rung: a profile-aware, sector/cohort-level
read of *what is driving senior comms / marketing demand in this part of the
market right now*. It is explicitly sector-level (not company-specific), so
the pack labels it as such and prompts Sara to add a bespoke line — but it is
relevant, current, and never embarrassing. Pure data; no I/O.

Keyed by BOTH the Pitch-Pack affinity-cohort keys (peers.PITCH_AFFINITY_GROUPS)
and the broad ranker sector keys (peers.SECTOR_PEERS), so a lookup can prefer
the tighter cohort and fall back to the sector.
"""
from __future__ import annotations


# What is pulling senior COMMS leaders into the market, by cohort/sector.
_COMMS: dict[str, list[str]] = {
    # --- affinity cohorts (preferred — tighter) ----------------------------
    "global_consumer_brands": [
        "Brand-trust and ESG scrutiny (greenwashing rules, health/HFSS, packaging) keep corporate-affairs firmly on the board agenda.",
        "Portfolio premiumisation and cost/restructuring programmes are reshaping comms teams around investor and change narratives.",
        "Activist and short-seller attention on consumer names is lifting demand for IR-literate corporate communications.",
    ],
    "premium_luxury": [
        "Brand reputation and founder/creative-director transitions make corporate narrative a board-level risk in luxury.",
        "China demand swings and macro sensitivity put a premium on investor and crisis communications capability.",
        "Sustainability, provenance and ethical-sourcing scrutiny are expanding the corporate-affairs remit.",
    ],
    "grocery_retail": [
        "Cost-of-living pricing politics and supplier disputes keep retailers in near-permanent reputation management.",
        "Store-estate restructuring and automation drive heavy internal-change communications demand.",
        "ESG, packaging and food-standards regulation sustain a steady corporate-affairs hiring base.",
    ],
    "hospitality_leisure": [
        "Margin pressure, restructuring and refinancing are driving internal-change and stakeholder comms.",
        "Gambling and alcohol regulation keep public-affairs and reputation risk high on the agenda.",
        "Brand and crisis readiness matters acutely where reputation is the franchise.",
    ],
    "uk_banks": [
        "Consumer Duty, fraud and operational-resilience scrutiny keep regulatory and crisis comms in constant demand.",
        "Cost programmes, branch reshaping and AI adoption drive large internal-change communications agendas.",
        "Investor and results-cycle communications remain a core senior-comms requirement.",
    ],
    "insurers": [
        "Claims-handling fairness and Consumer Duty scrutiny keep reputation and regulatory comms live.",
        "Bulk-annuity and consolidation activity drives M&A and investor communications.",
        "Climate and underwriting-transition narratives are expanding the corporate-affairs remit.",
    ],
    "asset_wealth_managers": [
        "Fee pressure, consolidation and outflows make investor and corporate narrative business-critical.",
        "Private-markets build-out and rebrands are driving senior marketing-adjacent corporate comms.",
        "Regulatory scrutiny (Consumer Duty, value-for-money) keeps reputation management front of mind.",
    ],
    "fintech_challengers": [
        "Path-to-profitability and funding scrutiny put investor and corporate narrative at a premium.",
        "Licensing, financial-crime and resilience oversight drive regulatory-comms hiring.",
        "Rapid scaling makes employer-brand and internal communications a leadership priority.",
    ],
    "telecoms": [
        "Network investment, price-rise politics and consolidation keep public-affairs and reputation comms busy.",
        "Large transformation and cost programmes drive internal-change communications.",
        "Service-outage and resilience risk make crisis communications a standing requirement.",
    ],
    "broadcast_media": [
        "Funding-model reform, restructuring and digital pivots drive heavy internal and corporate comms.",
        "Editorial-trust and impartiality scrutiny keep reputation management board-level.",
        "Streaming competition and audience strategy expand the brand-and-corporate-affairs remit.",
    ],
    # --- broad ranker sectors (fallback) -----------------------------------
    "financial_services": [
        "Consumer Duty, fraud and operational-resilience scrutiny keep regulatory and crisis comms in constant demand.",
        "Cost, consolidation and AI-adoption programmes drive large internal-change communications agendas.",
        "Results-cycle investor communications remain a core senior requirement.",
    ],
    "pharma_healthcare": [
        "Pipeline milestones, pricing-access debates and regulatory scrutiny keep corporate and product comms in demand.",
        "Restructuring and manufacturing-reshoring programmes drive internal-change communications.",
        "Patient-trust and ESG narratives are expanding the corporate-affairs remit.",
    ],
    "energy_utilities": [
        "Net-zero transition, AMP8 water investment and price-cap politics keep public-affairs and reputation comms central.",
        "Special-administration and pollution scrutiny (water) make crisis communications a standing need.",
        "Record capital programmes drive investor and stakeholder communications.",
    ],
    "technology": [
        "AI positioning, data-trust and resilience scrutiny put corporate narrative on the board agenda.",
        "Scaling and path-to-profit pressure drive investor and employer-brand communications.",
        "Regulatory attention (online safety, competition) is lifting public-affairs demand.",
    ],
    "retail_consumer": [
        "Cost-of-living pricing politics, ESG and supplier scrutiny keep consumer brands in active reputation management.",
        "Premiumisation, restructuring and automation drive investor and internal-change communications.",
        "Brand-trust and crisis readiness remain board-level priorities.",
    ],
    "industrial_manufacturing": [
        "Defence-spending and supply-chain reshoring narratives drive corporate and investor communications.",
        "Restructuring and M&A activity sustain internal-change comms demand.",
        "Safety and ESG scrutiny keep reputation management on the agenda.",
    ],
    "media_telecoms": [
        "Funding-model reform, consolidation and digital pivots drive heavy internal and corporate comms.",
        "Editorial-trust, impartiality and price-rise politics keep reputation management board-level.",
        "Streaming and network-investment strategy expand the brand-and-corporate-affairs remit.",
    ],
    "professional_services": [
        "Audit-reform, governance and quality scrutiny keep reputation and regulatory comms live.",
        "Restructuring, partnership-model change and AI adoption drive internal-change communications.",
        "Win-rate and employer-brand pressure expand the senior-comms remit.",
    ],
    "transport_logistics": [
        "Industrial relations, service-reliability and safety risk make crisis communications a standing need.",
        "Decarbonisation and franchise/contract change drive stakeholder and public-affairs comms.",
        "Restructuring programmes sustain internal-change communications demand.",
    ],
    "real_estate": [
        "Building-safety, planning politics and affordability scrutiny keep public-affairs and reputation comms central.",
        "Refinancing and restructuring activity drives investor communications.",
        "Social-housing regulation (for housing providers) sustains corporate-affairs demand.",
    ],
    "public_sector_charities": [
        "Government-communications reform and machinery-of-government change drive senior-comms turnover.",
        "Funding pressure and trust scrutiny make reputation and campaign communications business-critical for charities.",
        "Major programmes and reorganisations drive internal-change communications.",
    ],
}


# What is pulling senior MARKETING leaders into the market, by cohort/sector.
# Marketing's value frame is growth/brand/pipeline, not reputation/risk.
_MARKETING: dict[str, list[str]] = {
    "global_consumer_brands": [
        "Brand investment, premiumisation and DTC/ecommerce growth are the board's main demand levers.",
        "Retail-media and first-party-data shifts are reshaping marketing leadership and martech ownership.",
        "Portfolio reinvention and new-category launches drive senior brand and growth hiring.",
    ],
    "premium_luxury": [
        "Clienteling, brand desirability and experiential marketing are core growth levers in luxury.",
        "Digital flagship and DTC build-out is driving senior brand and ecommerce leadership demand.",
        "Younger-audience and China/Middle-East strategies put a premium on brand-marketing leadership.",
    ],
    "grocery_retail": [
        "Loyalty, retail-media monetisation and personalisation are the dominant growth and marketing-leadership themes.",
        "Own-brand reinvention and value positioning drive brand-marketing hiring.",
        "Ecommerce and app-led customer journeys expand the digital-marketing remit.",
    ],
    "hospitality_leisure": [
        "Loyalty, app ordering and demand generation are central to recovering covers and spend.",
        "Brand differentiation and experiential marketing drive senior growth hiring.",
        "CRM and data-led marketing are expanding the leadership remit.",
    ],
    "uk_banks": [
        "Mass-affluent and SME acquisition targets drive demand-generation and brand-marketing leadership.",
        "Digital-first journeys and personalisation are reshaping martech and growth ownership.",
        "Brand-trust and challenger pressure put a premium on brand-marketing capability.",
    ],
    "insurers": [
        "Direct-to-consumer acquisition, price-comparison performance and retention drive growth-marketing hiring.",
        "Brand differentiation in a commoditised market is a board-level marketing priority.",
        "Data and CRM ownership are expanding the senior-marketing remit.",
    ],
    "asset_wealth_managers": [
        "Brand build-out, private-markets positioning and adviser/intermediary marketing drive senior hiring.",
        "Digital distribution and content-led demand generation are growing priorities.",
        "Rebrands and consolidation create brand-marketing leadership needs.",
    ],
    "fintech_challengers": [
        "Growth, performance and lifecycle marketing sit at the centre of the path-to-profit agenda.",
        "Brand build-out beyond performance channels is a recurring senior-hire trigger.",
        "Product-marketing and category creation drive leadership demand.",
    ],
    "telecoms": [
        "Acquisition, retention and convergence bundles drive demand-generation and brand-marketing leadership.",
        "Brand differentiation in a price-led market is a board priority.",
        "Digital and CRM-led customer journeys expand the marketing remit.",
    ],
    "broadcast_media": [
        "Audience growth, subscriber acquisition and retention drive senior marketing hiring.",
        "Streaming-product marketing and data-led audience strategy are expanding leadership remits.",
        "Brand and content-marketing differentiation is a board-level growth lever.",
    ],
    "financial_services": [
        "Customer acquisition, retention and digital-journey performance drive growth-marketing leadership demand.",
        "Brand-trust and challenger pressure put a premium on brand-marketing capability.",
        "First-party data and martech ownership are reshaping senior-marketing roles.",
    ],
    "pharma_healthcare": [
        "Consumer-health brand building, DTC and omnichannel HCP marketing drive senior hiring.",
        "Launch excellence and category growth are board-level marketing priorities.",
        "Digital and data-led engagement is expanding the marketing remit.",
    ],
    "energy_utilities": [
        "Customer acquisition/retention in a switching market and net-zero propositions drive marketing leadership demand.",
        "Brand-trust and value messaging are board priorities under price scrutiny.",
        "Digital and CRM-led journeys expand the senior-marketing remit.",
    ],
    "technology": [
        "Demand generation, product marketing and pipeline ownership are core to the growth agenda.",
        "Category creation and brand build-out drive senior-marketing hiring.",
        "Marketing-attribution and martech leadership are board-level priorities.",
    ],
    "retail_consumer": [
        "Loyalty, retail-media, personalisation and DTC growth dominate the marketing-leadership agenda.",
        "Brand reinvention and premiumisation drive senior brand hiring.",
        "Ecommerce and first-party data are reshaping the marketing remit.",
    ],
    "industrial_manufacturing": [
        "B2B demand generation, ABM and digital-pipeline build-out drive senior-marketing hiring where it exists.",
        "Brand and category positioning support growth and M&A integration.",
        "Marketing-technology adoption is expanding the leadership remit.",
    ],
    "media_telecoms": [
        "Audience and subscriber growth, retention and convergence drive senior marketing hiring.",
        "Brand and product-marketing differentiation is a board-level growth lever.",
        "Data-led audience strategy is expanding leadership remits.",
    ],
    "professional_services": [
        "Pipeline, ABM and thought-leadership marketing drive senior-marketing demand.",
        "Brand differentiation and win-rate pressure are board priorities.",
        "Marketing-technology and data ownership are expanding the remit.",
    ],
    "transport_logistics": [
        "Customer acquisition/retention and digital booking journeys drive growth-marketing hiring.",
        "Brand differentiation in price-led markets is a board priority.",
        "CRM and loyalty are expanding the senior-marketing remit.",
    ],
    "real_estate": [
        "Customer/occupier acquisition, brand and digital marketing drive senior hiring across developers and platforms.",
        "Placemaking and brand differentiation support sales and lettings growth.",
        "Data-led marketing is expanding the leadership remit.",
    ],
    "public_sector_charities": [
        "Fundraising, supporter acquisition and retention make marketing business-critical for charities.",
        "Brand and campaign marketing drive senior hiring.",
        "Digital and data-led engagement is expanding the remit.",
    ],
}


def strategic_context(key: str | None, profile_key: str = "comms") -> list[str] | None:
    """Sector/cohort-level strategic context for the given key (an affinity
    cohort key or a broad sector key). Returns 2-3 bullet strings, or None if
    the key isn't recognised. Profile-aware: comms vs marketing demand drivers."""
    if not key:
        return None
    table = _MARKETING if profile_key == "marketing" else _COMMS
    return table.get(key)
