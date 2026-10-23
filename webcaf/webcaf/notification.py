import logging
import time
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from notifications_python_client import NotificationsAPIClient
from notifications_python_client.errors import APIError

logger = logging.getLogger(__name__)


class NotifyError(Exception):
    """A GOV.UK Notify failure that is safe to expose in application logs."""

    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        message = "GOV.UK Notify request failed"
        if status_code is not None:
            message = f"{message} with status {status_code}"
        super().__init__(message)


def _is_retryable(error: Exception) -> bool:
    if not isinstance(error, APIError):
        return False
    # Do not retry a timeout or invalid response because Notify may already have
    # accepted the request, and the send endpoint is not idempotent.
    return error.response is not None and (error.status_code == 429 or error.status_code >= 500)


def send_notify_email(
    email_addresses: list[str],
    personalisation_data: dict[str, Any],
    template_id: str,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> list[dict[str, Any]]:
    """
    Sends an email notification using the Gov Notify service with the specified
    template and personalisation data. It utilizes the `NotificationsAPIClient`
    to send the email to the designated recipient.
    :param retry_delay: Retry delay. Each retry will be delayed by
    retry_delay * retry (1-max_retries) seconds
    :param max_retries: How many times to retry before giving up
    :param email_addresses: A list of email addresses to send the notification to.
    :param personalisation_data: A dictionary containing key-value pairs for
        personalisation fields to be inserted into the email template.
    :param template_id: The ID of the email template to be used for the notification.
        If not provided, a default template must be predefined or handled appropriately.
    :return: The response for each notification accepted by GOV.UK Notify.
    :raises NotifyError: If GOV.UK Notify rejects the request or remains unavailable.
    """
    if not email_addresses:
        return []
    if not settings.NOTIFY_API_KEY:
        raise ImproperlyConfigured("NOTIFY_API_KEY is required to send notifications")
    if not template_id:
        raise ImproperlyConfigured("A GOV.UK Notify template ID is required to send notifications")

    try:
        notify_client = NotificationsAPIClient(
            settings.NOTIFY_API_KEY,
            timeout=settings.NOTIFY_API_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.error("GOV.UK Notify client could not be initialised")
        raise NotifyError() from None
    responses = []
    unique_email_addresses = list(dict.fromkeys(email_addresses))
    for index, email_address in enumerate(unique_email_addresses):
        retry_count = 0
        while True:
            try:
                response = notify_client.send_email_notification(
                    # You can give only 1 email address per call
                    # https://docs.notifications.service.gov.uk/python.html#email-address-required
                    email_address=email_address,
                    template_id=template_id,
                    personalisation=personalisation_data,
                )
                responses.append(response)
                break
            except Exception as error:
                status_code = error.status_code if isinstance(error, APIError) else None
                if _is_retryable(error) and retry_count < max_retries:
                    retry_count += 1
                    delay_seconds = retry_delay * retry_count
                    logger.warning(
                        "GOV.UK Notify request failed transiently with status %s; retrying in %s seconds "
                        "(attempt %s of %s)",
                        status_code or "unknown",
                        delay_seconds,
                        retry_count,
                        max_retries,
                    )
                    time.sleep(delay_seconds)  # backoff before retry
                else:
                    logger.error(
                        "GOV.UK Notify request failed with status %s",
                        status_code or "unknown",
                    )
                    raise NotifyError(status_code) from None
        if index < len(unique_email_addresses) - 1:
            time.sleep(retry_delay)

    return responses
