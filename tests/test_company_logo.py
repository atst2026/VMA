"""Tests for tool.company_logo (pitch-pack cover logo via logo.dev).

Run: python3 -m unittest tests.test_company_logo
These tests are network-free — the logo.dev fetch is monkeypatched. A
live check against belron.com lives in the module's __main__ CLI.
"""
import io
import os
import unittest

from PIL import Image

from tool import company_logo as cl


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rich_logo(w=256, h=128) -> Image.Image:
    """A multi-colour image that should pass validation."""
    img = Image.new("RGB", (w, h), "white")
    px = img.load()
    for x in range(w):
        for y in range(h):
            px[x, y] = ((x * 7) % 256, (y * 5) % 256, (x * y) % 256)
    return img


def _monogram(w=256, h=256) -> Image.Image:
    """Flat tile + a single block 'glyph' — mimics logo.dev's fallback."""
    img = Image.new("RGB", (w, h), (40, 60, 90))
    px = img.load()
    for x in range(110, 146):
        for y in range(90, 160):
            px[x, y] = (255, 255, 255)
    return img


class TestNormalizeAndDomain(unittest.TestCase):
    def test_normalize_strips_suffixes(self):
        self.assertEqual(cl._normalize("BELRON UK Ltd"), "belron")
        self.assertEqual(cl._normalize("Tesco PLC"), "tesco")
        self.assertEqual(cl._normalize("HSBC Holdings"), "hsbc")

    def test_domain_for_registered(self):
        self.assertEqual(cl.domain_for("Belron"), "belron.com")
        self.assertEqual(cl.domain_for("Belron Group"), "belron.com")

    def test_domain_for_unknown_is_none(self):
        self.assertIsNone(cl.domain_for("Acme Widgets Ltd"))
        self.assertIsNone(cl.domain_for(""))

    def test_domain_for_bare_domain_passthrough(self):
        self.assertEqual(cl.domain_for("belron.com"), "belron.com")
        self.assertEqual(cl.domain_for("https://belron.com/about"), "belron.com")


class TestValidation(unittest.TestCase):
    def test_placeholder_rejected(self):
        self.assertTrue(cl._is_placeholder(_monogram()))

    def test_rich_logo_accepted(self):
        self.assertFalse(cl._is_placeholder(_rich_logo()))

    def test_process_good_image_returns_data_uri(self):
        uri = cl._process(_png_bytes(_rich_logo()), box_h=96)
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_process_too_small_rejected(self):
        self.assertIsNone(cl._process(_png_bytes(_rich_logo(64, 32)), box_h=96))

    def test_process_fully_transparent_rejected(self):
        clear = Image.new("RGBA", (256, 128), (0, 0, 0, 0))
        self.assertIsNone(cl._process(_png_bytes(clear), box_h=96))

    def test_process_not_an_image_rejected(self):
        self.assertIsNone(cl._process(b"not a png", box_h=96))


class TestLogoDataUri(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("LOGODEV_TOKEN")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("LOGODEV_TOKEN", None)
        else:
            os.environ["LOGODEV_TOKEN"] = self._saved

    def test_no_token_returns_none(self):
        os.environ.pop("LOGODEV_TOKEN", None)
        self.assertIsNone(cl.logo_data_uri("Belron"))

    def test_unknown_company_returns_none_even_with_token(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        self.assertIsNone(cl.logo_data_uri("Acme Widgets Ltd"))

    def test_good_fetch_returns_data_uri(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        good = _png_bytes(_rich_logo())
        orig = cl._fetch_png
        cl._fetch_png = lambda domain, token: good
        try:
            uri = cl.logo_data_uri("Belron")
        finally:
            cl._fetch_png = orig
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/png;base64,"))

    def test_failed_fetch_returns_none(self):
        os.environ["LOGODEV_TOKEN"] = "pk_test"
        orig = cl._fetch_png
        cl._fetch_png = lambda domain, token: None
        try:
            self.assertIsNone(cl.logo_data_uri("Belron"))
        finally:
            cl._fetch_png = orig


if __name__ == "__main__":
    unittest.main()
