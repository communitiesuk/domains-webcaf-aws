# Microsoft Entra sign-in for Django Admin

CS-566 uses a tenant-specific Microsoft Entra web app registration and MSAL for
Django Admin authentication. WebCAF manages the staff account and its Django
permissions. The main service uses its existing SSO configuration.

## Configure AWS Dev

Register this Web redirect URI in the MHCLG tenant.

`https://dev.assess-council-cyber-resilience.service.gov.uk/admin/entra/callback/`

Apply the database migration before enabling Entra. Set these values for the
environment and inject the secret from AWS Secrets Manager through the task
definition.

| Setting | Value |
| --- | --- |
| `ENTRA_ADMIN_ENABLED` | `true` once the registration and staff links are ready |
| `ENTRA_ADMIN_TENANT_ID` | MHCLG tenant UUID |
| `ENTRA_ADMIN_CLIENT_ID` | Application UUID for the environment |
| `ENTRA_ADMIN_CLIENT_SECRET` | Secret injected into the running container |
| `ENTRA_ADMIN_REDIRECT_URI` | Exact registered Web redirect URI |

Use separate credentials and configuration per environment. Local automated tests
mock MSAL. If real local sign-in is needed, use an approved local-development
registration. With the feature disabled, Django Admin uses its existing sign-in.
With it enabled, Django Admin requires an Entra-authenticated session. Django
Admin sessions expire after 15 minutes of inactivity. Admin logout and idle
expiry end the WebCAF session. The Microsoft browser session may remain.
WebCAF verifies the ID token signature using the configured tenant's signing
keys and checks the issuer, audience, expiry and tenant before linking the user.
MSAL handles the authorisation-code exchange and checks the flow state and nonce.

## Link a staff account

Before enabling the feature, verify the MHCLG directory user's object ID and the
intended existing Django user with the account owner or authorised administrator.
Confirm the Django user is active, has staff access and has the required Django
groups and permissions. An authorised operator can create the link in a Django
shell using the verified IDs.

```python
from django.conf import settings
from webcaf.webcaf.models import EntraAdminIdentity

EntraAdminIdentity.objects.create(
    user_id=django_user_id,
    tenant_id=settings.ENTRA_ADMIN_TENANT_ID,
    object_id=verified_entra_object_id,
)
```

Remove the link or revoke staff access to block further admin requests. Disabling
the Entra account blocks new sign-ins, but an existing WebCAF session also needs
its local access revoked if it must be blocked immediately. Accounts
are provisioned explicitly and email addresses are not used for identity matching.

## Verify the deployment

- Sign in with a linked MHCLG staff account and check that its Django permissions
  govern which actions it can take.
- Confirm an unlinked MHCLG account cannot sign in to Admin and that the main
  service still signs in through Internal Access.
- Confirm the agreed network controls allow intended staff to reach Admin and
  its callback. Check proxy and monitoring configuration for callback query
  strings. Gunicorn logs the request path without the query string.
