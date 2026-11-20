"""Validate a council CSV completely, then apply it in one transaction.

The agreed CSV schema is defined by CSV_HEADERS below. New authentication
allowlist entries deliberately require approval in Admin until the dataset
domain approval policy is agreed.
"""

import csv
from collections import defaultdict
from copy import copy
from dataclasses import dataclass, field
from io import StringIO

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms.models import model_to_dict

from webcaf.webcaf.forms.organisation import OrganisationAdminForm
from webcaf.webcaf.models import AllowedEmailDomain, Organisation, OrganisationEmailDomain
from webcaf.webcaf.utils import mask_email

REQUIRED_HEADERS = ("Organisation", "GSS code", "Type")
OPTIONAL_HEADERS = ("Contact email", "Domains")
CSV_HEADERS = REQUIRED_HEADERS + OPTIONAL_HEADERS
LEGACY_HEADERS = ("Reference", "Lead Government Department", *(f"Email{number}" for number in range(1, 7)))
MAX_CSV_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class ReconciliationCandidate:
    pk: int
    name: str
    reason: str


@dataclass(frozen=True)
class ImportIssue:
    row_number: int | None
    field: str
    message: str
    candidates: tuple[ReconciliationCandidate, ...] = ()


class OrganisationImportError(Exception):
    def __init__(self, issues):
        # Exception text must be safe even if captured by application monitoring.
        super().__init__("The organisation CSV could not be imported. Nothing was saved.")
        self.issues = issues


@dataclass
class CouncilRow:
    number: int
    name: str
    gss_code: str
    type_label: str
    contact_email: str
    domains: tuple[str, ...]


@dataclass
class OrganisationChange:
    pk: int
    name: str
    gss_code: str
    action: str
    details: list[str]


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    changes: list[OrganisationChange] = field(default_factory=list)
    missing_organisations: list[Organisation] = field(default_factory=list)


@dataclass
class PlannedOrganisation:
    row: CouncilRow
    organisation: Organisation
    is_new: bool
    details: list[str]
    domains_to_link: list[AllowedEmailDomain]


def _normalise_name(name):
    return " ".join(name.split()).casefold()


def _header_issues(headers):
    issues = []
    missing = [header for header in REQUIRED_HEADERS if header not in headers]
    unsupported = list(dict.fromkeys(header for header in headers if header in LEGACY_HEADERS))
    unexpected = list(dict.fromkeys(header for header in headers if header not in (*CSV_HEADERS, *LEGACY_HEADERS)))
    duplicates = list(dict.fromkeys(header for header in headers if headers.count(header) > 1))
    for label, columns in (
        ("Missing required columns", missing),
        ("Unsupported legacy columns", unsupported),
        ("Unexpected columns", unexpected),
        ("Duplicate columns", duplicates),
    ):
        if columns:
            issues.append(ImportIssue(1, "Headers", f"{label}: {', '.join(columns)}."))
    if issues:
        issues.append(ImportIssue(1, "Headers", f"Expected CSV schema: {', '.join(CSV_HEADERS)}."))
    return issues


def _read_rows(upload):
    contents = upload.read(MAX_CSV_BYTES + 1)
    if len(contents) > MAX_CSV_BYTES:
        raise OrganisationImportError([ImportIssue(None, "File", "The CSV must be no larger than 5 MiB.")])
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise OrganisationImportError([ImportIssue(None, "File", "Upload a UTF-8 encoded CSV file.")]) from None

    reader = csv.reader(StringIO(text, newline=""), strict=True)
    rows, issues = [], []
    try:
        headers = next(reader, [])
        if header_issues := _header_issues(headers):
            raise OrganisationImportError(header_issues)
        while True:
            row_number = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            if not values:  # Ignore completely empty lines, not rows of empty cells.
                continue
            if len(values) != len(headers):
                issues.append(
                    ImportIssue(row_number, "Columns", f"Expected {len(headers)} values; found {len(values)}.")
                )
            row = dict(zip(headers, values))
            domains = tuple(value.strip() for value in row.get("Domains", "").split(";") if value.strip())
            rows.append(
                CouncilRow(
                    number=row_number,
                    name=row.get("Organisation", "").strip(),
                    gss_code=row.get("GSS code", "").strip(),
                    type_label=row.get("Type", "").strip(),
                    contact_email=row.get("Contact email", "").strip(),
                    domains=domains,
                )
            )
    except csv.Error:
        issues.append(ImportIssue(reader.line_num, "File", "Malformed CSV. Check quoting and column separators."))
    if not rows:
        issues.append(ImportIssue(None, "File", "The CSV must contain at least one organisation."))
    return rows, issues


