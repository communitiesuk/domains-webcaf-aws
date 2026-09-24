from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.urls import reverse


class RedisSessionStorageTest(TestCase):
    def test_session_round_trips_without_database_storage(self):
        self.assertEqual(settings.SESSION_ENGINE, "django.contrib.sessions.backends.cache")
        self.assertEqual(
            settings.CACHES[settings.SESSION_CACHE_ALIAS]["BACKEND"],
            "django.core.cache.backends.redis.RedisCache",
        )

        session_store = import_module(settings.SESSION_ENGINE).SessionStore
        session = session_store()
        session["test_key"] = "test value"
        session.save()
        self.addCleanup(session.delete)

        stored_session = session_store(session_key=session.session_key)

        self.assertEqual(stored_session["test_key"], "test value")
        self.assertFalse(Session.objects.filter(session_key=session.session_key).exists())

    @override_settings(
        SSO_MODE="local",
        OIDC_OP_LOGOUT_ENDPOINT="http://localhost:5556/auth/logout",
        OIDC_RP_CLIENT_ID="my-django-app",
        LOGOUT_REDIRECT_URL="http://localhost:8010/",
    )
    def test_logout_deletes_redis_session(self):
        user = get_user_model().objects.create_user(username="redis-session-user")
        self.client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")

        session = self.client.session
        session["oidc_id_token"] = "redis-session-id-token"
        session.save()
        session_key = session.session_key
        session_store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
        self.addCleanup(session_store.delete)
        self.assertTrue(session_store.exists(session_key))

        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(session_store.exists(session_key))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())
