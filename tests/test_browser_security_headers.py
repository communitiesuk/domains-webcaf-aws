from django.test import SimpleTestCase
from django.urls import reverse


class BrowserSecurityHeadersTest(SimpleTestCase):
    def test_public_page_sets_browser_security_headers(self):
        response = self.client.get(reverse("cookies"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-DNS-Prefetch-Control"], "off")
        directives = response.headers["Content-Security-Policy"].split(";")
        self.assertIn("base-uri 'none'", [directive.strip() for directive in directives])
