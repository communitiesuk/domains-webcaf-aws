import json
from unittest.mock import call, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from notifications_python_client.errors import HTTPError, InvalidResponse
from requests import Response

from webcaf.webcaf.notification import NotifyError, NotifyFailureCategory, send_notify_email


def api_error(
    status_code: int,
    error_type: str | None = None,
    message: str = "Request failed",
    retry_after: str | None = None,
) -> HTTPError:
    response = Response()
    response.status_code = status_code
    if error_type:
        response._content = json.dumps({"errors": [{"error": error_type, "message": message}]}).encode()
    else:
        response._content = json.dumps({"message": message}).encode()
    if retry_after:
        response.headers["Retry-After"] = retry_after
    return HTTPError(response)


def invalid_response(status_code: int = 201) -> InvalidResponse:
    response = Response()
    response.status_code = status_code
    response._content = b"not-json"
    return InvalidResponse(response, message="No JSON response object could be decoded")


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

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_configures_connection_only_retries(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.return_value = {"id": "notification-id"}

        send_notify_email(
            ["alice@example.com"],
            self.personalisation,
            self.template_id,
            max_retries=3,
            retry_delay=1.5,
        )

        mock_client.request_session.mount.assert_called_once()
        scheme, adapter = mock_client.request_session.mount.call_args.args
        retry_policy = adapter.max_retries
        self.assertEqual(scheme, "https://")
        self.assertEqual(retry_policy.total, 3)
        self.assertEqual(retry_policy.connect, 3)
        self.assertEqual(retry_policy.read, 0)
        self.assertEqual(retry_policy.status, 0)
        self.assertEqual(retry_policy.other, 0)
        self.assertIsNone(retry_policy.allowed_methods)
        self.assertEqual(retry_policy.backoff_factor, 1.5)
        self.assertEqual(retry_policy.backoff_max, 60.0)

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_sends_to_case_insensitively_unique_addresses_in_input_order(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            {"id": "first-id"},
            {"id": "second-id"},
        ]

        responses = send_notify_email(
            ["First@example.com", "second@example.com", "first@EXAMPLE.com"],
            self.personalisation,
            self.template_id,
        )

        self.assertEqual(
            [item.kwargs["email_address"] for item in mock_client.send_email_notification.call_args_list],
            ["First@example.com", "second@example.com"],
        )
        self.assertEqual(responses, [{"id": "first-id"}, {"id": "second-id"}])

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
        with self.assertRaises(NotifyError) as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        mock_client_cls.assert_called_once_with("test-api-key", timeout=7.5)
        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.CLIENT_INITIALISATION,))
        self.assertNotIn("invalid key", str(raised.exception))

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_retries_documented_transient_failures_with_exponential_backoff(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            api_error(429, "RateLimitError"),
            api_error(500, "Exception"),
            {"id": "notification-id"},
        ]

        responses = send_notify_email(
            ["alice@example.com"],
            self.personalisation,
            self.template_id,
            max_retries=2,
            retry_delay=1.0,
        )

        self.assertEqual(responses, [{"id": "notification-id"}])
        self.assertEqual(mock_client.send_email_notification.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(1.0), call(2.0)])

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_honours_bounded_retry_after_header(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            api_error(429, "RateLimitError", retry_after="90"),
            {"id": "notification-id"},
        ]

        send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        mock_sleep.assert_called_once_with(60.0)

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_daily_limit(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = api_error(429, "TooManyRequestsError")

        with self.assertRaises(NotifyError) as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.DAILY_LIMIT,))
        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_retry_ambiguous_gateway_failures(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value

        for status_code in (502, 504):
            with self.subTest(status_code=status_code):
                mock_client.reset_mock()
                mock_client.send_email_notification.side_effect = api_error(status_code)

                with self.assertRaises(NotifyError):
                    send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

                self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_retries_explicit_503_response(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [api_error(503), {"id": "notification-id"}]

        responses = send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(responses, [{"id": "notification-id"}])
        mock_sleep.assert_called_once_with(1.0)

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_does_not_report_response_less_transport_failure_as_503(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = HTTPError()

        with self.assertRaises(NotifyError) as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.TRANSPORT,))
        self.assertEqual(raised.exception.status_codes, ())
        self.assertNotIn("503", str(raised.exception))
        self.assertEqual(mock_client.send_email_notification.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.time.sleep")
    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_reports_successful_invalid_response_as_accepted(self, mock_client_cls, mock_sleep):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = invalid_response()

        with self.assertRaises(NotifyError) as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(raised.exception.failed_count, 0)
        self.assertEqual(raised.exception.accepted_without_response_count, 1)
        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.INVALID_RESPONSE,))
        self.assertNotIn("status 201", str(raised.exception))
        self.assertNotIn("failed", str(raised.exception))
        mock_sleep.assert_not_called()

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_continues_after_recipient_failure_then_raises_safe_aggregate(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = [
            api_error(400, "BadRequestError", "alice@example.com is not on the guest list"),
            {"id": "second-id"},
        ]

        with self.assertRaises(NotifyError) as raised:
            send_notify_email(
                ["alice@example.com", "second@example.com"],
                self.personalisation,
                self.template_id,
            )

        self.assertEqual(
            [item.kwargs["email_address"] for item in mock_client.send_email_notification.call_args_list],
            ["alice@example.com", "second@example.com"],
        )
        self.assertEqual(raised.exception.failed_count, 1)
        self.assertEqual(raised.exception.accepted_count, 1)
        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.BAD_REQUEST,))
        self.assertEqual(raised.exception.status_codes, (400,))
        self.assertNotIn("alice@example.com", str(raised.exception))
        self.assertNotIn("guest list", str(raised.exception))

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_exposes_safe_notify_error_types_without_provider_messages(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        cases = (
            ("ValidationError", NotifyFailureCategory.VALIDATION),
            ("AuthError", NotifyFailureCategory.AUTHENTICATION),
            ("RateLimitError", NotifyFailureCategory.RATE_LIMIT),
        )

        for error_type, expected_category in cases:
            with self.subTest(error_type=error_type):
                mock_client.reset_mock()
                mock_client.send_email_notification.side_effect = api_error(
                    400,
                    error_type,
                    "alice@example.com bad-secret-value",  # pragma: allowlist secret
                )

                with self.assertRaises(NotifyError) as raised:
                    send_notify_email(["alice@example.com"], self.personalisation, self.template_id, max_retries=0)

                self.assertEqual(raised.exception.categories, (expected_category,))
                self.assertNotIn("alice@example.com", str(raised.exception))
                self.assertNotIn("bad-secret-value", str(raised.exception))

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_helper_does_not_log_terminal_failures(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = api_error(400, "BadRequestError")

        with self.assertNoLogs("webcaf.webcaf.notification", level="ERROR"):
            with self.assertRaises(NotifyError):
                send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

    @patch("webcaf.webcaf.notification.NotificationsAPIClient")
    def test_wraps_unexpected_error_without_exposing_its_message(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.send_email_notification.side_effect = RuntimeError("sensitive data")

        with self.assertRaises(NotifyError) as raised:
            send_notify_email(["alice@example.com"], self.personalisation, self.template_id)

        self.assertEqual(raised.exception.categories, (NotifyFailureCategory.UNEXPECTED,))
        self.assertNotIn("sensitive data", str(raised.exception))
