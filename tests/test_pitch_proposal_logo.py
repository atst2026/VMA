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
    def setUp(self):
        self._saved = os.environ.get("LOGODEV_TOKEN")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("LOGODEV_TOKEN", None)
        else:
            os.environ["LOGODEV_TOKEN"] = self._saved

    def test_no_token_falls_back_to_wordmark(self):
        os.environ.pop("LOGODEV_TOKEN", None)
        html = pp._cover_logo_html("Diageo")
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)

    def test_unknown_company_falls_back_even_with_token(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        html = pp._cover_logo_html("Some Unlisted Startup Ltd")
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)

    def test_good_logo_emits_img(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        good = _png(_rich())
        orig = pp._fetch_logo_png
        pp._fetch_logo_png = lambda domain, token: good
        try:
            html = pp._cover_logo_html("Diageo")  # Diageo is in company_identity
        finally:
            pp._fetch_logo_png = orig
        self.assertIn('<img class="client-logo"', html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn('alt="Diageo"', html)

    def test_failed_fetch_falls_back_to_wordmark(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        orig = pp._fetch_logo_png
        pp._fetch_logo_png = lambda domain, token: None
        try:
            html = pp._cover_logo_html("Diageo")
        finally:
            pp._fetch_logo_png = orig
        self.assertIn("client-wordmark", html)
        self.assertNotIn("<img", html)


if __name__ == "__main__":
    unittest.main()
