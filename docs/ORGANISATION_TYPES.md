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
