# GOV.UK Notify

WebCAF uses the supported
[`notifications-python-client`](https://docs.notifications.service.gov.uk/python.html) to send email notifications.
The shared integration is in `webcaf/webcaf/notification.py`.

## API key policy

Use a separate API key appropriate to each environment:

- Local development uses a Notify test key. Requests reach the real Notify API but no email is delivered.
- Sandbox uses a team and guest list key. Email is delivered only to recipients approved in the Notify service.
- Production uses a live key. A production key must not be available in local development or Sandbox.

API keys and template IDs must belong to the same Notify service. Do not commit API keys, pass them as image build
arguments, or store them in ordinary ECS environment variables.

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
2. Store the team API key in AWS Secrets Manager for Sandbox.
3. Set the Sandbox GitHub Environment variable `NOTIFY_API_KEY_SECRET_ARN` to that secret's ARN.
4. Set the Sandbox GitHub Environment variable `NOTIFY_TEST_TEMPLATE_ID` to the Sandbox template ID.
5. Grant the ECS task execution role `secretsmanager:GetSecretValue` for the secret and `kms:Decrypt` if required.
6. Deploy a new ECS task so the secret is injected as `NOTIFY_API_KEY`.

Run the command inside the deployed application container with an approved recipient:

```shell
python manage.py send_test_notification approved-recipient@example.gov.uk
```

The deployment workflow removes any same-named plaintext environment entry before adding the secret reference. Rotate
the key after migrating an environment that previously stored it as plaintext. ECS injects a secret when a task starts,
so deploy new tasks after rotating the API key.

This repository currently wires Notify into the Sandbox deployment workflow only. The application settings support
later environments, but their authoritative infrastructure or deployment configuration must inject separate Notify
keys and templates before notifications are enabled there. Use separate Secrets Manager secrets and least-privilege ECS
task execution roles, and never reuse the production key in another environment.

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

The integration test uses a synthetic recipient and must only be run with a Notify test key.

## Error handling

WebCAF retries explicit rate-limit and server-error responses with bounded backoff. It does not retry connection
timeouts or invalid responses because Notify may already have accepted the request. Authentication, recipient,
template, and personalisation errors fail immediately. Application logs include the status but do not include API
credentials, personalisation data, email addresses, or raw Notify responses.

Common failures are:

- Status 400: check the recipient, template ID, and required template personalisation.
- Status 401 or 403: check that the API key is valid and belongs to the expected Notify service.
- Status 429: wait for the Notify rate limit to reset.
- Status 500 or 503: check the Notify service status and retry later.

The management command exits unsuccessfully when Notify rejects the request or remains unavailable.
