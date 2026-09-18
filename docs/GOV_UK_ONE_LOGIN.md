# GOV.UK One Login

WebCAF uses `govuk-onelogin-django` for GOV.UK One Login and retains `mozilla-django-oidc` for DEX and generic OIDC providers. Application authorisation remains in WebCAF's `UserProfile`, organisation and role models.

## Local integration environment

The registered local URLs are:

```text
Redirect URI:             http://localhost:8010/one-login/callback/
Post-logout redirect URI: http://localhost:8010/
```

Create `webcaf/.env` from `webcaf/.env.example`, set `SSO_MODE=one-login`, and uncomment the GOV.UK One Login settings. Keep the private key in the ignored repository-root `.secrets/` directory and set `GOV_UK_ONE_LOGIN_PRIVATE_KEY_PATH` to its absolute path or a path relative to the repository root.

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
