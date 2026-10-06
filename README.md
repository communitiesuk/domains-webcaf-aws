# WebCAF

[![Integration tests](https://github.com/communitiesuk/domains-webcaf-aws/actions/workflows/pull-request.yml/badge.svg?branch=main)](https://github.com/communitiesuk/domains-webcaf-aws/actions/workflows/pull-request.yml)
[![Feature tests](https://github.com/communitiesuk/domains-webcaf-aws/actions/workflows/feature-tests.yml/badge.svg?branch=main)](https://github.com/communitiesuk/domains-webcaf-aws/actions/workflows/feature-tests.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)

WebCAF enables users to assess their organisations against the NCSC Cyber Assessment Framework (CAF). Assessments
retain the CAF version with which they were created, allowing multiple framework versions to remain in use.

## Prerequisites

The containerised development workflow requires:

- Docker with Docker Compose
- Make

Running Django or repository tooling directly on the host also requires:

- Python 3.12, as pinned in `.python-version`
- [Poetry](https://python-poetry.org/docs/#installation)
- The [native libraries required by WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation),
  including Pango (`brew install pango` on macOS)

Use a recent Docker Compose release. The feature-test Compose files use the `!override` and `!reset` YAML tags.

## Run the application in Docker

Start the default development stack from the repository root:

```shell
docker compose up
```

Open WebCAF at [http://localhost:8010](http://localhost:8010). The default stack contains:

- `postgres`: PostgreSQL 18, exposed to the host on port `54321`
- `redis`: Valkey 9.0, exposed to the host on port `6379`
- `one-login-simulator`: the local GOV.UK One Login provider, exposed on port `3000`
- `init`: a one-shot service that runs `makemigrations`, `collectstatic`, and `migrate`
- `web`: WebCAF served by Gunicorn with source reloading enabled

The repository is bind-mounted into `init` and `web`. If model changes do not have a migration, startup can create a
migration file in the working tree; review and commit it where appropriate.

Useful development commands are:

```shell
make up-devserver  # use Django's development server instead of Gunicorn
make build         # rebuild the Compose images
make shell         # open a shell in the running web container
docker compose down
```

`make shell` requires the `web` service to be running. Database data remains in the Compose volume after
`docker compose down`; use `docker compose down --volumes` only when you intentionally want to delete local data.

## Run Django on the host

Create the local environment file and install the Python dependencies before starting the One Login simulator:

```shell
cp webcaf/.env.example webcaf/.env
poetry install
poetry run pre-commit install
make up_one_login_simulator
poetry run python manage.py migrate
poetry run python manage.py runserver 0.0.0.0:8010
```

The host server must use port `8010` because that port is registered for the local authentication callbacks.
`make up_one_login_simulator` starts PostgreSQL, Valkey, and the simulator, and configures the Alice preset. It requires
the Poetry environment to be installed because the preset is applied by a Python helper.

## Authentication

GOV.UK One Login is the default identity provider for local development and BDD tests. DEX is retained for explicit
generic OIDC testing. `SSO_MODE` supports these modes:

- `one-login`: GOV.UK One Login or the local simulator; this is the local default
- `dex`: DEX reached by its Compose network name; used by the fully containerised DEX workflow
- `local` or `localhost`: DEX reached at `localhost:5556`; used when Django runs on the host
- `external`: a generic provider configured through the `OIDC_*` environment variables
- `none`: build-time tasks that do not need to authenticate a user

Start the complete containerised application with DEX instead of One Login using:

```shell
make up_dex
```

To run Django on the host with DEX, set `SSO_MODE=local` in `webcaf/.env`, then start only its dependencies so that the
containerised `web` service does not take port `8010`:

```shell
docker compose --profile dex up -d --wait postgres redis oauth
poetry run python manage.py migrate
poetry run python manage.py runserver 0.0.0.0:8010
```

The local DEX users are defined in `oauth-stub/config.yaml`; their development-only password is `password`.

See [GOV.UK One Login](docs/GOV_UK_ONE_LOGIN.md) for simulator presets, user mapping, authentication policy, the
integration-environment workflow, and the application configuration contract.

## Local data and first sign-in

The following commands are for local development only. Run them from the host when using the host workflow:

```shell
poetry run python manage.py add_organisations
poetry run python manage.py add_local_seed_data
```

For a running containerised application, run the equivalent commands in `web`:

```shell
docker compose exec web python manage.py add_organisations
docker compose exec web python manage.py add_local_seed_data
```

`add_organisations` loads `webcaf/seed/webcaf-orgs.csv` only when the organisation table is empty.
`add_local_seed_data` requires at least one organisation and creates development systems and the local Django
superuser `admin` with password `password`. If the configured Alice and admin DEX users already exist, it also creates
profiles for them against the first organisation.

A fresh database has no allowed email domains. To use the One Login simulator for the first time:

1. Run both local data commands above.
2. Sign in to `/admin/` with the local `admin` account and add `example.gov.uk` to **Allowed email domains**.
3. Sign in through One Login once so that WebCAF creates the Alice Django user.
4. Assign that user an organisation and role in Django admin.

Authentication creates or matches a Django user but does not create its `UserProfile`, organisation, or role.

## CAF versions and assessment periods

WebCAF supports CAF 3.2 (`caf32`) and CAF 4.0 (`caf40`). Every assessment stores its framework version and continues to
use that version throughout completion and review.

New assessments use the `default_framework` from the earliest database `Configuration` whose
`assessment_period_end` has not passed. Fresh databases currently seed:

- `25/26`, ending 31 August 2026 at 11:59pm, using CAF 3.2
- `26/27`, ending 31 August 2027 at 11:59pm, using CAF 4.0

Administrators must create the next configuration before the current period ends. Changing a period's default affects
only assessments created afterwards. See [Managing assessment period cutoff dates](docs/MANAGING_CUTOFF_DATES.md) and
the [framework definition guide](frameworks/README.md).

## Tests and checks

Run all pre-commit checks before the test suites:

```shell
poetry run pre-commit run --all-files
```

Run the containerised pytest suite with:

```shell
make test
```

The report is written to `reports/pytest-report.html`. This target uses the normal Compose project and runs
`docker compose down` after a successful test run, so stop the development stack before invoking it. If pytest fails,
the target can leave supporting services running; use `docker compose down` to clean them up.

After starting PostgreSQL, Valkey, and the One Login simulator, an individual test can also run on the host:

```shell
poetry run pytest tests/<test-file>.py::<test-name>
```

Run the isolated browser suites with:

```shell
make behave      # application, Django admin, and GOV.UK One Login scenarios
make behave_dex  # focused DEX login and logout scenarios
```

The Behave targets use disposable Compose projects and clean up after success or failure. See
[BDD feature tests](features/README.md) for focused runs, reports, failure artifacts, and cleanup commands.

## Development conventions

### Logging user information

Never log raw email addresses or other user identifiers. Use `mask_email` when an email address is needed for
diagnostics:

```python
from webcaf.webcaf.utils import mask_email

logger.info("Sent verification to %s", mask_email(user.email))
```

### Review permissions

Review views use `BaseReviewMixin` in `webcaf/webcaf/views/assessor/util.py`. The allowed roles are `cyber_advisor`,
`organisation_lead`, `reviewer`, and `assessor`; `organisation_lead` is read-only by default. The mixin sets `can_edit`
on the returned object for templates and forms.

### Targeted Improvement Plan permissions

Targeted Improvement Plan views use `BaseTipMixin` in `webcaf/webcaf/tip/util.py`. The allowed roles are
`cyber_advisor`, `organisation_lead`, and `organisation_user`; `cyber_advisor` is read-only by default. Submitted plans
are read-only for everyone. The model restricts approval and rejection to Django staff users. In Django admin, either
the `can_approve_tip` or `can_reject_tip` permission currently grants access to both actions.

## Environment operations

Deployment procedures, AWS account bootstrapping, environment secrets, releases, and rollback instructions belong in
the team's environment operations runbook and are intentionally not duplicated in this developer guide. Add a direct
link here when the authoritative runbook location is confirmed.
