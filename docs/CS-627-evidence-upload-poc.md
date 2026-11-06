# CS-627 evidence upload proof of concept

This local-only proof of concept uploads benign PDFs directly from a browser to the dedicated Sandbox intake bucket, polls GuardDuty scan tags, validates the exact clean object version and conditionally writes approved bytes to the accepted bucket. It is disabled by default.

The provisional `EvidencePocUpload` model is not the final CS-626 evidence schema. Intake originals are retained because neither POC role has deletion permission. Retention, disposal, multiple Contributing Outcome associations and production worker delivery remain separate work.

## Prerequisites

- Python 3.14 and repository-pinned `uv` 0.12.23.
- Local PostgreSQL, cache and authentication simulator services running.
- AWS CLI v2 solely to establish a temporary IAM Identity Center session. Credentials must not be copied into the repository or Docker environment.
- A staff user with an active `organisation_lead` or `organisation_user` profile and a draft assessment for that profile's council.

Use a host Django process so boto3 can refresh the user's normal AWS shared-config credentials without mounting the host credential cache into a container. Stop only the Compose web service if it currently owns port 8010; do not remove volumes:

```shell
docker compose --profile one-login -f docker-compose.yml -f docker-compose.one-login.yml stop web
```

If the local Compose files have changed since the stack was created and Compose cannot parse the project, identify the
project's web container with `docker ps` and stop that one container with `docker stop <web-container-name>`. Do not stop
PostgreSQL, Redis or the simulator, and do not remove volumes.

## Minimal AWS shared configuration

Use the organisation's existing Identity Center start URL, Identity Center region, account and permission-set details.
Obtain every value from the approved environment handover rather than guessing or committing live identifiers.

```ini
# ~/.aws/config -- values in angle brackets must be supplied locally
[sso-session <sandbox-sso-session-name>]
sso_start_url = <organisation Identity Center start URL>
sso_region = <Identity Center region>
sso_registration_scopes = sso:account:access

[profile <sandbox-developer-profile-name>]
sso_session = <sandbox-sso-session-name>
sso_account_id = <sandbox-account-id>
sso_role_name = <developer-permission-set-name>
region = <aws-region>

[profile <evidence-application-profile-name>]
source_profile = <sandbox-developer-profile-name>
role_arn = <evidence-application-role-arn>
role_session_name = <evidence-application-session-name>
region = <aws-region>

[profile <evidence-processor-profile-name>]
source_profile = <sandbox-developer-profile-name>
role_arn = <evidence-processor-role-arn>
role_session_name = <evidence-processor-session-name>
region = <aws-region>
```

Authenticate and explicitly verify both identities before enabling the feature:

```shell
aws sso login --profile "$SANDBOX_DEVELOPER_PROFILE"
AWS_PROFILE="$EVIDENCE_APPLICATION_PROFILE" aws sts get-caller-identity
AWS_PROFILE="$EVIDENCE_PROCESSOR_PROFILE" aws sts get-caller-identity
```

The returned account must match the approved Sandbox account. The ARNs must identify the configured application and
processor roles respectively. WebCAF performs the same role-name and account check before S3 use. It fails closed and
does not retry with the developer profile.

## Local configuration and launch

Copy the CS-627 variables from `webcaf/.env.example` into the ignored `webcaf/.env`, then set:

```dotenv
EVIDENCE_POC_ENABLED=True
EVIDENCE_POC_APP_AWS_PROFILE=<evidence-application-profile-name>
EVIDENCE_POC_PROCESSOR_AWS_PROFILE=<evidence-processor-profile-name>
```

The bucket names, KMS key, role ARNs, region and profile names have no live-resource defaults. Supply every value in the
ignored local `.env`; startup fails closed if any are missing while the feature is enabled. Obtain approved values through
the environment handover rather than committing them to `.env.example`. The feature also refuses to start when `DEBUG`
is false.

The evidence S3 client is pinned to virtual-host addressing on the configured regional endpoint. This must continue to
match the exact intake origin in the bucket CORS rule and the POC-only CSP directives. AWS SDK and HTTP-client debug logs
are suppressed because they can contain temporary signing material or presigned URLs.

