import csv
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from threading import Event
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase

from webcaf.webcaf.admin import OrganisationAdmin
from webcaf.webcaf.models import AllowedEmailDomain, Organisation, OrganisationEmailDomain
from webcaf.webcaf.utils.organisation_importer import (
    CSV_HEADERS,
    LEGACY_HEADERS,
    MAX_CSV_BYTES,
    REQUIRED_HEADERS,
    OrganisationImportError,
    import_organisations,
)


class OrganisationImporterTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="importer", is_staff=True)

    @staticmethod
    def row(**overrides):
        return {"Organisation": "Test Council", "GSS code": "E06000001", "Type": "Council", **overrides}

    def upload(self, rows, headers=CSV_HEADERS):
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
        return BytesIO(buffer.getvalue().encode())

    def import_rows(self, rows, headers=CSV_HEADERS):
        return import_organisations(self.upload(rows, headers), user=self.user)

    def errors(self, rows, headers=CSV_HEADERS):
        with self.assertRaises(OrganisationImportError) as caught:
            self.import_rows(rows, headers)
        return caught.exception.issues

    def snapshot(self):
        models = (
            Organisation,
            Organisation.history.model,
            AllowedEmailDomain,
            AllowedEmailDomain.history.model,
            OrganisationEmailDomain,
            OrganisationEmailDomain.history.model,
        )
        return [list(model.objects.order_by(model._meta.pk.name).values()) for model in models]

    def test_creates_council_and_combined_authority_with_contact_email(self):
        result = self.import_rows(
            [
                self.row(**{"Contact email": "contact@test.gov.uk"}),
                self.row(
                    **{"Organisation": "Combined Authority", "GSS code": "E47000001", "Type": "Combined authority"}
                ),
            ]
        )
        self.assertEqual((result.created, result.updated, result.unchanged), (2, 0, 0))
        council = Organisation.objects.get(gss_code="E06000001")
        self.assertEqual(council.contact_email, "contact@test.gov.uk")
        self.assertEqual(council.organisation_type, "council")
        self.assertTrue(council.reference)
        self.assertEqual(Organisation.objects.get(gss_code="E47000001").organisation_type, "combined-authority")

    def test_optional_columns_can_be_omitted_and_header_order_does_not_matter(self):
        result = self.import_rows([self.row()], headers=tuple(reversed(REQUIRED_HEADERS)))
        self.assertEqual(result.created, 1)

    def test_same_file_is_unchanged_on_reimport_including_history(self):
        domain = AllowedEmailDomain.objects.create(domain="council.gov.uk")
        row = self.row(**{"Contact email": "contact@council.gov.uk", "Domains": domain.domain})
        self.import_rows([row])
        before = self.snapshot()
        result = self.import_rows([row])
        self.assertEqual((result.created, result.updated, result.unchanged), (0, 0, 1))
        self.assertEqual(self.snapshot(), before)

    def test_rename_matches_gss_code_and_preserves_reference_and_other_fields(self):
        parent = Organisation.objects.create(name="Parent")
        organisation = Organisation.objects.create(
            name="Old Council",
            gss_code="E06000001",
            organisation_type="council",
            parent_organisation=parent,
            contact_name="Contact",
            contact_role="Role",
            contact_email="keep@test.gov.uk",
        )
        reference = organisation.reference
        result = self.import_rows([self.row()])
        organisation.refresh_from_db()
        self.assertEqual((result.created, result.updated, result.unchanged), (0, 1, 0))
        self.assertEqual(organisation.name, "Test Council")
        self.assertEqual(organisation.reference, reference)
        self.assertEqual(organisation.parent_organisation, parent)
        self.assertEqual((organisation.contact_name, organisation.contact_role), ("Contact", "Role"))
        self.assertEqual(organisation.contact_email, "keep@test.gov.uk")
        self.assertIn("Name: Old Council → Test Council.", result.changes[0].details)

    def test_nonblank_contact_email_replaces_existing_value_and_change_report_is_masked(self):
        self.import_rows([self.row(**{"Contact email": "old@test.gov.uk"})])
        result = self.import_rows([self.row(**{"Contact email": "new@test.gov.uk"})])
        self.assertEqual(result.updated, 1)
        self.assertEqual(Organisation.objects.get().contact_email, "new@test.gov.uk")
        self.assertIn("Contact email: ol***@test.gov.uk → ne***@test.gov.uk.", result.changes[0].details)

    def test_blank_optional_values_preserve_email_primary_domain_and_retired_links(self):
        self.import_rows([self.row(**{"Contact email": "keep@test.gov.uk"})])
        organisation = Organisation.objects.get()
        primary = OrganisationEmailDomain.objects.create(
            organisation=organisation,
            domain=AllowedEmailDomain.objects.create(domain="primary.gov.uk"),
            is_primary=True,
        )
        retired = OrganisationEmailDomain.objects.create(
            organisation=organisation,
            domain=AllowedEmailDomain.objects.create(domain="retired.gov.uk"),
            status="retired",
        )
        before = self.snapshot()
        result = self.import_rows([self.row(**{"Contact email": "   ", "Domains": "  "})])
        self.assertEqual(result.unchanged, 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(organisation.email_domain_links.values_list("pk", flat=True)), {primary.pk, retired.pk})

    def test_shared_domains_are_normalised_added_once_and_linked_to_both_councils(self):
        AllowedEmailDomain.objects.create(domain="shared.gov.uk")
        rows = [
            self.row(Domains=" Shared.GOV.UK.;shared.gov.uk "),
            self.row(**{"Organisation": "Other Council", "GSS code": "E06000002", "Domains": "shared.gov.uk"}),
        ]
        self.import_rows(rows)
        self.assertEqual(AllowedEmailDomain.objects.count(), 1)
        self.assertEqual(OrganisationEmailDomain.objects.count(), 2)
        self.assertFalse(OrganisationEmailDomain.objects.filter(is_primary=True).exists())
        self.assertEqual(self.import_rows(rows).unchanged, 2)
        self.assertEqual(OrganisationEmailDomain.objects.count(), 2)

    def test_domains_are_additive_and_domain_only_change_counts_as_updated(self):
        first = AllowedEmailDomain.objects.create(domain="first.gov.uk")
        second = AllowedEmailDomain.objects.create(domain="second.gov.uk")
        self.import_rows([self.row(Domains=first.domain)])
        result = self.import_rows([self.row(Domains=second.domain)])
        self.assertEqual((result.created, result.updated, result.unchanged), (0, 1, 0))
        self.assertEqual(set(Organisation.objects.get().allowed_email_domains.all()), {first, second})

    def test_unknown_domains_require_approval_and_never_change_authentication_allowlist(self):
        before = self.snapshot()
        issues = self.errors([self.row(Domains="unapproved.gov.uk")])
        self.assertTrue(any("not approved" in issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(AllowedEmailDomain.allows_email("person@unapproved.gov.uk"))

    def test_retired_domain_link_is_not_silently_reactivated(self):
        domain = AllowedEmailDomain.objects.create(domain="retired.gov.uk")
        self.import_rows([self.row()])
        OrganisationEmailDomain.objects.create(organisation=Organisation.objects.get(), domain=domain, status="retired")
        before = self.snapshot()
        issues = self.errors([self.row(Domains=domain.domain)])
        self.assertTrue(any("is retired" in issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)

    def test_reports_all_invalid_values_before_any_saves(self):
        rows = [
            self.row(),
            self.row(
                **{
                    "Organisation": "Invalid",
                    "GSS code": "bad",
                    "Type": "Unknown",
                    "Contact email": "bad",
                    "Domains": "bad_domain;https://bad.gov.uk",
                }
            ),
            self.row(**{"Organisation": "", "GSS code": "", "Type": ""}),
        ]
        before = self.snapshot()
        with patch.object(Organisation, "save") as save:
            issues = self.errors(rows)
        save.assert_not_called()
        row_three = [issue for issue in issues if issue.row_number == 3]
        self.assertTrue(any("valid GSS code" in issue.message for issue in row_three))
        self.assertTrue(any("valid choice" in issue.message for issue in row_three))
        self.assertTrue(any("valid email address" in issue.message for issue in row_three))
        self.assertEqual(sum(issue.field == "Domains" for issue in row_three), 2)
        self.assertGreaterEqual(sum(issue.row_number == 4 for issue in issues), 3)
        self.assertEqual(self.snapshot(), before)

    def test_missing_gss_code_rejects_row(self):
        issues = self.errors([self.row(**{"GSS code": ""})])
        self.assertTrue(any("must have a GSS code" in issue.message for issue in issues))

    def test_non_council_types_are_rejected(self):
        issues = self.errors([self.row(Type="Other")])
        self.assertTrue(any("Only Council and Combined authority" in issue.message for issue in issues))

    def test_duplicate_gss_codes_and_normalised_names_report_every_affected_row(self):
        rows = [
            self.row(),
            self.row(**{"Organisation": " test   COUNCIL "}),
            self.row(**{"Organisation": "Third Council"}),
        ]
        issues = self.errors(rows)
        duplicates = [issue for issue in issues if "Duplicate" in issue.message]
        self.assertEqual({issue.row_number for issue in duplicates}, {2, 3, 4})
        self.assertTrue(any("rows 2, 3, 4" in issue.message for issue in duplicates))
        self.assertTrue(any("Duplicate organisation in rows 2, 3" in issue.message for issue in duplicates))

    def test_invalid_row_rolls_back_existing_updates_and_new_records(self):
        self.import_rows([self.row()])
        before = self.snapshot()
        self.errors(
            [
                self.row(**{"Organisation": "Renamed Council"}),
                self.row(**{"Organisation": "New Council", "GSS code": "E06000002"}),
                self.row(**{"Organisation": "Invalid Council", "GSS code": "bad"}),
            ]
        )
        self.assertEqual(self.snapshot(), before)

    def test_mid_save_failure_rolls_back_organisations_domain_links_and_history(self):
        domain = AllowedEmailDomain.objects.create(domain="council.gov.uk")
        before = self.snapshot()
        original_save = OrganisationEmailDomain.save

        def fail_second_link(link, *args, **kwargs):
            if link.organisation.gss_code == "E06000002":
                raise IntegrityError("Sensitive database detail")
            return original_save(link, *args, **kwargs)

        with patch.object(OrganisationEmailDomain, "save", new=fail_second_link):
            issues = self.errors(
                [
                    self.row(Domains=domain.domain),
                    self.row(**{"Organisation": "Second Council", "GSS code": "E06000002", "Domains": domain.domain}),
                ]
            )
        self.assertTrue(any("Nothing was saved" in issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)

    def test_validation_failure_during_save_is_safe_and_rolls_back(self):
        before = self.snapshot()
        with patch.object(Organisation, "save", side_effect=ValidationError("private.person@test.gov.uk")):
            issues = self.errors([self.row()])
        self.assertNotIn("private.person@test.gov.uk", " ".join(issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)

    def test_debug_sql_logs_cannot_expose_imported_emails_or_history_values(self):
        email = "private.person@test.gov.uk"
        with self.settings(DEBUG=True):
            with self.assertLogs("django.db.backends", level="DEBUG") as captured:
                self.import_rows([self.row(**{"Contact email": email})])
        self.assertEqual(Organisation.objects.get().contact_email, email)
        self.assertNotIn(email, " ".join(captured.output))
        for record in captured.records:
            self.assertNotIn(email, repr(record.__dict__))

    def test_header_errors_name_missing_unexpected_and_legacy_columns_together(self):
        headers = ("Organisation", "Type", "Unexpected", *LEGACY_HEADERS)
        row = {"Organisation": "Test Council", "Type": "Council"}
        issues = self.errors([row], headers=headers)
        messages = " ".join(issue.message for issue in issues)
        self.assertIn("Missing required columns: GSS code.", messages)
        self.assertIn("Unexpected columns: Unexpected.", messages)
        self.assertIn(f"Unsupported legacy columns: {', '.join(LEGACY_HEADERS)}.", messages)
        self.assertIn(f"Expected CSV schema: {', '.join(CSV_HEADERS)}.", messages)

    def test_duplicate_headers_are_rejected(self):
        issues = self.errors([self.row()], headers=(*CSV_HEADERS, "Type"))
        self.assertTrue(any("Duplicate columns: Type" in issue.message for issue in issues))

    def test_empty_files_and_headers_without_data_are_rejected(self):
        for content in (b"", b"Organisation,GSS code,Type\n"):
            with self.subTest(content=content):
                with self.assertRaises(OrganisationImportError):
                    import_organisations(BytesIO(content), user=self.user)

    def test_utf8_bom_and_blank_lines_are_supported(self):
        contents = b"\xef\xbb\xbfOrganisation,GSS code,Type\r\n\r\nTest Council,E06000001,Council\r\n"
        self.assertEqual(import_organisations(BytesIO(contents), user=self.user).created, 1)

    def test_wrong_column_counts_malformed_quoting_encoding_and_oversized_files_are_rejected(self):
        for contents in (
            b"Organisation,GSS code,Type\nCouncil,E06000001,Council,extra\n",
            b"Organisation,GSS code,Type\nCouncil,E06000001\n",
            b'Organisation,GSS code,Type\n"unclosed',
            b"\xff",
            b"a" * (MAX_CSV_BYTES + 1),
        ):
            with self.subTest(start=contents[:50]):
                with self.assertRaises(OrganisationImportError):
                    import_organisations(BytesIO(contents), user=self.user)
        self.assertFalse(Organisation.objects.exists())

    def test_multiline_fields_report_physical_csv_line_numbers(self):
        content = b'Organisation,GSS code,Type\n"First\nCouncil",E06000001,Council\nSecond,bad,Council\n'
        with self.assertRaises(OrganisationImportError) as caught:
            import_organisations(BytesIO(content), user=self.user)
        self.assertTrue(
            any(issue.row_number == 4 and "valid GSS" in issue.message for issue in caught.exception.issues)
        )

    def test_existing_organisation_without_gss_is_reported_not_automatically_matched(self):
        existing = Organisation.objects.create(name="Test Council", contact_email="keep@test.gov.uk")
        before = self.snapshot()
        issues = self.errors([self.row()])
        candidates = [candidate for issue in issues for candidate in issue.candidates]
        self.assertEqual([candidate.pk for candidate in candidates], [existing.pk])
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_normalised_name_matches_report_all_candidates(self):
        first = Organisation.objects.create(name="Test Council")
        second = Organisation.objects.create(name="test  council")
        issues = self.errors([self.row()])
        self.assertEqual({candidate.pk for issue in issues for candidate in issue.candidates}, {first.pk, second.pk})

    def test_shared_domains_report_potential_renamed_organisations_without_merging(self):
        domain = AllowedEmailDomain.objects.create(domain="shared.gov.uk")
        organisations = [Organisation.objects.create(name=name) for name in ("Old Council", "Another Council")]
        for organisation in organisations:
            OrganisationEmailDomain.objects.create(organisation=organisation, domain=domain)
        before = self.snapshot()
        issues = self.errors([self.row(Domains=domain.domain)])
        self.assertEqual(
            {candidate.pk for issue in issues for candidate in issue.candidates}, {org.pk for org in organisations}
        )
        self.assertEqual(self.snapshot(), before)

    def test_conflicting_name_does_not_replace_existing_gss_code(self):
        organisation = Organisation.objects.create(
            name="Test Council", gss_code="E06000002", organisation_type="council"
        )
        before = self.snapshot()
        issues = self.errors([self.row()])
        self.assertTrue(any("different GSS code" in issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)
        organisation.refresh_from_db()
        self.assertEqual(organisation.gss_code, "E06000002")

    def test_counts_and_reconciliation_list_include_all_existing_organisations_missing_from_file(self):
        first, second = self.row(), self.row(**{"Organisation": "Second Council", "GSS code": "E06000002"})
        self.import_rows([first, second])
        missing = Organisation.objects.create(name="Legacy Without Code")
        result = self.import_rows(
            [
                first,
                {**second, "Organisation": "Renamed Council"},
                self.row(**{"Organisation": "Third Council", "GSS code": "E06000003"}),
            ]
        )
        self.assertEqual((result.created, result.updated, result.unchanged), (1, 1, 1))
        self.assertEqual([organisation.pk for organisation in result.missing_organisations], [missing.pk])

    def test_rename_cannot_collide_with_a_normalised_legacy_name_without_gss(self):
        Organisation.objects.create(name="Old Council", gss_code="E06000001", organisation_type="council")
        Organisation.objects.create(name="TEST  Council")
        before = self.snapshot()
        issues = self.errors([self.row()])
        self.assertTrue(any("name belongs to another organisation" in issue.message for issue in issues))
        self.assertEqual(self.snapshot(), before)

    def test_standard_validation_messages_match_the_actual_admin_form(self):
        admin = OrganisationAdmin(Organisation, AdminSite())
        form_class = admin.get_form(RequestFactory().get("/admin/webcaf/organisation/add/"))
        for changes, form_data in (
            ({"GSS code": "bad"}, {"gss_code": "bad"}),
            ({"GSS code": ""}, {"gss_code": ""}),
            ({"Contact email": "bad"}, {"contact_email": "bad"}),
            ({"Organisation": "x" * 256}, {"name": "x" * 256}),
        ):
            with self.subTest(changes=changes):
                data = {"name": "Test Council", "gss_code": "E06000001", "organisation_type": "council", **form_data}
                form = form_class(data=data)
                self.assertFalse(form.is_valid())
                messages = [issue.message for issue in self.errors([self.row(**changes)])]
                for errors in form.errors.values():
                    for error in errors:
                        self.assertIn(str(error), messages)


class OrganisationImporterConcurrencyTest(TransactionTestCase):
    def test_concurrent_updates_and_link_additions_do_not_duplicate_records_or_history(self):
        user = User.objects.create_user(username="importer", is_staff=True)
        organisation = Organisation.objects.create(
            name="Old Council", gss_code="E06000001", organisation_type="council"
        )
        initial_history_count = organisation.history.count()
        AllowedEmailDomain.objects.create(domain="council.gov.uk")
        attempts = [Event(), Event()]

        def run_import(attempt):
            def track_lock(execute, sql, params, many, context):
                if "FOR UPDATE" in sql and f'"{Organisation._meta.db_table}"' in sql:
                    attempt.set()
                return execute(sql, params, many, context)

            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '15s'")
                with connection.execute_wrapper(track_lock):
                    return import_organisations(
                        BytesIO(
                            b"Organisation,GSS code,Type,Domains\nRenamed Council,E06000001,Council,council.gov.uk\n"
                        ),
                        user=user,
                    )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            with transaction.atomic():
                Organisation.objects.select_for_update().get(pk=organisation.pk)
                futures = [executor.submit(run_import, attempt) for attempt in attempts]
                attempted_locks = [attempt.wait(timeout=5) for attempt in attempts]
            results = [future.result(timeout=20) for future in futures]

        self.assertEqual(attempted_locks, [True, True])
        self.assertEqual(sum(result.updated for result in results), 1)
        self.assertEqual(sum(result.unchanged for result in results), 1)
        self.assertEqual(Organisation.objects.count(), 1)
        self.assertEqual(Organisation.objects.get().name, "Renamed Council")
        self.assertEqual(OrganisationEmailDomain.objects.count(), 1)
        self.assertEqual(organisation.history.count(), initial_history_count + 1)
        self.assertEqual(OrganisationEmailDomain.history.count(), 1)
