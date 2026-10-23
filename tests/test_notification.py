from unittest.mock import call, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from notifications_python_client.errors import HTTPError, InvalidResponse
from requests import Response

from webcaf.webcaf.notification import NotifyError, send_notify_email


def api_error(status_code: int, message: str = "Request failed") -> HTTPError:
    response = Response()
    response.status_code = status_code
    response._content = f'{{"message": "{message}"}}'.encode()
    return HTTPError(response)


@override_settings(NOTIFY_API_KEY="test-api-key", NOTIFY_API_TIMEOUT_SECONDS=7.5)  # pragma: allowlist secret
class SendNotifyEmailTest(SimpleTestCase):
    def setUp(self):
        self.personalisation = {"first_name": "Alice", "link": "https://example.com"}
        self.template_id = "template-abc-123"

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_sends_email_and_returns_response(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.return_value = {"id": "notification-id"}

        responses = send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        mock_client_cls.assert_called_once_with("test-api-key", timeout=7.5)
        mock_client.send_email_notification.assert_called_once_with(
            email_address="alice@example.com",
            template_id=self.template_id,
            personalisation=self.personalisation,
        )
        self.assertEqual(responses, [{"id": "notification-id"}])

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_sends_to_unique_addresses_in_input_order(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            {"id": "first-id"},
            {"id": "second-id"},
        ]

        responses = send_notify_email(
            ["first@example.com", "second@example.com", "first@example.com"],
            self.personalisation,
            self.template_id,
            retry_delay=0.25,
        )

        self.assertEqual(
            [item.kwargs["email_address"] for item in mock_client.send_email_notification.call_args_list],
            ["first@example.com", "second@example.com"],
        )
        self.assertEqual(responses, [{"id": "first-id"}, {"id": "second-id"}])
        mock_sleep.assert_called_once_with(0.25)

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_initialise_client_for_empty_recipient_list(self, mock_client_cls):
        self.assertEqual(send_notify_email([], self.personalisation, self.template_id), [])
        mock_client_cls.assert_not_called()

    @override_settings(NOTIFY_API_KEY="")
    def test_requires_api_key(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "NOTIFY_API_KEY"):
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

    def test_requires_template_id(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "template ID"):
            send_notify_email(["alice@example.com"], self.personalisation, "")

    @patch("webcaf.webcaf.notification.NotificationsAPIClient", side_effect=AssertionError("invalid key"))
    def test_wraps_client_initialisation_error(self, mock_client_cls):
        with self.assertRaisesRegex(NotifyError, "GOV.UK Notify request failed") as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        mock_client_cls.assert_called_once_with("test-api-key", timeout=7.5)
        self.assertNotIn("invalid key", str(raised.exception))

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_retries_transient_failures_with_backoff(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            api_error(429),
            api_error(503),
            {"id": "notification-id"},
        ]

        responses = send_notify_email(
            ["alice@example.com"],
            self.personalisation,
            self.template_id,
            max_retries=2,
            retry_delay=0.5,
        )

        self.assertEqual(responses, [{"id": "notification-id"}])
        self.assertEqual(mock_client.send_email_notification.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(0.5), call(1.0)])

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_invalid_response(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = InvalidResponse(message="invalid response")

        with self.assertRaisesRegex(NotifyError, "status 503"):
            send_notify_email(
                ["alice@example.com"],
                self.personalisation,
                self.template_id,
                max_retries=1,
                retry_delay=0.5,
            )

        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_response_less_network_failure(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = HTTPError()

        with self.assertRaisesRegex(NotifyError, "status 503"):
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_invalid_request(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = api_error(400)

        with self.assertRaisesRegex(NotifyError, "status 400"):
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_authentication_error(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = api_error(403)

        with self.assertRaisesRegex(NotifyError, "status 403"):
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_raises_safe_error_after_transient_retries_exhausted(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = api_error(503)

        with self.assertRaisesRegex(NotifyError, "status 503"):
            send_notify_email(
                ["alice@example.com"],
                self.personalisation,
                self.template_id,
                max_retries=2,
                retry_delay=0.5,
            )

        self.assertEqual(mock_client.send_email_notification.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(0.5), call(1.0)])

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_logs_no_recipient_or_provider_message(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        secret_provider_message = "alice@example.com bad-secret-value"  # pragma: allowlist secret
        mock_client.send_email_notification.side_effect = api_error(400, secret_provider_message)

        with self.assertLogs("webcaf.webcaf.notification", level="ERROR") as log:
            with self.assertRaises(NotifyError):
                send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        output = " ".join(log.output)
        self.assertNotIn("alice@example.com", output)
        self.assertNotIn("al***@example.com", output)
        self.assertNotIn(secret_provider_message, output)
        self.assertNotIn("bad-secret-value", output)

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_wraps_unexpected_error_without_exposing_its_message(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = RuntimeError("sensitive data")

        with self.assertRaisesRegex(NotifyError, "GOV.UK Notify request failed") as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertNotIn("sensitive data", str(raised.exception))
