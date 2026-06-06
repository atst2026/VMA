"""Tests for the cover-logo path in tool.pitch_proposal (logo.dev source).

Run: python3 -m unittest tests.test_pitch_proposal_logo
Network-free — the logo.dev fetch is monkeypatched. A live check against a
registered company is done at build time (see the PR notes).
"""
import io
import os
import unittest

from PIL import Image

from tool import pitch_proposal as pp


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rich(w=256, h=128) -> Image.Image:
    img = Image.new("RGB", (w, h), "white")
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = ((x * 7) % 256, (y * 5) % 256, (x * y) % 256)
    return img


def _monogram(w=256, h=256) -> Image.Image:
    img = Image.new("RGB", (w, h), (40, 60, 90))
    px = img.load()
    for x in range(110, 146):
        for y in range(90, 160):
            px[x, y] = (255, 255, 255)
    return img


class TestValidation(unittest.TestCase):
    def test_placeholder_rejected(self):
        self.assertTrue(pp._logo_is_placeholder(_monogram()))

    def test_rich_logo_accepted(self):
        self.assertFalse(pp._logo_is_placeholder(_rich()))

    def test_process_good_returns_data_uri(self):
        uri = pp._process_logo(_png(_rich()), pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W)
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_process_too_small_rejected(self):
        self.assertIsNone(pp._process_logo(_png(_rich(64, 32)),
                                           pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_process_fully_transparent_rejected(self):
        clear = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        self.assertIsNone(pp._process_logo(_png(clear),
                                           pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_process_not_an_image_rejected(self):
        self.assertIsNone(pp._process_logo(b"nope",
                                           pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))


class TestCoverLogoHtml(unittest.TestCase):
    """The logo.dev FALLBACK path (P154 disabled), kept network-free."""

    def setUp(self):
        from tool import company_domain
        self.cd = company_domain
        self._saved = (os.environ.get("LOGODEV_TOKEN"),
                       self.cd.wikidata_logo_png, self.cd.resolve_domain,
                       pp._fetch_logo_png)
        # Disable the P154 path for these logo.dev-focused tests.
        self.cd.wikidata_logo_png = lambda company: None
        self.cd.resolve_domain = lambda company: "diageo.com"

    def tearDown(self):
        tok, wm, rd, fp = self._saved
        self.cd.wikidata_logo_png, self.cd.resolve_domain = wm, rd
        pp._fetch_logo_png = fp
        if tok is None:
            os.environ.pop("LOGODEV_TOKEN", None)
        else:
            os.environ["LOGODEV_TOKEN"] = tok

    def test_no_token_falls_back_to_wordmark(self):
        os.environ.pop("LOGODEV_TOKEN", None)
        html = pp._cover_logo_html("Diageo")
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)

    def test_unknown_company_falls_back_even_with_token(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        self.cd.resolve_domain = lambda company: None
        html = pp._cover_logo_html("Some Unlisted Startup Ltd")
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)

    def test_good_logo_emits_img(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        pp._fetch_logo_png = lambda domain, token: _png(_rich())
        html = pp._cover_logo_html("Diageo")
        self.assertIn('<img class="client-logo"', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn('alt="Diageo"', html)

    def test_failed_fetch_falls_back_to_wordmark(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        pp._fetch_logo_png = lambda domain, token: None
        html = pp._cover_logo_html("Diageo")
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)


def _wide(w=400, h=130):
    img = Image.new("RGB", (w, h), "white")
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = ((x * 3) % 256, (y * 9) % 256, (x * y) % 256)
    return _png(img)


class TestVisibility(unittest.TestCase):
    def test_dark_image_visible(self):
        self.assertTrue(pp._passes_visibility(_wide()))

    def test_fully_transparent_invisible(self):
        self.assertFalse(pp._passes_visibility(_png(Image.new("RGBA", (300, 120), (0, 0, 0, 0)))))

    def test_white_on_transparent_invisible(self):
        img = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
        px = img.load()
        for x in range(60, 240):
            for y in range(40, 80):
                px[x, y] = (255, 255, 255, 255)
        self.assertFalse(pp._passes_visibility(_png(img)))


class TestWikidataFirst(unittest.TestCase):
    """P154 is preferred over logo.dev, with quality + visibility fallbacks."""

    def setUp(self):
        from tool import company_domain
        self.cd = company_domain
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        self._saved = (self.cd.wikidata_logo_png, self.cd.resolve_domain,
                       pp._fetch_logo_png)
        self.cd.resolve_domain = lambda company: "example.com"
        # logo.dev fallback returns a recognisable square icon.
        self.icon = _png(_rich(120, 120))
        pp._fetch_logo_png = lambda domain, token: self.icon

    def tearDown(self):
        self.cd.wikidata_logo_png, self.cd.resolve_domain, pp._fetch_logo_png = self._saved
        os.environ.pop("LOGODEV_TOKEN", None)

    def _uri(self):
        return pp._company_logo_data_uri("Whoever", pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W)

    def test_p154_preferred_when_passes(self):
        wide = _wide()
        self.cd.wikidata_logo_png = lambda company: wide
        self.assertEqual(self._uri(),
                         pp._process_logo(wide, pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_fallback_to_logodev_when_p154_missing(self):
        self.cd.wikidata_logo_png = lambda company: None
        self.assertEqual(self._uri(),
                         pp._process_logo(self.icon, pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_fallback_when_p154_fails_visibility(self):
        self.cd.wikidata_logo_png = lambda company: _png(Image.new("RGBA", (400, 130), (0, 0, 0, 0)))
        self.assertEqual(self._uri(),
                         pp._process_logo(self.icon, pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_fallback_when_p154_fails_quality_gate(self):
        # Too small -> _process_logo returns None -> logo.dev used.
        self.cd.wikidata_logo_png = lambda company: _png(_rich(40, 40))
        self.assertEqual(self._uri(),
                         pp._process_logo(self.icon, pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))


if __name__ == "__main__":
    unittest.main()
