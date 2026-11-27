import time
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import BACKEND_SESSION_KEY, get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from freezegun import freeze_time

from webcaf.entra_admin import BACKEND
from webcaf.webcaf.models import EntraAdminIdentity

TENANT_ID = str(uuid4())
CLIENT_ID = str(uuid4())
CALLBACK_URI = "https://webcaf.example/admin/entra/callback/"


@override_settings(
    ENTRA_ADMIN_ENABLED=True,
    ENTRA_ADMIN_TENANT_ID=TENANT_ID,
    ENTRA_ADMIN_CLIENT_ID=CLIENT_ID,
    ENTRA_ADMIN_REDIRECT_URI=CALLBACK_URI,
    AUTHENTICATION_BACKENDS=[BACKEND, "django.contrib.auth.backends.ModelBackend"],
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    ENABLED_2FA=False,
)
class EntraAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("staff", is_staff=True)
        self.object_id = uuid4()
        provider = patch("webcaf.entra_admin.entra_client")
        self.msal = provider.start().return_value
        self.addCleanup(provider.stop)
        self.flow = {"auth_uri": "https://login.microsoftonline.com/authorize", "state": "state"}
        self.msal.initiate_auth_code_flow.return_value = self.flow
        signing_keys = patch("webcaf.entra_admin.entra_signing_keys")
        jwks = signing_keys.start().return_value
        self.addCleanup(signing_keys.stop)
        jwks.get_signing_key_from_jwt.return_value.key = self.signing_key.public_key()
        self.set_id_token()

    def set_id_token(self, *, signing_key=None, **overrides):
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
            "aud": CLIENT_ID,
            "exp": int(time.time()) + 300,
            "sub": "entra-subject",
            "tid": TENANT_ID,
            "oid": str(self.object_id),
        }
        claims.update(overrides)
        self.msal.acquire_token_by_auth_code_flow.return_value = {
            "id_token": jwt.encode(claims, signing_key or self.signing_key, algorithm="RS256"),
        }

    def start_login(self):
        response = self.client.get(reverse("admin:login"))
        self.assertRedirects(response, self.flow["auth_uri"], fetch_redirect_response=False)
        self.msal.initiate_auth_code_flow.assert_called_once_with(scopes=[], redirect_uri=CALLBACK_URI)

    def callback(self):
        return self.client.get(reverse("entra-admin-callback"), {"code": "test-code", "state": "state"})

    def test_linked_staff_user_gets_admin_access_with_django_permissions(self):
        identity = EntraAdminIdentity.objects.create(user=self.user, tenant_id=TENANT_ID, object_id=self.object_id)
        self.start_login()
        self.assertRedirects(self.callback(), reverse("admin:index"))
        self.assertEqual(self.client.session[BACKEND_SESSION_KEY], BACKEND)
        self.assertEqual(self.client.get(reverse("admin:auth_user_add")).status_code, 403)

        identity.delete()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_unlinked_user_is_denied_without_creating_an_account(self):
        self.start_login()
        self.assertEqual(self.callback().status_code, 403)
        self.assertNotIn(BACKEND_SESSION_KEY, self.client.session)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_wrong_tenant_and_invalid_state_are_denied(self):
        EntraAdminIdentity.objects.create(user=self.user, tenant_id=TENANT_ID, object_id=self.object_id)
        self.start_login()
        self.msal.acquire_token_by_auth_code_flow.side_effect = RuntimeError("sensitive provider details")
        with self.assertLogs("webcaf.entra_admin", "WARNING") as logs:
            self.assertEqual(self.callback().status_code, 403)
        self.assertNotIn("sensitive provider details", " ".join(logs.output))
        self.assertEqual(self.callback().status_code, 403)
        self.msal.acquire_token_by_auth_code_flow.assert_called_once()

        self.msal.reset_mock()
        self.msal.acquire_token_by_auth_code_flow.side_effect = None
        self.set_id_token(tid=str(uuid4()))
        self.start_login()
        self.assertEqual(self.callback().status_code, 403)

    def test_invalid_tokens_cannot_sign_in_a_linked_staff_user(self):
        EntraAdminIdentity.objects.create(user=self.user, tenant_id=TENANT_ID, object_id=self.object_id)
        for fields in (
            {"iss": "https://wrong-issuer.example"},
            {"aud": str(uuid4())},
            {"exp": int(time.time()) - 1},
            {"signing_key": rsa.generate_private_key(public_exponent=65537, key_size=2048)},
        ):
            with self.subTest(fields=fields):
                self.msal.reset_mock()
                self.set_id_token(**fields)
                self.start_login()
                self.assertEqual(self.callback().status_code, 403)
                self.assertNotIn(BACKEND_SESSION_KEY, self.client.session)

    def test_admin_session_expires_after_fifteen_minutes_of_inactivity(self):
        EntraAdminIdentity.objects.create(user=self.user, tenant_id=TENANT_ID, object_id=self.object_id)
        with freeze_time() as clock:
            self.start_login()
            self.assertRedirects(self.callback(), reverse("admin:index"))
            clock.tick(timedelta(minutes=14))
            self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
            clock.tick(timedelta(minutes=15))
            response = self.client.get(reverse("admin:index"), follow=True)
            self.assertRedirects(response, reverse("session-expired"))
            self.assertNotIn(BACKEND_SESSION_KEY, self.client.session)

    def test_password_backend_session_does_not_grant_admin_access(self):
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.assertEqual(self.client.post(reverse("admin:login")).status_code, 405)

    @override_settings(ENTRA_ADMIN_ENABLED=False)
    def test_disabled_uses_existing_admin_login(self):
        self.assertEqual(self.client.get(reverse("admin:login")).status_code, 200)
        self.assertEqual(self.callback().status_code, 404)
        self.msal.initiate_auth_code_flow.assert_not_called()
