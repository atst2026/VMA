"""Tests for the cover-logo path in tool.pitch_proposal (logo.dev source).

Run: python3 -m unittest tests.test_pitch_proposal_logo
Network-free — the logo.dev fetch is monkeypatched. A live check against a
registered company is done at build time (see the PR notes).
"""
import io
import os
import unittest

from PIL import Image, ImageDraw

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


def _monogram_on_transparent(s=256) -> Image.Image:
    """A square single-letter mark on a TRANSPARENT field (logo.dev's 'M'
    fallback style) — must STILL be rejected after the white-composite fix."""
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    px = img.load()
    for x in range(110, 146):
        for y in range(90, 160):
            px[x, y] = (0, 0, 0, 255)
    return img


def _black_wordmark_on_transparent(w=420, h=130) -> Image.Image:
    """A WIDE monochrome (black) wordmark on a TRANSPARENT field — the
    Morgan-Stanley class that used to collapse to one RGB colour and be wrongly
    rejected. Must now be ACCEPTED. Proportions kept realistic (aspect ~3-4)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for x in range(20, w - 20):
        for y in range(35, 95):
            if (x // 10) % 2 == 0:        # vertical strokes ~ "text"
                px[x, y] = (0, 0, 0, 255)
    return img


class TestValidation(unittest.TestCase):
    def test_placeholder_rejected(self):
        self.assertTrue(pp._logo_is_placeholder(_monogram()))

    def test_rich_logo_accepted(self):
        self.assertFalse(pp._logo_is_placeholder(_rich()))

    def test_square_monogram_on_transparent_still_rejected(self):
        # Genuine blank/monogram tile on transparency stays rejected.
        self.assertTrue(pp._logo_is_placeholder(_monogram_on_transparent()))

    def test_black_wordmark_on_transparent_accepted(self):
        # The fix: a monochrome wordmark on transparency is no longer collapsed
        # to one colour, and the aspect guard keeps a wide mark from the flag.
        wm = _black_wordmark_on_transparent()
        self.assertFalse(pp._logo_is_placeholder(wm))
        self.assertIsNotNone(pp._process_logo(_png(wm), pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

    def test_letter_monogram_rejected_despite_antialiasing(self):
        # logo.dev's generated letter-monogram: a single anti-aliased glyph
        # (many grey edge colours, so NOT colour-uniform) on a square white
        # canvas with tiny ink coverage -> must be rejected by the ink backstop.
        img = Image.new("RGB", (256, 256), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.ellipse((96, 80, 160, 176), outline=(20, 20, 20), width=10)  # an "O" ring
        self.assertTrue(pp._logo_is_placeholder(img))
        self.assertIsNone(pp._process_logo(_png(img), pp._COVER_LOGO_MAX_H, pp._COVER_LOGO_MAX_W))

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

    def test_padded_canvas_logo_visible(self):
        # A genuine dark logo on a big padded Commons canvas: the ink fraction
        # over the RAW canvas is < 2%, but the content-box measure must pass
        # it (previously this silently fell back to the bare logo.dev symbol).
        img = Image.new("RGBA", (1200, 1200), (0, 0, 0, 0))
        px = img.load()
        for x in range(500, 700):
            for y in range(550, 650):
                px[x, y] = (10, 30, 60, 255)
        self.assertTrue(pp._passes_visibility(_png(img)))


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

    def test_logodev_white_wordmark_rejected(self):
        # The visible-on-white gate must also cover the logo.dev branch: a
        # wide white-on-transparent wordmark is exempt from the placeholder
        # check (wide = wordmark) and would land invisible on the white cover.
        self.cd.wikidata_logo_png = lambda company: None
        white = Image.new("RGBA", (420, 130), (0, 0, 0, 0))
        px = white.load()
        for x in range(20, 400):
            for y in range(35, 95):
                px[x, y] = (255, 255, 255, 255)
        pp._fetch_logo_png = lambda domain, token: _png(white)
        self.assertIsNone(self._uri())


class TestP154CurrentLogo(unittest.TestCase):
    """_p154_filename must pick the CURRENT logo: preferred rank first, then
    claims without an end-time (P582) qualifier, else statement order."""

    def setUp(self):
        from tool import company_domain
        self.cd = company_domain
        self._orig = self.cd._wd_get
        self.claims = [
            {"rank": "normal", "qualifiers": {"P582": [{}]},
             "mainsnak": {"datavalue": {"value": "Old logo.svg"}}},
            {"rank": "normal",
             "mainsnak": {"datavalue": {"value": "Mid logo.svg"}}},
            {"rank": "preferred",
             "mainsnak": {"datavalue": {"value": "Current logo.svg"}}},
        ]
        self.cd._wd_get = lambda params: {
            "entities": {"Q1": {"claims": {"P154": self.claims}}}}

    def tearDown(self):
        self.cd._wd_get = self._orig

    def test_preferred_rank_wins(self):
        self.assertEqual(self.cd._p154_filename("Q1"), "Current logo.svg")

    def test_unended_claim_wins_without_preferred(self):
        self.claims[2]["rank"] = "normal"
        self.assertEqual(self.cd._p154_filename("Q1"), "Mid logo.svg")

    def test_deprecated_skipped(self):
        self.claims[1]["rank"] = self.claims[2]["rank"] = "deprecated"
        self.assertEqual(self.cd._p154_filename("Q1"), "Old logo.svg")


if __name__ == "__main__":
    unittest.main()
