"""
Integration tests for the interaction between ``LastOrganisationCookieMiddleware``
and ``AccountView.get_or_pick_user_profile``.

These exercise the full request flow through the real middleware stack (the middleware
is registered in ``settings.MIDDLEWARE``), covering:

  * First landing with no session profile and no ``last_org`` cookie -> the first
    profile is picked and the middleware writes the ``last_org`` cookie on the response.
  * A subsequent request driven purely by the ``last_org`` cookie -> the cookied profile
    is selected even though the session has no ``current_profile_id``.
  * An invalid ``last_org`` cookie -> selection falls back to the first profile.
  * End-to-end persistence: the cookie set by the middleware on one request is honoured
    by the view on the next request made through the same client.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tests.test_views.base_view_test import BaseViewTest
from webcaf.webcaf.models import UserProfile


@pytest.mark.django_db
class TestAccountMiddlewareIntegration(BaseViewTest):
    def setUp(self):
        """A single user that owns two profiles in two different organisations."""
        self.client = Client()
        self.user = self.org_map[self.organisation_name]["users"]["organisation_user"]

        # First profile (lowest id) belongs to the "Big organisation".
        self.first_profile = UserProfile.objects.get(user=self.user, organisation=self.test_organisation)

        # Second profile in a different organisation, guaranteed to have a higher id.
        self.other_organisation = self.org_map["Medium organisation"]["organisation"]
        self.second_profile = UserProfile.objects.create(
            user=self.user, organisation=self.other_organisation, role="organisation_user"
        )

        self.client.force_login(self.user)

    def test_first_landing_picks_first_profile_and_sets_cookie(self):
        """No session profile and no cookie -> first profile chosen and cookie written."""
        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        # View selected the lowest-id profile and stored it in the session.
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.first_profile.id)
        self.assertEqual(response.context["profile_count"], 2)
        # Middleware wrote the last_org cookie matching the selected profile.
        self.assertIn("last_org", response.cookies)
        self.assertEqual(response.cookies["last_org"].value, str(self.first_profile.id))

    def test_cookie_drives_profile_selection(self):
        """With no session profile, the last_org cookie selects the matching profile."""
        self.client.cookies["last_org"] = str(self.second_profile.id)

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.second_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.second_profile.id)

    def test_invalid_cookie_falls_back_to_first_profile(self):
        """An unusable last_org cookie value falls back to the first profile."""
        self.client.cookies["last_org"] = "999999"

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.first_profile.id)

    def test_malformed_cookie_falls_back_to_first_profile(self):
        self.client.cookies["last_org"] = "not-a-profile-id"

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.first_profile.id)

    def test_foreign_session_profile_falls_back_to_owned_profile(self):
        foreign_user = self.org_map["Medium organisation"]["users"]["organisation_user"]
        foreign_profile = UserProfile.objects.get(user=foreign_user)
        session = self.client.session
        session["current_profile_id"] = foreign_profile.id
        session.save()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.first_profile.id)

    def test_stale_session_profile_falls_back_to_owned_profile(self):
        session = self.client.session
        session["current_profile_id"] = 999999
        session.save()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.first_profile.id)

    def test_user_without_owned_profile_has_no_organisation_context(self):
        user_without_profile = User.objects.create_user(username="no-profile-user")
        foreign_profile = self.first_profile
        self.client.force_login(user_without_profile)
        session = self.client.session
        session["current_profile_id"] = foreign_profile.id
        session.save()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("current_profile_id", self.client.session)
        self.assertEqual(self.client.session["profile_count"], 0)

    def test_switches_between_profiles_owned_by_authenticated_user(self):
        response = self.client.post(
            reverse("change-organisation"),
            {"profile_id": self.second_profile.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.second_profile.id)
        self.assertEqual(response.context["current_profile"].id, self.second_profile.id)

    def test_cannot_switch_to_foreign_profile(self):
        self.client.get(reverse("my-account"))
        foreign_user = self.org_map["Medium organisation"]["users"]["organisation_user"]
        foreign_profile = UserProfile.objects.get(user=foreign_user)

        response = self.client.post(reverse("change-organisation"), {"profile_id": foreign_profile.id})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.session["current_profile_id"], self.first_profile.id)

    def test_cookie_set_by_middleware_is_honoured_on_next_request(self):
        """
        End-to-end: select the second profile so the middleware persists it in the cookie,
        then clear the session and confirm the cookie alone re-selects that profile.
        """
        # Drive the first request via a cookie pointing at the second profile.
        self.client.cookies["last_org"] = str(self.second_profile.id)
        first_response = self.client.get(reverse("my-account"))
        self.assertEqual(first_response.context["current_profile"].id, self.second_profile.id)
        # Middleware re-affirms the cookie on the response.
        self.assertEqual(self.client.cookies["last_org"].value, str(self.second_profile.id))

        # Forget the session selection; only the cookie remains.
        session = self.client.session
        del session["current_profile_id"]
        session.save()

        second_response = self.client.get(reverse("my-account"))

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(self.client.session["current_profile_id"], self.second_profile.id)
        self.assertEqual(second_response.context["current_profile"].id, self.second_profile.id)
