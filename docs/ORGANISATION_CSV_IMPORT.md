# Council organisation CSV import

## Purpose and delivery status

The Django Admin import creates and updates council and combined-authority organisations by GSS code. It does not
create or update users, user profiles, memberships or roles. User onboarding is handled separately.

The central-government import has been replaced at its existing URLs. Legacy files are rejected rather than
automatically interpreted as council data.

The CSV schema below is agreed for CS-681. Organisation type labels and classifications, including Greater London
Authority, still require confirmation under CS-680.

**Automatic creation of new AllowedEmailDomain records is deferred.** CS-681's domain approval question is still
open. For now, an authorised administrator must approve a new domain in Allowed email domains before it can be
imported. The importer must not silently expand the sign-in allowlist. This is a deliberate outstanding part of AC10,
not a replacement for agreeing the approval policy with the ticket owners.

## Access

Open `/admin/webcaf/organisation/` and select **Import councils from CSV**. A template is available on that page or
at `/admin/webcaf/organisation/import-org-csv-template/`.

The upload page, upload action and template download require an active staff account with
`webcaf.add_organisation`. This permission authorises both creation and updates through this importer, as specified
by CS-681. Ordinary staff without it are refused. The import link is hidden when the permission is absent.

## CSV schema

```csv
Organisation,GSS code,Type,Contact email,Domains
Example Council,E06000001,Council,contact@example.gov.uk,example.gov.uk;shared.gov.uk
Example Combined Authority,E47000001,Combined authority,,shared.gov.uk
```

- `Organisation`: required name, validated using the organisation Admin form.
- `GSS code`: required; one uppercase letter followed by eight digits, unique per organisation.
- `Type`: required; currently the exact model labels `Council` or `Combined authority`.
- `Contact email`: optional column and value; a valid nonblank value replaces the existing contact email.
- `Domains`: optional column and value; semicolon-separated approved email domains, added to the organisation's links.

Header order does not matter. Required columns must be present, but either optional column may be omitted.
Missing, unexpected, duplicate and unsupported legacy headers are reported explicitly. `Reference`,
`Lead Government Department`, and `Email1` to `Email6` are unsupported and cause the whole file to be rejected.

Files must be UTF-8 (a UTF-8 BOM is accepted), at most 5 MiB, and contain at least one organisation. Completely empty
lines are ignored. Incorrect column counts, malformed CSV and invalid encoding are rejected. Error row numbers refer
to physical CSV lines, including the header and any multiline quoted fields.

## Matching and updates

An existing GSS code identifies an update, including a renamed council. A new GSS code identifies a possible new
organisation, subject to the reconciliation checks below. The importer never uses a supplied WebCAF reference,
automatically overwrites an existing GSS code, or merges records.

Duplicate GSS codes or names in a file reject the entire import. Name comparison for duplicate detection and
reconciliation ignores case and repeated whitespace. Conflicting names belonging to another GSS code are rejected.
The normal Django Admin form validates each proposed organisation, including email, name length and uniqueness.

On re-import:

- A blank or omitted contact email preserves the existing email; a nonblank valid email replaces it.
- Blank or omitted domains preserve all existing links.
- Supplied domains are additive. No links are removed, no primary domain is automatically selected or demoted, and
  a supplied retired link blocks import pending Admin review rather than being silently reactivated.
- Domain names use the existing canonicalisation rules: lowercase, IDNA ASCII encoding and no trailing dot.
- A domain may be shared by several organisations. Repeated domains do not create duplicate links.
- Generated references, contact name/role and parent organisations are preserved.
- No record or history entry is written for an unchanged organisation.

Removing optional data or changing primary/retired domain settings remains an explicit Admin operation.

## Reconciliation before creating records

For each previously unseen GSS code, the importer checks existing organisations without GSS codes for:

1. A name matching after case and whitespace normalisation.
2. An overlapping linked email domain, including retired links.

These are review signals, **not proof that two organisations are the same**. Shared domains may produce multiple
candidates. Any candidates block the entire file and are listed with IDs, reasons and links to Django Admin.
The importer performs no fuzzy-name inference: a renamed organisation with neither a matching normalised name nor
a shared linked domain cannot be identified automatically and requires dataset review before upload.

Staff must review the candidates and explicitly populate the confirmed organisation's GSS code in Django Admin,
using an account with organisation change permission. Admin history records the change and acting user. Then
re-upload the file; the organisation is now matched by GSS code. Resolve ambiguous matches and conflicting data
before retrying. Never assign a different GSS code to bypass a conflict.

If review establishes that all candidates are different organisations (for example, unrelated bodies sharing a
domain), explicitly create the new organisation with its correct GSS code in Django Admin, then re-upload. This
records the staff member's decision in the new organisation's history without changing the unrelated candidates.
There is no unchecked "ignore matches" option in the importer.

## Atomicity, results and audit

The complete file is validated before any organisation or domain link is saved. All detected row problems are shown
together. Database-dependent checks and writes run in one transaction, with stable locking and unique constraints
protecting against concurrent changes. A validation or persistence failure rolls back the whole import, including
new organisations, domain links and their history. Database error details are not exposed or logged because they
may contain contact email addresses. Database sequences may advance even when their transaction is rolled back.

After success the page shows:

- Organisations created, updated and unchanged. Adding a domain link counts as an organisation update.
- Per-organisation changes, with contact email changes masked.
- Every existing organisation absent from the file, including those without GSS codes. These records are not changed
  or deleted; omission is only reported for staff review.

Organisation and link history records identify the importing staff user and CSV row. Application logs contain only
counts or a generic rejection message, never CSV rows, filenames, full email addresses or database exception details.
Development SQL logging withholds queries containing `@`, including INSERT parameters and historical-record writes,
so enabling `DEBUG` does not expose uploaded email addresses through the database logger.

Refreshing a successful POST re-runs validation and import, but an identical upload is idempotent. There is no
persistent uploaded-file storage, automatic reconciliation workflow or new import-job model.

## Remaining decisions before CS-681 acceptance

- Confirm organisation type labels and classifications under CS-680, including Greater London Authority.
- Agree and implement the approval rule for adding previously unknown domains to the authentication allowlist.
- Confirm that manual reconciliation with existing Django Admin history and the documented candidate rules meets
  AC14's review and audit requirements.
