from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from webcaf.context_processors import variable_page_content


class GoogleAnalyticsContextProcessorTest(SimpleTestCase):
    def test_ga4_measurement_id_is_exposed_to_templates(self):
        with patch.dict("os.environ", {"GOOGLE_ANALYTICS_ID": "G-XXXXXXXXXX"}):
            context = variable_page_content(None)

        self.assertEqual(context["GOOGLE_ANALYTICS_ID"], "G-XXXXXXXXXX")
        self.assertEqual(context["GOOGLE_ANALYTICS_TYPE"], "ga4")

    def test_gtm_container_id_remains_supported(self):
        with patch.dict("os.environ", {"GOOGLE_ANALYTICS_ID": "GTM-TEST"}):
            context = variable_page_content(None)

        self.assertEqual(context["GOOGLE_ANALYTICS_ID"], "GTM-TEST")
        self.assertEqual(context["GOOGLE_ANALYTICS_TYPE"], "gtm")

    def test_invalid_id_disables_analytics(self):
        with patch.dict("os.environ", {"GOOGLE_ANALYTICS_ID": "not-a-google-id"}):
            context = variable_page_content(None)

        self.assertEqual(context["GOOGLE_ANALYTICS_ID"], "")
        self.assertEqual(context["GOOGLE_ANALYTICS_TYPE"], "")


class GoogleAnalyticsTemplateTest(SimpleTestCase):
    def test_ga4_tag_is_inert_until_cookie_consent(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.csp_nonce = "test-nonce"

        html = render_to_string(
            "base.html",
            {"GOOGLE_ANALYTICS_ID": "G-XXXXXXXXXX", "GOOGLE_ANALYTICS_TYPE": "ga4"},
            request=request,
        )

        self.assertIn('type="unknown/type" id="google-analytics-script" data-analytics-script', html)
        self.assertIn("https://www.googletagmanager.com/gtag/js?id=", html)
        self.assertIn("w.gtag('config',i)", html)
        self.assertNotIn("googletagmanager.com/ns.html", html)

    def test_analytics_markup_is_omitted_without_configuration(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.csp_nonce = "test-nonce"

        html = render_to_string(
            "base.html",
            {"GOOGLE_ANALYTICS_ID": "", "GOOGLE_ANALYTICS_TYPE": ""},
            request=request,
        )

        self.assertNotIn("data-analytics-script", html)
        self.assertNotIn("cookie-banner-done", html)
