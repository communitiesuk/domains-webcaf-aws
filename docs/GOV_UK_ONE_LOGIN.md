# GOV.UK One Login

WebCAF uses `govuk-onelogin-django` for GOV.UK One Login and retains `mozilla-django-oidc` for DEX and generic OIDC providers. Application authorisation remains in WebCAF's `UserProfile`, organisation and role models.

## Local simulator

The official [GOV.UK One Login simulator](https://github.com/govuk-one-login/simulator) can exercise WebCAF's One Login protocol flow without a registered integration-environment client. It is opt-in and does not replace the default DEX Docker Compose workflow.

Copy `webcaf/.env.example` to `webcaf/.env` and enable the documented simulator values:

```text
SSO_MODE=one-login
GOV_UK_ONE_LOGIN_CLIENT_ID=webcaf-local
GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH=tests/fixtures/one-login-simulator-private-key.pem
GOV_UK_ONE_LOGIN_OPENID_CONFIG_URL=http://localhost:3000/.well-known/openid-configuration
GOV_UK_ONE_LOGIN_SCOPE="openid email"
GOV_UK_ONE_LOGIN_ENVIRONMENT=integration
```

The committed private key is the simulator project's published default test key. It is public, is not a deployment credential, and must never be used outside the local simulator.

Start PostgreSQL and the simulator, migrate the database, then run Django on the host:

```shell
make up_one_login_simulator
poetry run python manage.py migrate
poetry run python manage.py runserver 0.0.0.0:8010
```

Open `http://localhost:8010` and select **Sign in**. The simulator runs in interactive mode and displays a response form pre-populated with a production-shaped subject, `alice@example.gov.uk`, and a verified email. Select **Continue** to use those values, or edit them to exercise another response.

Named presets can update the values shown on the next sign-in:

```shell
make one_login_user PRESET=alice
make one_login_user PRESET=organisation_user
make one_login_user PRESET=cyber_advisor
make one_login_user PRESET=assessor
make one_login_user PRESET=unverified
```

The available presets are `alice`, `admin`, `organisation_user`, `organisation_lead`, `cyber_advisor`, `assessor`, and `unverified`. Restarting the simulator restores the Alice defaults. Set `ONE_LOGIN_SIMULATOR_INTERACTIVE_MODE=false` before starting the service if the configured response should be returned without displaying the form.

For a new user, first add the exact email domain to **Allowed email domains** in Django admin. One Login creates the Django user but does not create a `UserProfile`, so an administrator must also assign the organisation and role before the account can use protected WebCAF functionality. Existing non-staff users are matched by email and retain their profiles.

An opt-in smoke test performs a real authorization, token, JWKS and UserInfo exchange against the running simulator. It uses an isolated Django test database and restores the simulator's configured redirect URLs:

```shell
RUN_ONE_LOGIN_SIMULATOR_TESTS=true poetry run python manage.py test tests.test_one_login_simulator
```

The test is skipped during normal local test runs. Run it only after starting the simulator and configuring `webcaf/.env` with the simulator values above. It supports both interactive and non-interactive simulator modes and runs automatically in pull-request CI.

Stop the simulator with:

```shell
docker compose --profile one-login-simulator stop one-login-simulator
```

This workflow intentionally runs Django on the host. The simulator publishes absolute endpoint URLs based on `http://localhost:3000`; those URLs cannot simultaneously refer to the host browser and a separate WebCAF container. DEX remains the supported provider for the default fully containerised development stack.

See the [simulator evaluation](GOV_UK_ONE_LOGIN_SIMULATOR_EVALUATION.md) for its capabilities, limitations and recommended testing scope.

## Simulator feature tests

Run the focused One Login browser scenarios with:

```shell
make behave_one_login
```

The dedicated Compose overlay uses Docker-resolvable URLs for the Playwright browser, WebCAF and simulator. It runs the simulator non-interactively, configures a deterministic identity before each login, and covers a profiled user, a user without a profile, an unverified email, and front-channel logout. The suite runs serially because `/config` controls global simulator state. The existing broad feature suite remains DEX-backed.

## One Login integration environment

The registered local URLs are:

```text
Redirect URI:             http://localhost:8010/one-login/callback/
Post-logout redirect URI: http://localhost:8010/
```

Create `webcaf/.env` from `webcaf/.env.example`, set `SSO_MODE=one-login`, and replace the simulator client ID, private key path and OpenID configuration URL with the registered integration-environment values. Keep the private key in the ignored repository-root `.secrets/` directory and set `GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH` to its absolute path or a path relative to the repository root.

Install dependencies, then start the local PostgreSQL database and WebCAF with:

```shell
poetry install
docker compose up -d postgres
poetry run python manage.py migrate
poetry run python manage.py runserver 0.0.0.0:8010
```

Open `http://localhost:8010`. The normal DEX Docker Compose workflow uses the same host port, so stop that stack before running the One Login integration server.

## User mapping and authorisation

One Login must return `sub`, `email` and `email_verified=true`. WebCAF matches one non-staff Django user by email or creates a new Django user with an unusable password.

New users can be created only when their email domain has an exact matching `Allowed email domain` entry in Django admin. The allowlist applies to GOV.UK One Login, DEX and generic OIDC, and an empty allowlist denies all new automatic users. It does not affect existing users, Django admin, management commands or WebCAF's user-management form. Add each permitted subdomain separately. Rejected users see a domain-specific error page and the attempt is logged with a masked email address.

Authentication does not create a `UserProfile`, assign an organisation or assign a role. A newly created user reaches the existing no-profile page with a 403 response until a WebCAF administrator creates the appropriate profile. Existing profiles remain attached when a pre-provisioned user is matched.

Email is currently the account-linking identifier. If a user's One Login email changes, an administrator may need to reconcile the WebCAF account. Durable provider subject mapping is deferred until multiple external authentication providers are introduced.

## Authentication policy

WebCAF requests:

```text
Scopes:              openid email
Authentication:      Cl.Cm
Identity confidence: P0
```

WebCAF's email OTP is disabled in `one-login` mode because `Cl.Cm` provides medium-level authentication. Back-channel logout is disabled. User-initiated logout clears the WebCAF session and uses One Login front-channel logout.

Provider denials, incomplete callbacks, token exchange failures and identities that cannot be mapped are redirected to a generic authentication error page. State and token validation failures remain bad requests rather than being hidden.

## Runtime configuration

Set these non-secret variables in `.env` or the deployment environment:

```text
SSO_MODE=one-login
GOV_UK_ONE_LOGIN_CLIENT_ID
GOV_UK_ONE_LOGIN_OPENID_CONFIG_URL
GOV_UK_ONE_LOGIN_SCOPE
GOV_UK_ONE_LOGIN_ENVIRONMENT
```

Configure exactly one private-key source:

```text
GOV_UK_ONE_LOGIN_CLIENT_SECRET
GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH
```

`GOV_UK_ONE_LOGIN_CLIENT_SECRET` is the package's name for the base64-encoded RSA private key. Use this form with AWS Secrets Manager or an equivalent deployment secret store. `GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH` is intended for local development and must point to the PEM file.

Continue to provide WebCAF's existing `SECRET_KEY`, database, host, domain, Notify and other environment-specific settings. No credential or private key should be included in an image, ordinary environment configuration or source control.

## ECS deployment

Store the base64-encoded PEM private key in AWS Secrets Manager and inject it into the ECS task as `GOV_UK_ONE_LOGIN_CLIENT_SECRET`. Do not set `GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH` in ECS.

The task execution role must allow `secretsmanager:GetSecretValue` for the secret and, when the secret uses a customer-managed KMS key, `kms:Decrypt` for that key. Do not put the value in the task definition's ordinary `environment` section, container image, logs or source control.

If the secret is a JSON object, configure the task-definition `valueFrom` ARN to select the key holding the base64-encoded PEM. Register the matching public key with GOV.UK One Login. After rotating the secret or registered key, start new ECS tasks so the updated secret is injected.

## AWS sandbox registration

When the sandbox hostname is available, register these exact URLs in the One Login console:

```text
Redirect URI:             https://<sandbox-host>/one-login/callback/
Post-logout redirect URI: https://<sandbox-host>/
```

The application builds callback URLs from the incoming request and trusts `X-Forwarded-Proto` outside debug mode. The load balancer must preserve the public host and set `X-Forwarded-Proto=https`.
