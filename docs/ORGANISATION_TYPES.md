# Organisation types and council subtypes

LG WebCAF has three organisation types, set in Django Admin:

| Type | Stored value | GSS code |
|---|---|---|
| Council | `council` | Required: a unique GSS code, one uppercase letter followed by eight digits |
| MHCLG | `mhclg` | Optional, usually blank |
| Independent assurer | `independent-assurer` | Optional, usually blank |

Combined authorities are stored as councils. Their GSS code identifies them.

## Council subtype

A council's subtype is not stored. It is worked out from the first three characters of its GSS code, using
`Organisation.COUNCIL_SUBTYPES_BY_GSS_PREFIX` in `webcaf/webcaf/models.py`, which is the only place the mapping is kept.

| GSS prefix | Council subtype | Example |
|---|---|---|
| E06 | Unitary authority | E06000014 City of York |
| E07 | Non-metropolitan district | E07000110 Maidstone |
| E08 | Metropolitan district | E08000035 Leeds |
| E09 | London borough | E09000033 Westminster |
| E10 | County council | E10000016 Kent |
| E47 | Combined authority | E47000003 West Yorkshire |

A council whose GSS code has any other prefix has no subtype. The subtype is read-only: it is shown in Django Admin
(the organisation list and form) and on My organisation, and changes only when the GSS code changes.

## Organisation identifier

Organisations have no generated reference. The organisation id (the database primary key) identifies an organisation
wherever an identifier is needed: in the Django Admin list, in the `Organisation ID` column of the organisation CSV
import, and in the data export file name.

## Data export changes (CS-680)

`python manage.py export_data` writes one JSON file per organisation, for every organisation type, to
`organisations/<id>.json`, where `<id>` is the organisation id (the same value as `organisation_id` in the file). This is
the LG WebCAF export format; the file names of the inherited central-government WebCAF export do not apply. Compared
with the format before CS-680:

| What | Before | Now |
|---|---|---|
| File name | `organisations/<reference>.json`, using the generated 5-character reference | `organisations/<id>.json`, using the organisation id |
| `organisation_type` | One of the inherited central-government type labels, Council, Combined authority or Other | `Council`, `MHCLG` or `Independent assurer`, or `null` if no type is set |
| `parent_organisation_id`, `parent_organisation_name` | Present | Removed |

The other fields (`organisation_name`, `organisation_id`, `app_version`, `legacy_organisation_id`) are unchanged. The
contact name, email and role were never exported. The analysis data built from the export
(`webcaf/webcaf/utils/data_analysis.py`) no longer has the parent organisation fields.
