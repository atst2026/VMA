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

    def test_name_match(self):
        self.assertEqual(cd._name_match_score("Deliveroo", "Deliveroo"), 1.0)
        self.assertGreaterEqual(cd._name_match_score("Wetherspoons", "Wetherspoons"), cd._ACCEPT_SCORE)
        self.assertLess(cd._name_match_score("Deliveroo", "Just Eat"), cd._ACCEPT_SCORE)
        self.assertLess(cd._name_match_score("Monzo", "Mondelez"), cd._ACCEPT_SCORE)

    def test_strip_domain(self):
        self.assertEqual(cd._strip_domain("https://www.deliveroo.co.uk/menu"), "deliveroo.co.uk")
        self.assertEqual(cd._strip_domain("http://jdwetherspoon.com?x=1"), "jdwetherspoon.com")

    def test_pick_domain_prefers_primary_tld(self):
        # Deliveroo lists the Italian site first; we want the .co.uk one.
        self.assertEqual(cd._pick_domain(["deliveroo.it", "deliveroo.co.uk"]), "deliveroo.co.uk")
        self.assertEqual(cd._pick_domain(["jdwetherspoon.co.uk", "jdwetherspoon.com"]), "jdwetherspoon.com")
        self.assertIsNone(cd._pick_domain([]))


class TestResolveDomain(unittest.TestCase):
    def setUp(self):
        cd._DOMAIN_CACHE.clear()

    def tearDown(self):
        cd._DOMAIN_CACHE.clear()

    def _patch(self, search, entity):
        """Install fake Wikidata layers; return the originals for restore."""
        o1, o2 = cd._search_entities, cd._entity_company_and_domain
        cd._search_entities, cd._entity_company_and_domain = search, entity
        return o1, o2

    def _restore(self, originals):
        cd._search_entities, cd._entity_company_and_domain = originals

    def test_registry_override_wins_without_network(self):
        called = {"n": 0}
        orig = self._patch(
            lambda name: called.__setitem__("n", called["n"] + 1) or [],
            lambda qid: (False, None, []),
        )
        try:
            # Diageo is in tool/company_identity with a verified domain.
            self.assertEqual(cd.resolve_domain("Diageo"), "diageo.com")
        finally:
            self._restore(orig)
        self.assertEqual(called["n"], 0)  # never touched Wikidata

    def test_company_with_website_and_name_match_accepted(self):
        orig = self._patch(
            lambda name: [{"id": "Q1", "label": "Deliveroo", "match": "", "description": ""}],
            lambda qid: (True, "deliveroo.co.uk", ["Deliveroo"]),
        )
        try:
            self.assertEqual(cd.resolve_domain("Deliveroo"), "deliveroo.co.uk")
        finally:
            self._restore(orig)

    def test_name_match_uses_aliases(self):
        # Label differs ("J D Wetherspoon") but an alias matches the lead.
        orig = self._patch(
            lambda name: [{"id": "Q6109362", "label": "J D Wetherspoon", "match": "", "description": ""}],
            lambda qid: (True, "jdwetherspoon.com", ["J D Wetherspoon", "Wetherspoons"]),
        )
        try:
            self.assertEqual(cd.resolve_domain("Wetherspoons"), "jdwetherspoon.com")
        finally:
            self._restore(orig)

    def test_non_company_entity_rejected(self):
        # A real entity with a website, but NOT instance-of a company.
        orig = self._patch(
            lambda name: [{"id": "Q2", "label": "Mercury", "match": "", "description": "planet"}],
            lambda qid: (False, "nasa.gov", ["Mercury"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Mercury"))
        finally:
            self._restore(orig)

    def test_low_name_match_rejected(self):
        # A company with a website, but the name is clearly different.
        orig = self._patch(
            lambda name: [{"id": "Q3", "label": "Totally Different Brand", "match": "", "description": "company"}],
            lambda qid: (True, "elsewhere.com", ["Totally Different Brand"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Deliveroo"))
        finally:
            self._restore(orig)

    def test_company_without_website_rejected(self):
        orig = self._patch(
            lambda name: [{"id": "Q4", "label": "Deliveroo", "match": "", "description": "company"}],
            lambda qid: (True, None, ["Deliveroo"]),
        )
        try:
            self.assertIsNone(cd.resolve_domain("Deliveroo"))
        finally:
            self._restore(orig)

    def test_no_search_results_falls_back(self):
        orig = self._patch(lambda name: [], lambda qid: (False, None, []))
        try:
            self.assertIsNone(cd.resolve_domain("Some Obscure Startup XYZ"))
        finally:
            self._restore(orig)

    def test_result_cached_no_second_call(self):
        calls = {"n": 0}

        def _search(name):
            calls["n"] += 1
            return [{"id": "Q1", "label": "Deliveroo", "match": "", "description": ""}]
        orig = self._patch(_search, lambda qid: (True, "deliveroo.co.uk", ["Deliveroo"]))
        try:
            cd.resolve_domain("Deliveroo")
            cd.resolve_domain("deliveroo")   # same normalised key
            cd.resolve_domain("Deliveroo Ltd")  # also normalises to "deliveroo"
        finally:
            self._restore(orig)
        self.assertEqual(calls["n"], 1)

    def test_first_candidate_wrong_second_right(self):
        # Search returns an unrelated entity first, the real company second.
        def _search(name):
            return [
                {"id": "QA", "label": "Deliveroo (song)", "match": "", "description": "single"},
                {"id": "QB", "label": "Deliveroo", "match": "", "description": "company"},
            ]
        def _entity(qid):
            return {"QA": (False, None, ["Deliveroo (song)"]),
                    "QB": (True, "deliveroo.co.uk", ["Deliveroo"])}[qid]
        orig = self._patch(_search, _entity)
        try:
            self.assertEqual(cd.resolve_domain("Deliveroo"), "deliveroo.co.uk")
        finally:
            self._restore(orig)


if __name__ == "__main__":
    unittest.main()
