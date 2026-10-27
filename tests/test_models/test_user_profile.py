from django.contrib.auth.models import User
from django.test import TestCase

from webcaf.webcaf.models import Organisation, UserProfile


class UserProfileStrTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="someone@example.gov.uk", email="someone@example.gov.uk")

    def test_str_with_an_organisation(self):
        organisation = Organisation.objects.create(name="Test Council")
        profile = UserProfile.objects.create(user=self.user, organisation=organisation, role="cyber_advisor")

        self.assertEqual(str(profile), "someone@example.gov.uk - Test Council - (Cyber advisor)")

    def test_str_without_an_organisation_does_not_raise(self):
        # organisation is nullable on the model; the admin list calls str() on every row
        profile = UserProfile.objects.create(user=self.user, role="cyber_advisor")

        self.assertEqual(str(profile), "someone@example.gov.uk - No organisation - (Cyber advisor)")
