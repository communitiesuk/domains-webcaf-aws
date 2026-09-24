import base64
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from govuk_onelogin_django.types import AuthenticationLevel, IdentityConfidenceLevel

from webcaf.auth import EMAIL_DOMAIN_REJECTED_SESSION_KEY

ONE_LOGIN_SETTINGS = {
    "SSO_MODE": "one-login",
    "LOGIN_URL": "one_login:login",
    "GOV_UK_ONE_LOGIN_CLIENT_ID": "webcaf-test-client",
    "GOV_UK_ONE_LOGIN_CLIENT_SECRET": base64.b64encode(b"test-private-key").decode("ascii"),
    "GOV_UK_ONE_LOGIN_OPENID_CONFIG_URL": "https://example.com/.well-known/openid-configuration",
    "GOV_UK_ONE_LOGIN_SCOPE": "openid email",
    "GOV_UK_ONE_LOGIN_AUTHENTICATION_LEVEL": AuthenticationLevel.MEDIUM_LEVEL,
    "GOV_UK_ONE_LOGIN_CONFIDENCE_LEVEL": IdentityConfidenceLevel.NONE,
    "ALLOWED_HOSTS": ["localhost", "testserver"],
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
}


@override_settings(**ONE_LOGIN_SETTINGS)
class OneLoginFlowTest(TestCase):
    def setUp(self):
        cache.clear()

    @patch("govuk_onelogin_django.utils.requests.get")
    def test_login_redirect_contains_required_parameters(self, requests_get):
        discovery_response = Mock()
        discovery_response.json.return_value = {
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "userinfo_endpoint": "https://example.com/userinfo",
            "jwks_uri": "https://example.com/jwks",
            "end_session_endpoint": "https://example.com/logout",
            "issuer": "https://example.com/",
        }
        discovery_response.raise_for_status.return_value = None
        requests_get.return_value = discovery_response

        response = self.client.get(reverse("one_login:login"), HTTP_HOST="localhost:8010")

        self.assertEqual(response.status_code, 302)
        parsed_url = urlparse(response.url)
        query = parse_qs(parsed_url.query)
        self.assertEqual(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path), ("https", "example.com", "/authorize")
        )
        self.assertEqual(query["client_id"], ["webcaf-test-client"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:8010/one-login/callback/"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["openid email"])
        self.assertEqual(query["vtr"], ['["Cl.Cm.P0"]'])
        self.assertTrue(query["nonce"][0])
        self.assertTrue(query["state"][0])

    @patch("govuk_onelogin_django.utils.requests.get")
    def test_forwarded_https_is_used_for_callback_url(self, requests_get):
        discovery_response = Mock()
        discovery_response.json.return_value = {
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "userinfo_endpoint": "https://example.com/userinfo",
            "jwks_uri": "https://example.com/jwks",
            "end_session_endpoint": "https://example.com/logout",
            "issuer": "https://example.com/",
        }
        discovery_response.raise_for_status.return_value = None
        requests_get.return_value = discovery_response

        response = self.client.get(
            reverse("one_login:login"),
            HTTP_HOST="localhost:8010",
            HTTP_X_FORWARDED_PROTO="https",
        )

        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["redirect_uri"], ["https://localhost:8010/one-login/callback/"])

    def test_provider_error_redirects_to_authentication_error(self):
        response = self.client.get(reverse("one_login:callback"), {"error": "access_denied"})

        self.assertRedirects(response, reverse("authentication-error"), fetch_redirect_response=False)

    def test_incomplete_callback_redirects_to_authentication_error(self):
        response = self.client.get(reverse("one_login:callback"))

        self.assertRedirects(response, reverse("authentication-error"), fetch_redirect_response=False)

    def test_unresolved_user_redirects_to_authentication_error(self):
        session = self.client.session
        session["_one_login_token_oauth_state"] = "expected-state"
        session.save()

        with patch("govuk_onelogin_django.views.AuthCallbackView.get", return_value=Mock(status_code=302)):
            response = self.client.get(
                reverse("one_login:callback"),
                {"code": "code", "state": "expected-state"},
            )

        self.assertRedirects(response, reverse("authentication-error"), fetch_redirect_response=False)

    def test_authentication_error_page_is_public(self):
        response = self.client.get(reverse("authentication-error"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "There is a problem signing you in")
        self.assertContains(response, reverse("logout"))

    def test_rejected_email_domain_shows_explicit_error(self):
        session = self.client.session
        session[EMAIL_DOMAIN_REJECTED_SESSION_KEY] = True
        session["oidc_id_token"] = "failed-login-token"
        session.save()

        response = self.client.get(reverse("authentication-error"))

        self.assertContains(response, "Your email domain is not approved")
        self.assertContains(response, reverse("logout"))
        self.assertNotIn(EMAIL_DOMAIN_REJECTED_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.session["oidc_id_token"], "failed-login-token")
