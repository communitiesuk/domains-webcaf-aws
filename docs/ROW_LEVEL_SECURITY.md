# PostgreSQL Row Level Security for Organisation Data Isolation

## Document purpose

This document records the investigation and proof of concept completed for CS-445. It is intended to give decision makers enough context to decide whether WebCAF should adopt PostgreSQL Row Level Security (RLS), and to give developers enough detail to understand and review the implementation on this branch.

The proof of concept changes local development and automated-test infrastructure only. No Sandbox, staging, or production-like database has been changed.

## Executive summary

PostgreSQL RLS is feasible for WebCAF and provides useful defence in depth against Django queries that accidentally omit organisation filtering. The proof of concept demonstrates real database enforcement for `System` and `HistoricalSystem` when queries run through a restricted, non-owner database role.

RLS should not replace Django organisation filtering or role-based access control (RBAC):

- Django remains responsible for selecting and validating the active membership, presenting the correct HTTP response, and deciding which actions a role may perform.
- PostgreSQL provides a second organisation boundary underneath Django and rejects rows outside the active organisation.

The recommendation is to adopt RLS for core organisation-owned data in stages, subject to the production prerequisites and follow-on work described below. A blanket rollout to every table is not recommended.

The PoC is deliberately incomplete as a tenant-isolation control. Only `webcaf_system` and `webcaf_historicalsystem` are protected. Assessment, Review, Tip, Organisation, UserProfile/Membership, and their remaining history tables are still directly accessible to a database role with table privileges.

## Decision summary

The PoC supports the following decisions:

1. Use transaction-local PostgreSQL settings to carry trusted user and organisation context.
2. Run normal application traffic through a restricted database role that does not own tables and cannot bypass RLS.
3. Keep migrations and approved cross-organisation operations on separate, explicit execution paths.
4. Keep application-level organisation filters and RBAC after RLS is introduced.
5. Extend RLS only after resolving historical ownership, operational access, and request-transaction concerns.

RLS is valuable primarily as protection against missing or incorrect query filters. It is not a security boundary against a fully compromised application process or arbitrary SQL execution, because the application database role can set its own custom PostgreSQL settings.

## Current WebCAF organisation model

WebCAF currently uses `UserProfile` as its membership record. A profile links one Django user to one organisation and stores that membership's role. A user may have profiles in multiple organisations.

The core ownership chain is:

```text
Organisation
|-- UserProfile
`-- System
    `-- Assessment
        `-- Review
            `-- Tip
```

Relevant model definitions are in `webcaf/webcaf/models.py`:

- `Organisation`
- `System`
- `Assessment`
- `UserProfile`
- `Review`
- `Tip`

There is no separate Membership model in the current codebase. If one is introduced, it should become the authoritative source for active organisation context.

### Directly organisation-scoped data

The following models already have a direct organisation identifier and are the most straightforward RLS candidates:

- `Organisation`, using its own ID
- `System`, using `organisation_id`
- `UserProfile`, using nullable `organisation_id`
- `HistoricalOrganisation`, using the copied object ID
- `HistoricalSystem`, using copied `organisation_id`
- `HistoricalUserProfile`, using copied `organisation_id`

### Indirectly organisation-scoped data

The following models derive organisation ownership through parent records:

- `Assessment` through `system.organisation_id`
- `Review` through `assessment.system.organisation_id`
- `Tip` through `review.assessment.system.organisation_id`

Policies can enforce these relationships with joins or `EXISTS` expressions. The additional policy depth must be evaluated using representative data and query plans before broad rollout.

Adding a duplicated organisation column to every live child table is not automatically required. If such columns are introduced for policy simplicity or performance, the database must enforce consistency with the parent relationship.

### Historical ownership problem

Historical Assessment, Review, and Tip records infer ownership through nullable, unconstrained links to live parent records. After a live parent is deleted, the organisation may no longer be recoverable.

Complete historical isolation will probably require an immutable, indexed `organisation_id` on each organisation-owned historical table, including a controlled backfill and an explicit decision for orphaned rows.

### Shared and global data

The following should generally remain outside organisation RLS:

- `auth_user`, because one user can belong to multiple organisations
- Django authentication, permission, and content-type tables
- WebCAF configuration and settings
- Allowed email domains
- OTP and Axes authentication data
- Framework metadata

