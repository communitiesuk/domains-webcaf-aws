# GOV.UK Notify

WebCAF uses the supported
[`notifications-python-client`](https://docs.notifications.service.gov.uk/python.html) to send email notifications.
The shared integration is in `webcaf/webcaf/notification.py`.

## API key policy

Use a separate API key appropriate to each environment:

- Local development uses a Notify test key. Requests reach the real Notify API but no email is delivered.
- Sandbox uses a team and guest list key. Email is delivered only to recipients approved in the Notify service.
- Production uses a live key. A production key must not be available in local development or Sandbox.

API keys and template IDs must belong to the same Notify service. Do not commit API keys or pass them as image build
arguments. Store the Sandbox key as a GitHub Environment secret so it is masked and access-controlled in GitHub.

## Configuration

The following settings apply to the shared integration:

- `NOTIFY_API_KEY`: API credential. This is secret.
- `NOTIFY_TEST_TEMPLATE_ID`: personalisation-free template used by the test command.
- `NOTIFY_API_TIMEOUT_SECONDS`: request timeout in seconds. It must be greater than 0 and no more than 30. The default
  is 10 seconds.

The existing service journeys use these optional template settings:

- `NOTIFY_OTP_TEMPLATE_ID`
- `NOTIFY_CONFIRMATION_TEMPLATE_ID`
- `NOTIFY_ASSESSOR_EMAIL_TEMPLATE_ID`
- `NOTIFY_REVIEW_FINALISED_TEMPLATE_ID`
- `NOTIFY_TIP_APPROVED_TEMPLATE_ID`
- `NOTIFY_TIP_REJECTED_TEMPLATE_ID`
- `NOTIFY_TIP_SUBMITTED_TEMPLATE_ID`

Leaving a journey template ID blank disables that notification. The test command requires both `NOTIFY_API_KEY` and
`NOTIFY_TEST_TEMPLATE_ID`.

## Local setup

Copy `webcaf/.env.example` to `webcaf/.env` if the local environment has not already been created. Add a Notify test
API key and the personalisation-free test template ID to the ignored `webcaf/.env` file:

```dotenv
NOTIFY_API_KEY=<test-api-key>
NOTIFY_TEST_TEMPLATE_ID=<test-template-id>
```

Do not use a team or live key for local development. Check that `webcaf/.env` remains ignored before working with
credentials:

```shell
git check-ignore webcaf/.env
```

Send a test notification from the host environment:

```shell
uv run python manage.py send_test_notification developer@example.com
```

Alternatively, run it through Docker Compose:

```shell
docker compose run --rm --entrypoint python web manage.py send_test_notification developer@example.com
```

The command validates the email address and prints the Notify notification ID. It does not include the recipient in its
output. With a test API key, Notify accepts the request without delivering an email.

## Sandbox setup

Sandbox uses a team and guest list API key so test emails can be delivered to approved recipients.

1. Add each approved recipient to the team or guest list in the Sandbox Notify service.
2. Set the Sandbox GitHub Environment secret `NOTIFY_API_KEY` to the team API key.
3. Set the Sandbox GitHub Environment variable `NOTIFY_TEST_TEMPLATE_ID` to the Sandbox template ID.
4. Deploy a new ECS task so both values are injected into the application environment.

Run the command inside the deployed application container with an approved recipient:

```shell
python manage.py send_test_notification approved-recipient@example.gov.uk
```

The current Sandbox workflow passes the GitHub secret to the ECS task definition as an environment variable. GitHub
masks the value in workflow logs, but users who can read ECS task definitions may be able to view it. Restrict access
accordingly. Deploy a new task after rotating the GitHub secret. Migration to AWS Secrets Manager is planned separately.

This repository currently wires Notify into the Sandbox deployment workflow only. The application settings support
later environments, but their authoritative infrastructure or deployment configuration must inject separate Notify
keys and templates before notifications are enabled there. Never reuse the production key in another environment.

## Automated tests

Normal unit and command tests mock `NotificationsAPIClient` and make no network requests. Feature-test containers
explicitly clear all Notify configuration so a developer's local credentials cannot be inherited.

An opt-in test confirms communication with the real Notify API using a test key. Add these values only to the ignored
`webcaf/.env` file:

```dotenv
NOTIFY_INTEGRATION_API_KEY=<test-api-key>
NOTIFY_INTEGRATION_TEMPLATE_ID=<test-template-id>
```

Run the integration test on the host:

```shell
RUN_GOVUK_NOTIFY_INTEGRATION_TESTS=true uv run pytest tests/test_notify_integration.py
```

Or run it in Docker Compose:

```shell
docker compose run --rm -e RUN_GOVUK_NOTIFY_INTEGRATION_TESTS=true \
  --entrypoint pytest web tests/test_notify_integration.py
```

The integration test uses Notify's `simulate-delivered@notifications.service.gov.uk` smoke-test recipient and must only
be run with a Notify test key. Notify validates the request but does not deliver or persist an email for this recipient.

## Error handling

WebCAF retries failures that are known to happen while establishing a connection, before the request reaches Notify.
It also retries Notify's documented `RateLimitError`, HTTP 500 responses, and explicit HTTP 503 responses with bounded
exponential backoff. It does not retry read timeouts, response-less ambiguous transport errors, HTTP 502 or 504,
daily-limit `TooManyRequestsError` responses, or invalid responses after a successful request.

Recipient addresses are de-duplicated without regard to case. If one recipient is rejected, WebCAF continues with the
remaining recipients and then raises one aggregate error. A successful HTTP response with unreadable content is
reported as accepted with an invalid response, not as a failed status 201 request. Empty responses and responses without
a non-empty string notification ID are handled the same way.

Application logs use safe categories based on Notify's documented stable error types, such as authentication,
validation, bad request, rate limit, and daily limit. Logs do not include API credentials, personalisation data, raw
email addresses, raw Notify messages, or synthetic HTTP statuses for failures where no response was received.

Common failures are:

- Status 400: check the recipient, template ID, and required template personalisation.
- Status 401 or 403: check that the API key is valid and belongs to the expected Notify service.
- `RateLimitError` with status 429: WebCAF retries with bounded backoff.
- `TooManyRequestsError` with status 429: the daily allowance is exhausted and resets at midnight UTC.
- Status 500 or explicit status 503: WebCAF retries with bounded backoff; check Notify's service status if retries fail.
- Transport failure without a response: delivery may be ambiguous and WebCAF does not report a synthetic status 503.

The management command exits unsuccessfully when Notify rejects the request or remains unavailable.