Apply the provisional migration and start Django on the registered origin:

```shell
uv run python manage.py migrate
uv run python manage.py runserver 0.0.0.0:8010
```

Open `http://localhost:8010/admin/webcaf/assessment/evidence-poc/`. Use `localhost`, not `127.0.0.1`, because the bucket CORS rule permits only `http://localhost:8010`.

In another host shell, process pending records once:

```shell
uv run python manage.py process_evidence_poc --identity-only
uv run python manage.py process_evidence_poc
```

Run the command again while a scan is pending. `--retry-failures` explicitly retries records in the processing-failure state after fixing the cause.

## Expected journey

1. Select a draft assessment and a synthetic PDF.
2. With JavaScript, upload progress appears and the page polls durable status. Without JavaScript, WebCAF prepares a native S3 form and asks for the same file again.
3. Do not navigate away while bytes are transferring. Full navigation, refresh or closing the tab can interrupt the browser request. Once S3 has received the file, processing continues independently and the user can return later.
4. The processor binds the oldest observed exact-key version once, requires `NO_THREATS_FOUND` on that version, checks size, S3 metadata, PDF signature, parser structure and encryption, computes SHA-256 and performs an `If-None-Match: *` accepted write with an S3-verifiable checksum.
5. Download is offered only after the database association is re-authorised. The Django endpoint signs a 60-second GET for the exact accepted version with attachment and private/no-store response overrides.

Do not use EICAR or another malware-test fixture until Sandbox Security has explicitly approved the monitored test.

## Verification

Mocked automated coverage is in `tests/test_evidence_poc.py`:

```shell
uv run pytest -q tests/test_evidence_poc.py
uv run pre-commit run --all
```

These tests simulate AWS. Record real role, S3 policy, GuardDuty, browser and digest observations separately; do not describe mocked outcomes as live AWS evidence.

### Observed Sandbox verification — 6 November 2026

- Both configured profiles resolved to their dedicated application and processor assumed roles in the approved Sandbox
  account; the GuardDuty plan was active with result tagging enabled.
- Chrome completed a real enhanced cross-origin upload and status polling. Chrome with JavaScript disabled completed the
  native S3 form and authenticated return journey. A throttled transfer displayed the browser navigation warning.
- Three benign 476-byte synthetic PDFs received `NO_THREATS_FOUND` on their exact intake versions, passed bounded PDF
  validation and were promoted to separate accepted versions. A repeated processor run did not re-promote them.
- The accepted object's KMS key, evidence/source metadata and S3 SHA-256 checksum matched the durable database record.
  An authorised browser download matched the processor SHA-256.
- S3 rejected zero-byte and 25-MiB-plus-one-byte presigned POSTs as `EntityTooSmall` and `EntityTooLarge`, and rejected a
  changed encryption field. The exact maximum is additionally covered by the mocked policy test; a 25-MiB live object
  was not uploaded solely to prove the accepted boundary.
- Live IAM and bucket-policy checks denied application-role intake reads and accepted writes, processor reads before a
  scan tag existed, processor accepted writes without `If-None-Match`, and intake writes without required SSE-KMS.
- Anonymous accepted-object access, a modified signed download and an expired short-lived signed download were denied.
  A second council profile could neither list nor address the first council's evidence.

No EICAR or other malware-test fixture was used. The browser sessions were created locally for the two seeded ordinary
staff users; the interactive One Login simulator ceremony was not part of this storage-flow check. Incomplete attempts
from deliberately blocked/interrupted browser tests and dedicated Sandbox test objects remain because the POC roles have
no deletion permission.

## Residual POC risks and limitations

- `pypdf` runs shallow structural checks inside the processor process after GuardDuty reports clean. A parser vulnerability would inherit that process's database, intake-read, accepted-write and network access. Security must decide whether production validation needs stronger isolation.
- A clean GuardDuty result reduces known-malware risk but does not prove a document is harmless.
- Presigned forms and GETs are bearer capabilities until expiry. They must not be logged or stored.
- An application task compromise can sign reads across the environment's accepted bucket; Django remains the council-level authorisation boundary.
- Intake originals and orphaned accepted writes after an authorisation race need a separately privileged, audited cleanup design.
