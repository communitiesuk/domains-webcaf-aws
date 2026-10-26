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

    def test_list_renders_with_a_profile_that_has_no_organisation(self):
        organisation = Organisation.objects.create(name="Test Council")
        UserProfile.objects.create(user=self.admin, organisation=organisation, role="cyber_advisor")
        orphan = User.objects.create_user(username="orphan@example.gov.uk", email="orphan@example.gov.uk")
        UserProfile.objects.create(user=orphan)

        response = self.client.get(reverse("admin:webcaf_userprofile_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "orphan@example.gov.uk")