`django_admin_log` may contain organisation-related representations but has no reliable organisation key. It cannot be accurately tenant-filtered without a schema change.

Sessions are stored in Redis and exports are written to S3. PostgreSQL RLS cannot protect data after it leaves PostgreSQL.

## PoC architecture

### Database roles

The PoC introduces three application roles in `docker/postgres/init-roles.sh`:

- `webcaf_owner`: non-login role that owns the schema and migrated objects.
- `webcaf_migrator`: restricted login that may explicitly assume `webcaf_owner`.
- `webcaf_app`: restricted runtime login with `NOSUPERUSER`, `NOBYPASSRLS`, no ownership, and only required DML and sequence privileges.

The PostgreSQL container's `webcaf` superuser remains a local bootstrap and test-database-lifecycle identity. It is not used by the running local web application.

`docker-compose.yml` configures:

- The `web` service to connect as `webcaf_app`.
- The `init` service to connect as `webcaf_migrator` and use Django's PostgreSQL `assume_role` option to become `webcaf_owner`.
- PostgreSQL 18 to create the roles when a fresh local volume is initialised.

Role creation remains outside Django migrations because PostgreSQL roles are cluster-level objects while Django migrations are database-level and also run in temporary test databases.

### Trusted active profile

`SessionUtil.resolve_current_user_profile()` centralises active profile selection. It:

- Loads only profiles belonging to the authenticated user.
- Validates the session profile ID and last-organisation cookie against those profiles.
- Falls back to the user's first profile where current behaviour requires it.
- Handles stale, foreign, invalid, and missing profile IDs safely.
- Stores the validated profile on `request.current_profile`.

This closes the previous gap where `SessionUtil.get_current_user_profile()` loaded a profile by session ID without binding it to `request.user`.

`AccountView` and profile switching now use the central result rather than independently trusting session state.

### Request database context

`OrganisationContextMiddleware` is registered immediately after Django's `AuthenticationMiddleware` in `webcaf/settings.py`.

It wraps downstream request processing in `transaction.atomic()` and uses parameterised calls to set transaction-local PostgreSQL values:

```sql
SELECT set_config('webcaf.user_id', %s, true);
SELECT set_config('webcaf.organisation_id', %s, true);
SELECT set_config('webcaf.access_scope', %s, true);
```

The third argument gives `SET LOCAL` semantics. Values are cleared automatically when the transaction commits or rolls back, including when the physical database connection is later reused.

Supported request scopes are:

- `tenant`: authenticated request with a validated organisation profile.
- `admin`: active staff request under `/admin/`.
- `public`: anonymous, authentication, or no-profile request.

Missing organisation context does not grant global access. Protected tables fail closed.

Profile switching updates the session during the current request. The redirected request establishes the new database context, avoiding a mid-request tenant-context change.

### RLS policies

Migration `webcaf/webcaf/migrations/0037_system_rls_poc.py` adds reversible policies to:

- `public.webcaf_system`
- `public.webcaf_historicalsystem`

Each table has permissive policies for:

- Tenant access where `organisation_id` matches `webcaf.organisation_id` and scope is `tenant`.
- Admin access where scope is `admin` and `webcaf.user_id` identifies an active Django staff user.
- Owner maintenance access for `webcaf_owner`.

Both tables use:

```sql
ALTER TABLE table_name ENABLE ROW LEVEL SECURITY;
ALTER TABLE table_name FORCE ROW LEVEL SECURITY;
```

`USING` expressions restrict reads, updates, and deletes. `WITH CHECK` expressions reject inserts or updates that would place a row outside the permitted organisation.

`FORCE ROW LEVEL SECURITY` subjects the table owner to policies, but PostgreSQL superusers and roles with `BYPASSRLS` still bypass RLS. The runtime role must therefore never have either attribute.

## Django RBAC interaction

RLS enforces an organisation boundary, not WebCAF's complete authorization model.

RLS answers:

> Can this database request access rows belonging to this organisation?

Django answers:

> Is this member allowed to perform this action on those rows?

Roles such as organisation user, organisation lead, assessor, reviewer, and cyber advisor remain enforced in Django. Reproducing these action-level rules in PostgreSQL would duplicate application logic and make policy changes harder to reason about.

Application query filters must remain after RLS rollout because they:

- Express intended data access clearly.
- Preserve expected 403 and 404 responses.
- Avoid querying rows that PostgreSQL will discard.
- Provide protection in test or maintenance paths that intentionally run with broader database access.