def _duplicate_issues(rows):
    issues = []
    for attribute, label, normalise in (
        ("gss_code", "GSS code", str),
        ("name", "Organisation", _normalise_name),
    ):
        occurrences = defaultdict(list)
        for row in rows:
            value = normalise(getattr(row, attribute))
            if value:
                occurrences[value].append(row.number)
        for row_numbers in occurrences.values():
            if len(row_numbers) > 1:
                message = f"Duplicate {label.lower()} in rows {', '.join(map(str, row_numbers))}."
                issues.extend(ImportIssue(number, label, message) for number in row_numbers)
    return issues


def _validate_domains(row, issues):
    domains = []
    for value in row.domains:
        try:
            domain = AllowedEmailDomain(domain=value)
            domain.clean()
            domain.full_clean(validate_unique=False)
        except ValidationError as error:
            issues.extend(ImportIssue(row.number, "Domains", message) for message in error.messages)
        else:
            if domain.domain not in domains:
                domains.append(domain.domain)
    return domains


def _reconciliation_candidates(row, domains, organisations, links):
    candidates = []
    for organisation in organisations:
        if organisation.gss_code:
            continue
        reasons = []
        if _normalise_name(organisation.name) == _normalise_name(row.name):
            reasons.append("matching name")
        if set(domains) & links.get(organisation.pk, {}).keys():
            reasons.append("shared email domain (not proof of identity)")
        if reasons:
            candidates.append(ReconciliationCandidate(organisation.pk, organisation.name, "; ".join(reasons)))
    return tuple(candidates)


def _organisation_form(row, existing, form_class, issues):
    instance = copy(existing) if existing else Organisation()
    data = model_to_dict(instance, fields=form_class.base_fields)
    data.update(name=row.name, gss_code=row.gss_code, organisation_type=Organisation.get_type_id(row.type_label))
    if row.contact_email:
        data["contact_email"] = row.contact_email
    # Feed unknown types to the actual form to retain its standard choice error.
    if row.type_label and data["organisation_type"] is None:
        data["organisation_type"] = row.type_label
    form = form_class(data=data, instance=instance)
    if not form.is_valid():
        for name, errors in form.errors.items():
            label = str(form.fields[name].label) if name in form.fields else "Organisation"
            issues.extend(ImportIssue(row.number, label, str(error)) for error in errors)
    if not row.gss_code and not form.errors.get("__all__"):
        issues.append(ImportIssue(row.number, "GSS code", "A GSS code is required for every council import row."))
    council_types = Organisation.GSS_CODE_REQUIRED_ORGANISATION_TYPES
    if not row.type_label:
        issues.append(ImportIssue(row.number, "Type", "An organisation type is required."))
    elif Organisation.get_type_id(row.type_label) not in council_types and not form.errors.get("organisation_type"):
        issues.append(ImportIssue(row.number, "Type", "Only Council and Combined authority types can be imported."))
    return form.instance


def _change_details(existing, organisation):
    if existing is None:
        return ["Organisation created."]
    details = []
    if existing.name != organisation.name:
        details.append(f"Name: {existing.name} → {organisation.name}.")
    if existing.organisation_type != organisation.organisation_type:
        details.append(
            f"Type: {existing.get_organisation_type_display() or 'not set'} → "
            f"{organisation.get_organisation_type_display()}."
        )
    if existing.contact_email != organisation.contact_email:
        details.append(
            f"Contact email: {mask_email(existing.contact_email or '') or 'not set'} → "
            f"{mask_email(organisation.contact_email or '') or 'not set'}."
        )
    return details


