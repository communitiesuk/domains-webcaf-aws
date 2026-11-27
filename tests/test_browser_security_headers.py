from django.test import SimpleTestCase
from django.urls import reverse


class BrowserSecurityHeadersTest(SimpleTestCase):
    def test_public_page_sets_browser_security_headers(self):
        response = self.client.get(reverse("cookies"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-DNS-Prefetch-Control"], "off")
        policy = response.headers["Content-Security-Policy"]
        directives = {}
        for directive in policy.split(";"):
            name, *sources = directive.split()
            directives[name] = sources
        self.assertEqual(directives["base-uri"], ["'none'"])
        script_sources = directives["script-src"]
        self.assertIn("https://www.googletagmanager.com", script_sources)
        self.assertNotIn("https://*.googletagmanager.com", script_sources)
        self.assertFalse(any(source.lstrip("'").startswith("sha256-") for source in script_sources))

    def test_redirect_disables_dns_prefetch(self):
        cookies_url = reverse("cookies")
        response = self.client.get(cookies_url.rstrip("/"), secure=True)

        self.assertRedirects(response, cookies_url, status_code=301, fetch_redirect_response=False)
        self.assertEqual(response.headers["X-DNS-Prefetch-Control"], "off")
