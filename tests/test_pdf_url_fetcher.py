"""
Tests for StaticFileURLFetcher, which serves PDF assets from STATIC_ROOT only.
"""

import tempfile
from pathlib import Path
from urllib.error import URLError

from django.test import SimpleTestCase, override_settings

from webcaf.webcaf.pdf import StaticFileURLFetcher


class StaticFileURLFetcherTest(SimpleTestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.static_root = Path(temp_dir.name) / "static"
        (self.static_root / "css").mkdir(parents=True)
        (self.static_root / "css" / "pdf.css").write_text("body { color: black; }")
        # A file next to STATIC_ROOT, which the fetcher must never serve.
        (Path(temp_dir.name) / "secret.txt").write_text("not a static asset")

        settings_override = override_settings(STATIC_ROOT=str(self.static_root))
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def fetch(self, url):
        response = StaticFileURLFetcher().fetch(url)
        self.addCleanup(response.close)
        return response

    def test_loads_a_static_asset(self):
        response = self.fetch("http://testserver/assets/css/pdf.css")

        self.assertEqual(response.read(), b"body { color: black; }")
        self.assertEqual(Path(response.path), (self.static_root / "css" / "pdf.css").resolve())

    def test_refuses_paths_outside_static_root(self):
        for url in (
            "http://testserver/assets/../secret.txt",
            "http://testserver/assets/../../../../etc/passwd",
            "../../etc/passwd",
        ):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValueError, "outside STATIC_ROOT"):
                    StaticFileURLFetcher().fetch(url)

    def test_never_fetches_from_the_network(self):
        # An external URL is still answered from STATIC_ROOT, never fetched.
        response = self.fetch("https://example.com/assets/css/pdf.css")

        self.assertTrue(response.url.startswith("file:"))
        self.assertEqual(response.read(), b"body { color: black; }")

        with self.assertRaises(URLError):
            StaticFileURLFetcher().fetch("https://example.com/style.css")

    def test_missing_file_raises_an_error(self):
        # WeasyPrint catches this, logs it and renders the PDF without the asset.
        with self.assertRaises(URLError):
            StaticFileURLFetcher().fetch("http://testserver/assets/css/missing.css")
