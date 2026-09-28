from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from features.one_login_simulator import configure_identity, subject_for_email


class OneLoginSimulatorClientTest(SimpleTestCase):
    def test_subject_is_stable_and_case_insensitive(self):
        self.assertEqual(subject_for_email("Alice@example.gov.uk"), subject_for_email("alice@example.gov.uk"))
        self.assertTrue(subject_for_email("alice@example.gov.uk").startswith("urn:fdc:gov.uk:2022:"))

    @patch("features.one_login_simulator.requests.post")
    def test_configures_verified_identity_and_clears_errors(self, post):
        response = Mock()
        post.return_value = response

        configure_identity("alice@example.gov.uk", True, "http://simulator:3000")

        post.assert_called_once_with(
            "http://simulator:3000/config",
            json={
                "responseConfiguration": {
                    "sub": subject_for_email("alice@example.gov.uk"),
                    "email": "alice@example.gov.uk",
                    "emailVerified": True,
                },
                "errorConfiguration": {},
            },
            timeout=10,
        )
        response.raise_for_status.assert_called_once_with()