def _plan_rows(rows, organisations, domains, links, form_class, issues):
    by_gss = {organisation.gss_code: organisation for organisation in organisations if organisation.gss_code}
    planned = []
    for row in rows:
        existing = by_gss.get(row.gss_code)
        domain_names = _validate_domains(row, issues)
        organisation = _organisation_form(row, existing, form_class, issues)
        if existing is None:
            candidates = _reconciliation_candidates(row, domain_names, organisations, links)
            if candidates:
                issues.append(
                    ImportIssue(
                        row.number,
                        "Reconciliation",
                        "Potential existing organisation without a GSS code. Review the candidates and explicitly "
                        "set the confirmed organisation's GSS code in Django Admin before re-uploading. "
                        "If all candidates are different organisations, explicitly add the new organisation in Admin "
                        "after review, then re-upload. Admin history records either decision. "
                        "Do not overwrite a different GSS code or merge organisations automatically.",
                        candidates,
                    )
                )
        conflicting_names = [
            other
            for other in organisations
            if other.pk != organisation.pk
            and (other.gss_code or existing is not None)
            and _normalise_name(other.name) == _normalise_name(row.name)
        ]
        if conflicting_names:
            issues.append(
                ImportIssue(
                    row.number,
                    "Organisation",
                    "This name belongs to another organisation. Resolve the conflict in "
                    "Django Admin; never overwrite a different GSS code.",
                )
            )
        details = _change_details(existing, organisation)
        new_domains = []
        existing_links = links.get(organisation.pk, {})
        for name in domain_names:
            domain = domains.get(name)
            if domain is None:
                issues.append(
                    ImportIssue(
                        row.number,
                        "Domains",
                        f"Domain {name} is not approved. An authorised domain administrator must approve it in "
                        "Allowed email domains before import. Automatic allowlisting awaits the CS-458 approval policy.",
                    )
                )
            elif name not in existing_links:
                new_domains.append(domain)
                details.append(f"Email domain linked: {name}.")
            elif existing_links[name].status == OrganisationEmailDomain.Status.RETIRED:
                issues.append(
                    ImportIssue(
                        row.number, "Domains", f"The link for {name} is retired. Review it in Django Admin first."
                    )
                )
        planned.append(PlannedOrganisation(row, organisation, existing is None, details, new_domains))
    return planned


def _save_plan(planned, organisations, user):
    result = ImportResult()
    imported_ids = set()
    for plan in planned:
        organisation = plan.organisation
        if plan.is_new or plan.details:
            organisation._history_user = user
            organisation._change_reason = f"Council CSV import, row {plan.row.number}"
            organisation.save()
        for domain in plan.domains_to_link:
            link = OrganisationEmailDomain(organisation=organisation, domain=domain)
            link._history_user = user
            link._change_reason = f"Council CSV import, row {plan.row.number}"
            link.full_clean()
            link.save()
        if plan.is_new:
            result.created += 1
            action = "Created"
        elif plan.details:
            result.updated += 1
            action = "Updated"
        else:
            result.unchanged += 1
            action = "Unchanged"
        imported_ids.add(organisation.pk)
        result.changes.append(
            OrganisationChange(organisation.pk, organisation.name, organisation.gss_code, action, plan.details)
        )
    result.missing_organisations = sorted(
        (organisation for organisation in organisations if organisation.pk not in imported_ids),
        key=lambda organisation: organisation.name.casefold(),
    )
    return result


def import_organisations(upload, *, user, form_class=OrganisationAdminForm):
    """Import only after every row passes validation; never expose database errors."""
    rows, issues = _read_rows(upload)
    issues.extend(_duplicate_issues(rows))
    try:
        with transaction.atomic():
            # A stable lock order serialises imports against existing organisations
            # and Admin changes. Unique constraints protect concurrent new records.
            organisations = list(Organisation.objects.select_for_update().order_by("pk"))
            domains = {
                domain.domain: domain for domain in AllowedEmailDomain.objects.select_for_update().order_by("pk")
            }
            links = defaultdict(dict)
            for link in OrganisationEmailDomain.objects.select_for_update().select_related("domain").order_by("pk"):
                links[link.organisation_id][link.domain.domain] = link
            planned = _plan_rows(rows, organisations, domains, links, form_class, issues)
            if issues:
                raise OrganisationImportError(issues)
            return _save_plan(planned, organisations, user)
    except (IntegrityError, ValidationError):
        # Do not include the exception or traceback: PostgreSQL may include a
        # complete failing row, including its contact email, in the detail.
        raise OrganisationImportError(
            [
                ImportIssue(
                    None,
                    "Database",
                    "A record changed or failed validation during import. Nothing was saved. "
                    "Review the current organisations and domains, then upload the file again.",
                )
            ]
        ) from None
