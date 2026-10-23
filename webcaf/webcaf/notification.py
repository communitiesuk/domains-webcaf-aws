import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from notifications_python_client import NotificationsAPIClient
from notifications_python_client.errors import APIError, InvalidResponse, TokenError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

MAX_RETRY_DELAY_SECONDS = 60.0


class NotifyFailureCategory(StrEnum):
    """Safe, stable categories for GOV.UK Notify failures."""

    AUTHENTICATION = "authentication"
    BAD_REQUEST = "bad_request"
    CLIENT_INITIALISATION = "client_initialisation"
    DAILY_LIMIT = "daily_limit"
    INVALID_RESPONSE = "invalid_response"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    TRANSPORT = "transport"
    UNEXPECTED = "unexpected"
    VALIDATION = "validation"


@dataclass(frozen=True)
class NotifyFailure:
    category: NotifyFailureCategory
    status_code: int | None = None
    accepted: bool = False


class NotifyError(Exception):
    """A GOV.UK Notify outcome that is safe to expose in application logs."""

    def __init__(self, failures: list[NotifyFailure], accepted_count: int = 0):
        self.accepted_count = accepted_count
        self.failed_count = sum(not failure.accepted for failure in failures)
        self.accepted_without_response_count = sum(failure.accepted for failure in failures)
        self.categories = tuple(dict.fromkeys(failure.category for failure in failures))
        self.status_codes = tuple(
            dict.fromkeys(
                failure.status_code for failure in failures if not failure.accepted and failure.status_code is not None
            )
        )
        self.status_code = self.status_codes[0] if len(self.status_codes) == 1 else None

        outcome_parts = []
        if self.failed_count:
            outcome_parts.append(f"{self.failed_count} request(s) failed")
        if self.accepted_without_response_count:
            outcome_parts.append(f"{self.accepted_without_response_count} request(s) accepted with an invalid response")
        if self.accepted_count:
            outcome_parts.append(f"{self.accepted_count} request(s) accepted")

        message = f"GOV.UK Notify batch completed with {'; '.join(outcome_parts)}"
        if self.categories:
            message = f"{message}; categories: {', '.join(self.categories)}"
        if self.status_codes:
            message = f"{message}; statuses: {', '.join(str(status) for status in self.status_codes)}"
        super().__init__(message)


_ERROR_TYPE_CATEGORIES = {
    "AuthError": NotifyFailureCategory.AUTHENTICATION,
    "BadRequestError": NotifyFailureCategory.BAD_REQUEST,
    "RateLimitError": NotifyFailureCategory.RATE_LIMIT,
    "TooManyRequestsError": NotifyFailureCategory.DAILY_LIMIT,
    "ValidationError": NotifyFailureCategory.VALIDATION,
}


def _notify_error_types(error: APIError) -> tuple[str, ...]:
    if error.response is None:
        return ()
    try:
        response_data = error.response.json()
        errors = response_data.get("errors", [])
    except (AttributeError, TypeError, ValueError):
        return ()
    if not isinstance(errors, list):
        return ()
    return tuple(
        error_data["error"]
        for error_data in errors
        if isinstance(error_data, dict) and error_data.get("error") in _ERROR_TYPE_CATEGORIES
    )


def _classify_failure(error: Exception) -> NotifyFailure:
    if isinstance(error, InvalidResponse):
        response = error.response
        accepted = response is not None and 200 <= response.status_code < 300
        return NotifyFailure(NotifyFailureCategory.INVALID_RESPONSE, accepted=accepted)
    if isinstance(error, TokenError):
        return NotifyFailure(NotifyFailureCategory.AUTHENTICATION)
    if not isinstance(error, APIError):
        return NotifyFailure(NotifyFailureCategory.UNEXPECTED)
    if error.response is None:
        return NotifyFailure(NotifyFailureCategory.TRANSPORT)

    status_code = error.status_code
    error_types = _notify_error_types(error)
    if error_types:
        return NotifyFailure(_ERROR_TYPE_CATEGORIES[error_types[0]], status_code=status_code)
    if status_code in {401, 403}:
        category = NotifyFailureCategory.AUTHENTICATION
    elif status_code >= 500:
        category = NotifyFailureCategory.SERVER_ERROR
    else:
        category = NotifyFailureCategory.BAD_REQUEST
    return NotifyFailure(category, status_code=status_code)


def _is_retryable(error: Exception) -> bool:
    if not isinstance(error, APIError) or error.response is None or isinstance(error, InvalidResponse):
        return False
    error_types = set(_notify_error_types(error))
    if "TooManyRequestsError" in error_types:
        return False
    if "RateLimitError" in error_types:
        return True
    return error.status_code in {500, 503}


def _retry_delay_seconds(error: APIError, retry_count: int, retry_delay: float) -> float:
    delay = retry_delay * (2 ** (retry_count - 1))
    if error.response is not None:
        retry_after = error.response.headers.get("Retry-After")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
    return min(delay, MAX_RETRY_DELAY_SECONDS)


def _configure_connection_retries(notify_client: NotificationsAPIClient, max_retries: int, retry_delay: float) -> None:
    """Retry only failures that urllib3 knows happened before a request reached Notify."""
    retry_policy = Retry(
        total=max_retries,
        connect=max_retries,
        read=0,
        redirect=0,
        status=0,
        other=0,
        allowed_methods=None,
        backoff_factor=retry_delay,
        backoff_max=MAX_RETRY_DELAY_SECONDS,
    )
    notify_client.request_session.mount("https://", HTTPAdapter(max_retries=retry_policy))


def _unique_email_addresses(email_addresses: list[str]) -> list[str]:
    seen = set()
    unique_addresses = []
    for email_address in email_addresses:
        key = email_address.casefold()
        if key not in seen:
            seen.add(key)
            unique_addresses.append(email_address)
    return unique_addresses


def send_notify_email(
    email_addresses: list[str],
    personalisation_data: dict[str, Any],
    template_id: str,
    max_retries: int = 2,
    retry_delay: float = 1.0,
) -> list[dict[str, Any]]:
    """Send one email per unique recipient and return Notify's accepted responses.

    Connection-establishment failures and documented transient responses are
    retried with bounded backoff. Every recipient is attempted. If any request
    fails, or Notify accepts one but returns an invalid response, a safe
    aggregate ``NotifyError`` is raised after the batch completes.
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
        _configure_connection_retries(notify_client, max_retries, retry_delay)
    except Exception:
        raise NotifyError([NotifyFailure(NotifyFailureCategory.CLIENT_INITIALISATION)]) from None

    responses = []
    failures = []
    for email_address in _unique_email_addresses(email_addresses):
        retry_count = 0
        while True:
            try:
                response = notify_client.send_email_notification(
                    email_address=email_address,
                    template_id=template_id,
                    personalisation=personalisation_data,
                )
                responses.append(response)
                break
            except Exception as error:
                if isinstance(error, APIError) and _is_retryable(error) and retry_count < max_retries:
                    retry_count += 1
                    delay_seconds = _retry_delay_seconds(error, retry_count, retry_delay)
                    failure = _classify_failure(error)
                    logger.warning(
                        "GOV.UK Notify request failed transiently (%s); retrying in %s seconds (attempt %s of %s)",
                        failure.category,
                        delay_seconds,
                        retry_count,
                        max_retries,
                    )
                    time.sleep(delay_seconds)
                    continue
                failures.append(_classify_failure(error))
                break

    if failures:
        raise NotifyError(failures, accepted_count=len(responses)) from None
    return responses
