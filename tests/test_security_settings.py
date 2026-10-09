import json
import os
import subprocess
import sys

from django.test import SimpleTestCase

# Conditional settings are evaluated when the settings module is first imported.
# override_settings(DEBUG=...) would change DEBUG afterward without re-running those branches,
# so this probe runs in a fresh interpreter for each startup mode.
REQUEST_SECURITY_SCRIPT = """
import json

from django.conf import settings
from django.test import RequestFactory

factory = RequestFactory()
forwarded_https = factory.get(
    "/",
    HTTP_HOST="webcaf.example.gov.uk",
    HTTP_X_FORWARDED_PROTO="https",
)
forwarded_http = factory.get(
    "/",
    HTTP_HOST="localhost:8010",
    HTTP_X_FORWARDED_PROTO="http",
)
local_http = factory.get("/", HTTP_HOST="localhost:8010")

print(
    json.dumps(
        {
            "debug": settings.DEBUG,
            "proxy_ssl_header": settings.SECURE_PROXY_SSL_HEADER,
            "forwarded_https_secure": forwarded_https.is_secure(),
            "forwarded_https_url": forwarded_https.build_absolute_uri("/one-login/callback/"),
            "forwarded_http_secure": forwarded_http.is_secure(),
            "forwarded_http_url": forwarded_http.build_absolute_uri("/one-login/callback/"),
            "local_http_secure": local_http.is_secure(),
            "local_http_url": local_http.build_absolute_uri("/"),
            "session_cookie_secure": settings.SESSION_COOKIE_SECURE,
            "csrf_cookie_secure": settings.CSRF_COOKIE_SECURE,
            "csrf_cookie_httponly": settings.CSRF_COOKIE_HTTPONLY,
        }
    )
)
"""


class SecuritySettingsTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.settings_by_debug = {debug: cls.load_settings(debug) for debug in (True, False)}

    @staticmethod
    def load_settings(debug):
        # Explicit environment values take precedence over the local .env file and
        # make each subprocess represent a deterministic Django startup mode.
        environment = os.environ.copy()
        environment["DEBUG"] = str(debug)
        environment["DJANGO_SETTINGS_MODULE"] = "webcaf.settings"
        environment["SECRET_KEY"] = "unneeded"  # pragma: allowlist secret

        result = subprocess.run(
            [sys.executable, "-c", REQUEST_SECURITY_SCRIPT],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
        )
        return json.loads(result.stdout)

    def test_forwarded_https_is_secure_in_both_debug_modes(self):
        for debug, result in self.settings_by_debug.items():
            with self.subTest(debug=debug):
                self.assertEqual(result["debug"], debug)
                self.assertEqual(result["proxy_ssl_header"], ["HTTP_X_FORWARDED_PROTO", "https"])
                self.assertTrue(result["forwarded_https_secure"])
                self.assertEqual(
                    result["forwarded_https_url"],
                    "https://webcaf.example.gov.uk/one-login/callback/",
                )

    def test_requests_without_forwarded_https_remain_http(self):
        for debug, result in self.settings_by_debug.items():
            with self.subTest(debug=debug):
                self.assertFalse(result["forwarded_http_secure"])
                self.assertEqual(
                    result["forwarded_http_url"],
                    "http://localhost:8010/one-login/callback/",
                )
                self.assertFalse(result["local_http_secure"])
                self.assertEqual(result["local_http_url"], "http://localhost:8010/")

    def test_secure_cookie_settings_remain_environment_specific(self):
        for debug, result in self.settings_by_debug.items():
            with self.subTest(debug=debug):
                expected_secure = not debug
                self.assertEqual(result["session_cookie_secure"], expected_secure)
                self.assertEqual(result["csrf_cookie_secure"], expected_secure)
                self.assertEqual(result["csrf_cookie_httponly"], expected_secure)
