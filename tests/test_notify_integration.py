import os
from unittest import skipUnless

from django.test import SimpleTestCase, override_settings

from webcaf.webcaf.notification import send_notify_email


@skipUnless(
    os.environ.get("RUN_GOVUK_NOTIFY_INTEGRATION_TESTS") == "true",
    "requires dedicated GOV.UK Notify test credentials",
)
class GovUkNotifyIntegrationTest(SimpleTestCase):
    def test_sends_notification_using_test_credentials(self):
        api_key = os.environ.get("NOTIFY_INTEGRATION_API_KEY")
        template_id = os.environ.get("NOTIFY_INTEGRATION_TEMPLATE_ID")
        self.assertTrue(api_key, "NOTIFY_INTEGRATION_API_KEY must be configured")
        self.assertTrue(template_id, "NOTIFY_INTEGRATION_TEMPLATE_ID must be configured")

        with override_settings(
            NOTIFY_API_KEY=api_key,
            NOTIFY_API_TIMEOUT_SECONDS=10,
        ):
            responses = send_notify_email(
                email_addresses=["webcaf-notify-integration@example.com"],
                personalisation_data={},
                template_id=template_id,
                max_retries=0,
            )

        self.assertTrue(responses[0].get("id"))
