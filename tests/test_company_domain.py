"""Tests for tool.company_domain — name -> domain via registry then Wikidata.

Run: python3 -m unittest tests.test_company_domain
Network-free: the registry path uses the real tool.company_identity; the
Wikidata calls (_search_entities / _entity_company_and_domain) are monkeypatched.
"""
import os
import unittest

from tool import company_domain as cd


class TestHelpers(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(cd._normalize("J D Wetherspoon PLC"), "j d wetherspoon")
        self.assertEqual(cd._normalize("Deliveroo Ltd"), "deliveroo")

    def test_name_match_basic(self):
        self.assertEqual(cd._name_match_score("Deliveroo", "Deliveroo"), 1.0)
        self.assertLess(cd._name_match_score("Monzo", "Mondelez"), cd._ACCEPT_SCORE)

    def test_containment_bonus_when_clean(self):
        # "sainsbury" inside "j sainsbury" — legitimate containment.
        self.assertGreaterEqual(cd._name_match_score("Sainsbury", "J Sainsbury"), 0.9)

    def test_containment_withheld_for_subentity(self):
        # Parent org must NOT match a named sub-entity sharing the prefix.
        self.assertLess(
            cd._name_match_score("Cambridge University", "Cambridge University Cricket Club"),
            cd._ACCEPT_SCORE)

    def test_strip_domain(self):
        self.assertEqual(cd._strip_domain("https://www.deliveroo.co.uk/menu"), "deliveroo.co.uk")
        self.assertEqual(cd._strip_domain("http://jdwetherspoon.com?x=1"), "jdwetherspoon.com")

    def test_registrable_apex(self):
        self.assertEqual(cd._registrable_apex("en.powys.gov.uk"), "powys.gov.uk")
        self.assertEqual(cd._registrable_apex("cy.powys.gov.uk"), "powys.gov.uk")
        self.assertEqual(cd._registrable_apex("cam.ac.uk"), "cam.ac.uk")
        self.assertEqual(cd._registrable_apex("entaingroup.com"), "entaingroup.com")

    def test_domain_label(self):
        self.assertEqual(cd._domain_label("entaingroup.com"), "entaingroup")
        self.assertEqual(cd._domain_label("gvc-plc.com"), "gvc plc")
        self.assertEqual(cd._domain_label("powys.gov.uk"), "powys")

    def test_eligible_org_covers_public_sector(self):
        # Fix B1: universities, councils, NHS trusts, charities are eligible.
        for qid in ("Q5341295", "Q3354859",   # educational org / collegiate uni
                    "Q837766",                 # local authority
                    "Q6954187", "Q6954197",    # NHS (foundation) trust
                    "Q708676", "Q163740"):     # charity / nonprofit
            self.assertIn(qid, cd._ELIGIBLE_ORG_QIDS)


class TestPickDomain(unittest.TestCase):
    def test_name_match_beats_stale_alias_domain(self):
        # Fix A: Entain lists both gvc-plc.com (stale) and entaingroup.com.
        # The label must be matched against the LEAD name, so entaingroup wins.
        cands = [("gvc-plc.com", "normal"), ("entaingroup.com", "normal")]
        self.assertEqual(cd._pick_domain(cands, "Entain"), "entaingroup.com")

    def test_apex_dedupe_for_subdomains(self):
        cands = [("en.powys.gov.uk", "normal"), ("cy.powys.gov.uk", "normal")]
        self.assertEqual(cd._pick_domain(cands, "Powys County Council"), "powys.gov.uk")

    def test_tld_preference_when_label_ties(self):
        cands = [("deliveroo.it", "normal"), ("deliveroo.co.uk", "normal")]
        self.assertEqual(cd._pick_domain(cands, "Deliveroo"), "deliveroo.co.uk")

    def test_preferred_rank_breaks_tie(self):
        # Equal name score (no lead match); preferred rank beats normal even
        # though .com out-ranks .org on TLD.
        cands = [("brandx.com", "normal"), ("brandx.org", "preferred")]
        self.assertEqual(cd._pick_domain(cands, "", ["Brand X"]), "brandx.org")

    def test_empty(self):
        self.assertIsNone(cd._pick_domain([], "Whatever"))


class TestResolveDomain(unittest.TestCase):
    def setUp(self):
        cd._DOMAIN_CACHE.clear()

    def tearDown(self):
        cd._DOMAIN_CACHE.clear()

    def _patch(self, search, entity):
        o1, o2 = cd._search_entities, cd._entity_company_and_domain
        cd._search_entities, cd._entity_company_and_domain = search, entity
        return o1, o2

    def _restore(self, originals):
        cd._search_entities, cd._entity_company_and_domain = originals

    def test_registry_override_wins_without_network(self):
        called = {"n": 0}
        orig = self._patch(
            lambda name: called.__setitem__("n", called["n"] + 1) or [],
            lambda qid, lead="": (False, None, []),
        )
        try:
            self.assertEqual(cd.resolve_domain("Diageo"), "diageo.com")
        finally:
            self._restore(orig)
        self.assertEqual(called["n"], 0)

    def test_university_accepted_after_org_broadening(self):
        orig = self._patch(
            lambda name: [{"id": "Q35794", "label": "University of Cambridge", "match": "", "description": ""}],
            lambda qid, lead="": (True, "cam.ac.uk",
                                  ["University of Cambridge", "Cambridge University"]),
        )
        try:
            self.assertEqual(cd.resolve_domain("Cambridge University"), "cam.ac.uk")
        finally:
            self._restore(orig)

    def test_council_accepted(self):
        orig = self._patch(
            lambda name: [{"id": "Q7236943", "label": "Powys County Council", "match": "", "description": ""}],
            lambda qid, lead="": (True, "powys.gov.uk", ["Powys County Council", "Powys Council"]),
        )
        try:
            self.assertEqual(cd.resolve_domain("Powys County Council"), "powys.gov.uk")
        finally:
            self._restore(orig)

    def test_exact_match_preferred_over_fuzzy_subentity(self):
        # Press (fuzzy ~0.87) is returned first; the University (exact via
        # alias) is second. Exact-match pass must pick the University.
        def _search(name):
            return [
                {"id": "QPRESS", "label": "Cambridge University Press", "match": "", "description": "publisher"},
                {"id": "QUNI", "label": "University of Cambridge", "match": "", "description": "university"},
            ]
        def _entity(qid, lead=""):
            return {
                "QPRESS": (True, "cambridge.org", ["Cambridge University Press"]),
                "QUNI": (True, "cam.ac.uk", ["University of Cambridge", "Cambridge University"]),
            }[qid]
        orig = self._patch(_search, _entity)
        try:
            self.assertEqual(cd.resolve_domain("Cambridge University"), "cam.ac.uk")
        finally:
            self._restore(orig)

    def test_non_org_rejected(self):
        orig = self._patch(
            lambda name: [{"id": "Q2", "label": "Mercury", "match": "", "description": "planet"}],
            lambda qid, lead="": (False, "nasa.gov", ["Mercury"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Mercury"))
        finally:
            self._restore(orig)

    def test_low_name_match_rejected(self):
        orig = self._patch(
            lambda name: [{"id": "Q3", "label": "Totally Different Brand", "match": "", "description": "company"}],
            lambda qid, lead="": (True, "elsewhere.com", ["Totally Different Brand"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Deliveroo"))
        finally:
            self._restore(orig)

    def test_org_without_website_rejected(self):
        orig = self._patch(
            lambda name: [{"id": "Q4", "label": "Deliveroo", "match": "", "description": "company"}],
            lambda qid, lead="": (True, None, ["Deliveroo"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Deliveroo"))
        finally:
            self._restore(orig)

    def test_no_results_falls_back(self):
        orig = self._patch(lambda name: [], lambda qid, lead="": (False, None, []))
        try:
            self.assertIsNone(cd.resolve_domain("Some Obscure Startup XYZ"))
        finally:
            self._restore(orig)

    def test_result_cached_no_second_call(self):
        calls = {"n": 0}

        def _search(name):
            calls["n"] += 1
            return [{"id": "Q1", "label": "Deliveroo", "match": "", "description": ""}]
        orig = self._patch(_search, lambda qid, lead="": (True, "deliveroo.co.uk", ["Deliveroo"]))
        try:
            cd.resolve_domain("Deliveroo")
            cd.resolve_domain("deliveroo")
            cd.resolve_domain("Deliveroo Ltd")
        finally:
            self._restore(orig)
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
