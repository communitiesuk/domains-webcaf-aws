from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from webcaf.webcaf.models import Organisation, UserProfile


class UserProfileAdminListTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.gov.uk",
            password="password",  # pragma: allowlist secret
        )
        self.client.force_login(self.admin)

    def test_list_renders_valid_memberships_and_does_not_require_every_user_to_have_one(self):
        organisation = Organisation.objects.create(name="Test Council")
        UserProfile.objects.create(user=self.admin, organisation=organisation, role="cyber_advisor")
        orphan = User.objects.create_user(username="orphan@example.gov.uk", email="orphan@example.gov.uk")

        response = self.client.get(reverse("admin:webcaf_userprofile_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Council")
        self.assertFalse(UserProfile.objects.filter(user=orphan).exists())
