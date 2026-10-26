from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings

from webcaf.webcaf.models import GovNotifyEmailDevice


class GovNotifyEmailDeviceTest(SimpleTestCase):
    @override_settings(SSO_MODE="external", NOTIFY_OTP_TEMPLATE_ID="")
    @patch("webcaf.webcaf.models.send_notify_email")
    def test_does_not_send_otp_when_template_is_not_configured(self, send_notify_email):
        user = User(first_name="Alice", last_name="Example", email="alice@example.com")
        device = GovNotifyEmailDevice(user=user, email=user.email)

        with self.assertLogs("webcaf.webcaf.models", level="INFO") as logs:
            device.send_mail("123456")

        send_notify_email.assert_not_called()
        self.assertEqual(
            logs.output,
            ["INFO:webcaf.webcaf.models:GOV.UK Notify: OTP notification is disabled because no template is configured"],
        )
