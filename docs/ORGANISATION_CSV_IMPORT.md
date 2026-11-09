# Organisation CSV import

## Purpose

The Django admin import creates organisation records and associates them with a lead government department. It does
not create or update users, user profiles, organisation memberships, or role assignments.

User onboarding, including invitations and claiming a council, is handled separately.

## Access

Open `/admin/webcaf/organisation/` and select **Import Organisations from CSV**. A blank template is available from
the import page or `/admin/webcaf/organisation/import-org-csv-template/`.

## Agreed council CSV schema

The CSV must contain exactly these headers. Their order does not matter:

```csv
Organisation,Lead Government Department,Reference,Type
```

- `Organisation` (required): name of the organisation.
- `Lead Government Department` (optional): parent organisation name.
- `Reference` (optional): existing WebCAF organisation reference used for matching.
- `Type` (optional): organisation type label.

Example:

```csv
Organisation,Lead Government Department,Reference,Type
Cabinet Office,,,Ministerial department
Government Digital Service,Cabinet Office,,Executive agency
```

`Reference` is used only to match an existing organisation. WebCAF generates a reference when it creates a new
organisation.

## Legacy email columns

`Email1`, `Email2`, `Email3`, `Email4`, `Email5`, and `Email6` are not part of the agreed council CSV schema. If any
of these columns is present, the entire import is rejected before any rows are processed. The validation message
identifies each unsupported column and displays the agreed schema.

Email addresses in legacy columns are not stored. The import does not use them to create or update users,
`UserProfile` records, memberships, or roles.

## Import behaviour

For each valid row, the importer:

1. Looks for an existing organisation by `Reference`, when supplied.
2. Falls back to matching by `Organisation` name.
3. Creates the organisation when no match is found.
4. Sets a recognised organisation type on new records.
5. Assigns the parent organisation named in `Lead Government Department`, while preventing self-parenting.

Existing users and profiles, including records created by earlier imports, are not changed or removed.

## Organisation type values

The `Type` column accepts these case-sensitive labels:

- Ad-hoc advisory group
- Advisory non-departmental public body
- Agency or other public body
- Devolved administration
- Executive agency
- Executive non-departmental public body
- Executive office
- High profile group
- Ministerial department
- Non-ministerial department
- Public corporation
- Tribunal
- Other

An invalid or empty type is ignored and a new organisation is created without a type.

## Validation

- The header must match the agreed council CSV schema exactly, although column order may differ.
- Legacy `Email1` to `Email6` columns produce a specific unsupported-column error.
- Validation failures reject the file before organisation processing begins.
- A successful import reports the number of organisation rows processed.