## Administrative and system access

### Django admin

The PoC preserves current global Django admin behaviour for active staff under `/admin/`. The database policy verifies the application-supplied user ID against the global `auth_user` table before granting admin access.

Staff requests outside `/admin/` remain tenant-scoped when the staff user has an active organisation profile.

This is appropriate for the current PoC but must be confirmed as the intended long-term administrative model.

### Migrations

Migrations run as `webcaf_migrator` assuming `webcaf_owner`. The owner-maintenance policies permit all-row maintenance even with forced RLS.

Production rollout must provision roles and object ownership before applying RLS migrations. Enabling RLS before context-aware code and credentials are deployed would cause protected rows to disappear from normal application requests.

### Management commands

Cross-organisation commands such as exports, seed commands, and data repair must run through an explicit owner or system execution path.

Local developers should use:

```shell
make management-shell
```

The normal `make shell` uses `webcaf_app`. Without HTTP middleware context, RLS intentionally hides protected rows from that role. Running a global export through the restricted role could otherwise produce incomplete output.

### Backups

A backup performed through an RLS-restricted role may silently omit rows. Production backup procedures require controlled credentials and should fail rather than silently apply row filtering.

### Background work

No production Celery or equivalent background ORM process was found during the investigation. Future background tasks must receive an explicit organisation or system scope and establish their own transaction-local context. They must not rely on connection state inherited from a request.

## Connection reuse and transactions

The PoC uses transaction-local context rather than session-level `SET` values.

Session-level settings could survive commits, exceptions, persistent connections, or a future pooling configuration and expose one organisation's context to another request. Transaction-local values are removed automatically at the transaction boundary.

The automated tests deliberately reuse a connection for Organisation A, Organisation B, and missing-context transactions. Each transaction sees only the appropriate rows.

The main trade-off is that the middleware transaction currently spans the complete request. Existing synchronous notification calls and report generation may therefore keep transactions open while external work completes.

Before broader rollout, irreversible external calls should move to `transaction.on_commit()` or a background execution path. Otherwise an email could be sent successfully and a later application error could roll back the corresponding database update.

Future streaming responses must not perform ORM work after the middleware transaction has ended.

## Automated test evidence

`tests/test_rls.py` contains direct PostgreSQL and request-level coverage.

Before asserting isolation, the tests verify that:

- `current_user` is `webcaf_app`.
- The role is not a superuser.
- The role does not have `BYPASSRLS`.
- The role does not own the protected table.
- RLS and forced RLS are enabled.

The tests demonstrate that:

- Organisation A can select, create, update, and delete its own System rows.
- Organisation A cannot select Organisation B's System by a known primary key.
- Cross-organisation inserts and ownership changes raise a database error.
- Updates and deletes targeting another organisation affect no rows.
- HistoricalSystem rows are tenant-isolated.
- Missing context exposes no protected rows.
- Context is removed after commit, rollback, exception, and connection reuse.
- A stale or foreign session profile cannot establish another user's context.
- Profile switching applies the new context on the redirected request.
- Active staff retain global access under explicit admin scope.
- Staff requests outside admin remain tenant-scoped.
- Users without a profile cannot access protected System rows.

Representative query plans use the existing organisation foreign-key indexes on both protected tables. The admin staff check is evaluated as an init plan using the `auth_user` primary-key index. No additional index was required for this PoC.

Validation completed for the branch:

- Pre-commit checks passed.
- 744 unit/integration tests passed and 1 was skipped.
- 32 default One Login Behave scenarios passed and 2 were skipped.
- 2 focused DEX Behave scenarios passed.
- Migration reversal from 0037 to 0036 succeeded.
- Reapplying migration 0037 succeeded.
- Final code review found no remaining in-scope correctness or security defects.

## Development and CI implications

The local role bootstrap runs only when PostgreSQL initialises a fresh volume. Developers with an older local volume must run `make clear-db` before using this branch. This deletes local database data.

The pull-request workflow now:

- Pins PostgreSQL to major version 18.
- Provisions the owner, migrator, and application roles.
- Runs repository migrations through the migrator/owner path.
- Runs focused RLS tests as `webcaf_app` even though the test runner retains privileged credentials for temporary test-database creation.

