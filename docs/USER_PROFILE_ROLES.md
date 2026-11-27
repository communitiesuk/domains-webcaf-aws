# User profile roles (CS-816)

Adds organisation memberships, the 11-role catalogue, audited assignments and
validated grant/removal paths. Application permission reads still use
`UserProfile.role`; switching those reads is separate work.

## Migrations and rollout

Two migrations follow CS-680's `0045`:
- `0046_user_profile_roles`: membership preflight, required profile organisation,
  unique user/organisation and profile/role pairs, and role/assignment/history tables.
- `0047_seed_user_roles`: the AC2 catalogue, matched by stable code rather than
  fixed numeric IDs. Reapplication restores canonical names/groups without
  replacing IDs; reversing the seed is a no-op to avoid deleting referenced roles.

The schema applies to all environments. The preflight reports missing organisations
and duplicate memberships and stops without deleting, merging or backfilling data.
In Sandbox, complete the separately approved CS-680 reset before schema migrations,
including removal of organisation-less profiles and verification of zero live profiles.
Other environments require approved preparation if the preflight fails; this change
does not reset any environment or carry over legacy roles.

Migration `0046` explicitly creates a PostgreSQL composite foreign key from
`UserProfileRole(user_profile_id, organisation_id)` to `UserProfile(id, organisation_id)`
and its supporting unique key. Django 5.2 does not generate this constraint.
It rejects mismatches immediately, including profile moves with existing assignments.
Historical rows retain their original references without this constraint.

## Managing assignments

In Django Admin, create one profile per user/organisation, then manage grants under
**User profile roles**. Organisation and grant metadata are derived automatically.
Revoke and re-grant instead of editing assignment identity; bulk actions and history
restoration are disabled. The catalogue allows display-name edits only.

Programmatic writes use `grant_role` and `revoke_role` in
`webcaf.webcaf.utils.role_assignments`. These require an active actor with Django's
assignment add/delete permission, lock the organisation then profile, and validate
eligibility and combinations in the write transaction. History records grants,
removals and their operators. Organisation-type edits reject incompatible existing
assignments. Revoke assignments before changing a profile's user or organisation;
normal model saves enforce this under the same locks as grants, including partial
and reconstructed-instance saves. Forms retain early validation for friendly errors.
Case-only email edits resolving to the same account remain allowed.
Loaded organisations and memberships cannot recreate deleted rows through a stale
save. Missing or cross-organisation edit targets return 404 rather than creating a membership.

Direct membership deletion and the legacy/Admin removal paths require assignments
to be explicitly revoked first. Deletion checks are locked against concurrent grants;
Admin bulk removal also uses the guarded model path. Organisation/account cascades
remain unchanged. Cascaded assignment deletions retain history, with the request actor
supplied by `HistoryRequestMiddleware` for authenticated web requests.

Eligibility uses the existing CS-680 organisation types, with combined authorities
stored as councils. The approved combination policy is implemented in
`webcaf.webcaf.utils.role_combinations`; unknown and unapproved combinations fail closed.
Direct bulk ORM/raw SQL writes bypass model save/delete guards and policy validation,
and bulk updates/inserts skip normal history. They are not supported grant, membership
management or reclassification paths. Database constraints still apply.

Existing user-facing forms continue editing the legacy role and report duplicate
memberships as form errors, including concurrent creation. LG grants do not change
application permissions, so developer profiles may also need an explicit legacy role
for existing application journeys.

## Follow-ups

- CAF Lead database uniqueness remains gated on user-testing retest confirmation.
- Application permission reads and removal of legacy role references are separate.
- CS-818's revised scope is unresolved; its old backfill description is not an
  assumed activation dependency. CS-817 is closed; both AC1 constraints are included here.
