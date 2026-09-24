from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class LogoutViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="user", email="user@example.com")
        self.client.force_login(self.user)

    @override_settings(SSO_MODE="one-login")
    @patch("webcaf.webcaf.views.general.get_one_login_logout_url")
    def test_one_login_logout_uses_id_token_before_clearing_session(self, get_logout_url):
        session = self.client.session
        session["_one_login_token"] = {"id_token": "one-login-id-token"}
        session.save()

        def build_logout_url(request, post_logout_redirect_uri):
            self.assertEqual(request.session["_one_login_token"]["id_token"], "one-login-id-token")
            self.assertEqual(post_logout_redirect_uri, "http://testserver/")
            return "https://example.com/logout?token=one-login-id-token"

        get_logout_url.side_effect = build_logout_url

        response = self.client.post(reverse("logout"))

        self.assertRedirects(
            response,
            "https://example.com/logout?token=one-login-id-token",
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(
        SSO_MODE="local",
        OIDC_OP_LOGOUT_ENDPOINT="http://localhost:5556/auth/logout",
        OIDC_RP_CLIENT_ID="my-django-app",
        LOGOUT_REDIRECT_URL="http://localhost:8010/",
    )
    def test_unauthenticated_oidc_logout_parameters_are_encoded(self):
        self.client.logout()
        session = self.client.session
        session["oidc_id_token"] = "token/value+with spaces"
        session.save()

        response = self.client.post(reverse("logout"))

        parsed_url = urlparse(response.url)
        self.assertEqual(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path), ("http", "localhost:5556", "/auth/logout")
        )
        self.assertEqual(
            parse_qs(parsed_url.query),
            {
                "id_token_hint": ["token/value+with spaces"],
                "client_id": ["my-django-app"],
                "redirect_uri": ["http://localhost:8010/"],
            },
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(SSO_MODE="one-login")
    @patch("webcaf.webcaf.views.general.get_one_login_logout_url", side_effect=KeyError("id_token"))
    def test_provider_logout_failure_still_clears_local_session(self, get_logout_url):
        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("index"))
        self.assertNotIn("_auth_user_id", self.client.session)
        get_logout_url.assert_called_once()

    def test_logout_requires_post(self):
        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_content_security_policy_allows_configured_logout_provider(self):
        response = self.client.get(reverse("index"))

        self.assertIn("form-action 'self' http://localhost:5556", response["Content-Security-Policy"])
