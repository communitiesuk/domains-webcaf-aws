import logging
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from webcaf.webcaf.models import Tip


class TipNotificationTest(SimpleTestCase):
    @override_settings(NOTIFY_TIP_APPROVED_TEMPLATE_ID="tip-template")
    @patch("webcaf.webcaf.models.send_notify_email", side_effect=RuntimeError("sensitive provider response"))
    @patch("webcaf.webcaf.models.Settings.get_instance")
    @patch("webcaf.webcaf.models.UserProfile.objects.filter")
    def test_does_not_log_tip_exception_details(self, filter_profiles, get_settings, send_notify_email):
        filter_profiles.return_value.values_list.return_value = ["lead@example.com"]
        get_settings.return_value.gov_assure_email = ""
        assessment = SimpleNamespace(
            system=SimpleNamespace(
                organisation_id=1,
                name="Example system",
                organisation=SimpleNamespace(name="Example organisation"),
            ),
            assessment_period="25/26",
            get_framework_display=lambda: "Cyber Assessment Framework",
        )
        tip = SimpleNamespace(
            review=SimpleNamespace(reference="REV001", assessment=assessment),
            submitted_by=None,
            submitted_date_time=None,
            LOGGER=logging.getLogger("Tip"),
        )

        with self.assertLogs("Tip", level="ERROR") as logs:
            Tip.send_email(tip, "approved")

        send_notify_email.assert_called_once()
        self.assertNotIn("sensitive provider response", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
