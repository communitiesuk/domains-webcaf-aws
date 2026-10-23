from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from webcaf.webcaf.notification import NotifyError, send_notify_email


class Command(BaseCommand):
    help = "Sends the configured GOV.UK Notify test template to one email address"

    def add_arguments(self, parser):
        parser.add_argument("email_address", help="Email address that should receive the test notification")

    def handle(self, *args, **options):
        email_address = options["email_address"]
        try:
            validate_email(email_address)
        except ValidationError:
            raise CommandError("Enter a valid email address") from None

        if not settings.NOTIFY_TEST_TEMPLATE_ID:
            raise CommandError("NOTIFY_TEST_TEMPLATE_ID is not configured")

        try:
            responses = send_notify_email(
                email_addresses=[email_address],
                personalisation_data={},
                template_id=settings.NOTIFY_TEST_TEMPLATE_ID,
            )
        except (ImproperlyConfigured, NotifyError) as error:
            raise CommandError(str(error)) from None

        notification_id = responses[0].get("id")
        if not notification_id:
            raise CommandError("GOV.UK Notify accepted the request without returning a notification ID")

        self.stdout.write(self.style.SUCCESS(f"Test notification accepted (notification ID: {notification_id})"))