The Compose unit-test target similarly uses the bootstrap role only for test-database lifecycle. The RLS tests explicitly use `SET LOCAL ROLE webcaf_app` and assert that enforcement is active.

The One Login and DEX Behave web services run as `webcaf_app`. Their fixture setup and cleanup retain an explicit privileged test connection.

See `README.md` and `webcaf/.env.example` for local migration, runtime, host-test, and management-shell commands.

## Security benefit and limitations

### Demonstrated benefit

- Protects against omitted organisation filters on the protected tables.
- Applies to ORM and direct SQL issued by the restricted role.
- Rejects cross-organisation writes as well as hiding reads.
- Fails closed when request context is absent.
- Remains safe when database connections are reused.
- Separates normal runtime credentials from schema ownership and migration access.

### Limitations

- Does not protect tables that do not yet have policies.
- Does not correct an incorrectly selected organisation before context is set.
- Does not implement RBAC within an organisation.
- Does not constrain PostgreSQL superusers or `BYPASSRLS` roles.
- Does not protect Redis sessions, S3 exports, backups, or other copied data.
- Does not eliminate all constraint-error or timing side channels.
- Does not protect against arbitrary SQL execution capable of changing custom settings.
- Does not remove the need for application filtering and authorization tests.

## Production prerequisites

The PoC bootstrap is intentionally designed for fresh local and CI databases. It is not an upgrade procedure for an existing managed database.

Before any production-like rollout:

1. Confirm the PostgreSQL engine version, connection proxy or pooling configuration, current roles, database owner, schema owner, and table owners.
2. Define and provision production owner, migrator, runtime, test, export, backup, and emergency-access roles.
3. Transfer existing object ownership and configure default privileges through a controlled operational change.
4. Define migration, backup, restore, export, and data-repair procedures.
5. Move external notification calls outside request transactions.
6. Decide the long-term Django admin organisation model.
7. Add durable organisation ownership to history records that can become orphaned.
8. Test policies using production-representative data volume and query plans.
9. Design deployment ordering and rollback so policies are never enabled before context-aware code and credentials are active.
10. Ensure monitoring can distinguish policy denial, missing context, and ordinary not-found behaviour.

## Recommended staged rollout

### Stage 1: production foundations

- Inventory actual database roles and ownership.
- Provision least-privilege roles outside Django migrations.
- Define credential rotation, migration, export, backup, and emergency procedures.
- Move external side effects outside request transactions.

### Stage 2: membership and historical schema

- Confirm the Organisation/Membership/RBAC model.
- Make the selected Membership the authoritative request context.
- Add immutable organisation ownership to affected historical records.
- Backfill recoverable history and decide how to handle orphaned rows.

### Stage 3: controlled policy expansion

- Add policies for Organisation and Membership/UserProfile with a safe authentication bootstrap path.
- Add Assessment, Review, and Tip policies using measured join-based or direct-key designs.
- Validate relationship changes, cascades, history creation, and deletion.
- Keep every stage reversible and independently testable.

### Stage 4: operational rollout

- Exercise admin, commands, exports, backups, Behave, and CI under final roles.
- Validate on a production-sized restored database.
- Deploy role provisioning, context-aware code, policies, and credential rotation in the agreed order.
- Monitor query performance and policy-denial behaviour.

## Proposed follow-on tickets

The investigation identifies the following implementation work:

1. Provision least-privilege roles and migrate ownership in deployed databases.
2. Move notifications and other external side effects outside request transactions.
3. Finalise the Organisation/Membership/RBAC model and active-membership contract.
4. Add durable organisation identifiers to orphan-prone historical models.
5. Extend RLS to Organisation and Membership/UserProfile with a safe bootstrap policy.
6. Extend RLS to Assessment, Review, Tip, and remaining history tables.
7. Define and implement privileged admin, export, backup, migration, and repair access.
8. Add policy coverage checks and production-scale query-plan tests.
9. Add operational monitoring and an RLS rollout/rollback runbook.

## Final recommendation

The PoC demonstrates that RLS can materially reduce the risk of cross-organisation access caused by missing application filters, provided WebCAF uses trusted transaction-local context and a restricted non-owner database role.

WebCAF should proceed with a staged implementation for core organisation-owned domain data. Approval for broader rollout should be conditional on the production role/ownership plan, transaction-side-effect changes, historical ownership design, privileged-operation design, and complete policy coverage for the chosen scope.
