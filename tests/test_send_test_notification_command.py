from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from webcaf.webcaf.notification import NotifyError, NotifyFailure, NotifyFailureCategory


@override_settings(
    NOTIFY_API_KEY="test-api-key",  # pragma: allowlist secret
    NOTIFY_TEST_TEMPLATE_ID="test-template-id",
)
class SendTestNotificationCommandTest(SimpleTestCase):
    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_sends_test_notification(self, send_notify_email):
        send_notify_email.return_value = [{"id": "notification-id"}]
        stdout = StringIO()

        call_command("send_test_notification", "alice@example.com", stdout=stdout)

        send_notify_email.assert_called_once_with(
            email_addresses=["alice@example.com"],
            personalisation_data={},
            template_id="test-template-id",
        )
        self.assertIn("notification-id", stdout.getvalue())
        self.assertNotIn("alice@example.com", stdout.getvalue())
        self.assertNotIn("al***@example.com", stdout.getvalue())
        self.assertNotIn("@", stdout.getvalue())

    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_rejects_invalid_email_address(self, send_notify_email):
        with self.assertRaisesRegex(CommandError, "valid email address"):
            call_command("send_test_notification", "not-an-email")

        send_notify_email.assert_not_called()

    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_reports_missing_api_key_from_shared_sender(self, send_notify_email):
        send_notify_email.side_effect = ImproperlyConfigured("NOTIFY_API_KEY is required to send notifications")

        with self.assertRaisesRegex(CommandError, "NOTIFY_API_KEY"):
            call_command("send_test_notification", "alice@example.com")

        send_notify_email.assert_called_once()

    @override_settings(NOTIFY_TEST_TEMPLATE_ID="")
    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_requires_test_template(self, send_notify_email):
        with self.assertRaisesRegex(CommandError, "NOTIFY_TEST_TEMPLATE_ID"):
            call_command("send_test_notification", "alice@example.com")

        send_notify_email.assert_not_called()

    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_reports_safe_notify_failure(self, send_notify_email):
        send_notify_email.side_effect = NotifyError(
            [NotifyFailure(NotifyFailureCategory.AUTHENTICATION, status_code=403)]
        )

        with self.assertRaisesRegex(CommandError, "statuses: 403") as raised:
            call_command("send_test_notification", "alice@example.com")

        self.assertNotIn("alice@example.com", str(raised.exception))

    @patch("webcaf.webcaf.management.commands.send_test_notification.send_notify_email")
    def test_requires_notification_id_in_response(self, send_notify_email):
        send_notify_email.return_value = [{}]

        with self.assertRaisesRegex(CommandError, "notification ID"):
            call_command("send_test_notification", "alice@example.com")
