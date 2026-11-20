import csv
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from webcaf.webcaf.admin import OrganisationAdmin
from webcaf.webcaf.models import AllowedEmailDomain, Organisation, OrganisationEmailDomain, UserProfile
from webcaf.webcaf.utils.organisation_importer import CSV_HEADERS


class OrganisationAdminTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = OrganisationAdmin(Organisation, AdminSite())

    def test_displays_and_searches_by_gss_code(self):
        self.assertIn("gss_code", self.admin.list_display)
        self.assertIn("gss_code", self.admin.search_fields)
        matching_organisation = Organisation.objects.create(name="Matching Organisation", gss_code="E06000001")
        Organisation.objects.create(name="Other Organisation", gss_code="E06000002")
        results, _ = self.admin.get_search_results(
            self.factory.get("/admin/webcaf/organisation/"), Organisation.objects.all(), "E06000001"
        )
        self.assertEqual(list(results), [matching_organisation])

    def test_form_allows_gss_code_to_be_edited(self):
        form_class = self.admin.get_form(self.factory.get("/admin/webcaf/organisation/add/"))
        self.assertIn("gss_code", form_class.base_fields)

    def test_form_shows_clear_invalid_gss_code_error(self):
        form_class = self.admin.get_form(self.factory.get("/admin/webcaf/organisation/add/"))
        form = form_class(data={"name": "Test Organisation", "organisation_type": "other", "gss_code": "invalid"})
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["gss_code"],
            ["Enter a valid GSS code: one uppercase letter followed by eight digits (for example, E06000001)."],
        )

    def test_form_requires_gss_code_for_council(self):
        form_class = self.admin.get_form(self.factory.get("/admin/webcaf/organisation/add/"))
        form = form_class(data={"name": "Test Council", "organisation_type": "council", "gss_code": ""})
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), ["Councils and combined authorities must have a GSS code."])

    def test_form_saves_combined_authority_with_gss_code(self):
        form_class = self.admin.get_form(self.factory.get("/admin/webcaf/organisation/add/"))
        form = form_class(
            data={"name": "Test Combined Authority", "organisation_type": "combined-authority", "gss_code": "E47000001"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().gss_code, "E47000001")


class OrganisationAdminImportCSVTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin", email="admin@test.gov.uk")
        self.client.force_login(self.user)
        self.url = reverse("admin:import-org-csv")

    def upload(self, rows=None, headers=CSV_HEADERS):
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows if rows is not None else [self.row()])
        return SimpleUploadedFile("councils.csv", buffer.getvalue().encode(), content_type="text/csv")

    @staticmethod
    def row(**overrides):
        return {"Organisation": "Test Council", "GSS code": "E06000001", "Type": "Council", **overrides}

    def test_upload_page_and_download_use_the_same_schema(self):
        response = self.client.get(self.url)
        self.assertContains(response, ", ".join(CSV_HEADERS))
        self.assertContains(response, "managed separately through council onboarding")
        self.assertNotContains(response, "Provisional schema")
        self.assertContains(
            response, "Organisation type labels and classifications still require confirmation under CS-680"
        )
        response = self.client.get(reverse("admin:import-org-csv-template"))
        self.assertEqual(list(csv.reader(StringIO(response.content.decode()))), [list(CSV_HEADERS)])

    def test_success_renders_counts_and_organisation_changes(self):
        missing = Organisation.objects.create(name="Not in file")
        response = self.client.post(self.url, {"csv_file": self.upload()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 created, 0 updated, 0 unchanged")
        self.assertContains(response, "Organisation created.")
        self.assertContains(response, "Existing organisations not in this file")
        self.assertContains(response, missing.name)
        organisation = Organisation.objects.get(gss_code="E06000001")
        self.assertEqual(organisation.history.first().history_user, self.user)
        self.assertEqual(organisation.history.first().history_change_reason, "Council CSV import, row 2")

    def test_missing_upload_is_reported(self):
        self.assertContains(self.client.post(self.url), "Choose a CSV file to upload.")
        self.assertFalse(Organisation.objects.exists())

    def test_all_row_errors_are_rendered_without_partial_writes(self):
        rows = [self.row(), self.row(**{"Organisation": "Invalid Council", "GSS code": "bad", "Contact email": "bad"})]
        response = self.client.post(self.url, {"csv_file": self.upload(rows)})
        self.assertContains(response, "Row 3")
        self.assertContains(response, "Enter a valid GSS code")
        self.assertContains(response, "Enter a valid email address")
        self.assertContains(response, "Nothing was saved")
        self.assertFalse(Organisation.objects.exists())

    def test_reconciliation_candidates_link_to_admin_for_manual_review(self):
        existing = Organisation.objects.create(name="Test Council")
        response = self.client.post(self.url, {"csv_file": self.upload()})
        self.assertContains(response, "Potential existing organisation without a GSS code")
        self.assertContains(response, reverse("admin:webcaf_organisation_change", args=[existing.pk]))
        existing.refresh_from_db()
        self.assertIsNone(existing.gss_code)
        self.assertEqual(Organisation.objects.count(), 1)

    def test_admin_reconciliation_is_audited_and_following_import_updates_same_record(self):
        existing = Organisation.objects.create(name="Test Council", contact_email="keep@test.gov.uk")
        response = self.client.post(
            reverse("admin:webcaf_organisation_change", args=[existing.pk]),
            {
                "name": existing.name,
                "gss_code": "E06000001",
                "contact_email": existing.contact_email,
                "email_domain_links-TOTAL_FORMS": "0",
                "email_domain_links-INITIAL_FORMS": "0",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        history = existing.history.first()
        self.assertEqual(history.history_user, self.user)
        self.assertEqual(history.gss_code, "E06000001")
        response = self.client.post(self.url, {"csv_file": self.upload()})
        self.assertContains(response, "0 created, 1 updated, 0 unchanged")
        self.assertEqual(Organisation.objects.get(gss_code="E06000001").pk, existing.pk)

    def test_import_does_not_create_or_change_users_profiles_memberships_or_roles(self):
        organisation = Organisation.objects.create(
            name="Test Council", gss_code="E06000001", organisation_type="council"
        )
        member = User.objects.create_user(username="existing", email="existing@test.gov.uk")
        UserProfile.objects.create(user=member, organisation=organisation, role="cyber_advisor")
        users_before = list(User.objects.values().order_by("pk"))
        profiles_before = list(UserProfile.objects.values().order_by("pk"))
        response = self.client.post(
            self.url,
            {
                "csv_file": self.upload(
                    [self.row(), self.row(**{"Organisation": "New Council", "GSS code": "E06000002"})]
                )
            },
        )
        self.assertContains(response, "1 created, 0 updated, 1 unchanged")
        self.assertEqual(list(User.objects.values().order_by("pk")), users_before)
        self.assertEqual(list(UserProfile.objects.values().order_by("pk")), profiles_before)

    def test_false_positive_shared_domain_is_resolved_by_audited_manual_creation(self):
        domain = AllowedEmailDomain.objects.create(domain="shared.gov.uk")
        legacy = Organisation.objects.create(name="Unrelated Legacy Body")
        OrganisationEmailDomain.objects.create(organisation=legacy, domain=domain)
        rows = [self.row(Domains=domain.domain)]
        response = self.client.post(self.url, {"csv_file": self.upload(rows)})
        self.assertContains(response, "Potential existing organisation without a GSS code")

        response = self.client.post(
            reverse("admin:webcaf_organisation_add"),
            {
                "name": "Test Council",
                "gss_code": "E06000001",
                "organisation_type": "council",
                "email_domain_links-TOTAL_FORMS": "0",
                "email_domain_links-INITIAL_FORMS": "0",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        council = Organisation.objects.get(gss_code="E06000001")
        self.assertEqual(council.history.first().history_user, self.user)
        response = self.client.post(self.url, {"csv_file": self.upload(rows)})
        self.assertContains(response, "0 created, 1 updated, 0 unchanged")
        self.assertEqual(Organisation.objects.count(), 2)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.gss_code)
        self.assertEqual(OrganisationEmailDomain.objects.count(), 2)

    def test_legacy_columns_are_rejected_without_creating_users_or_organisations(self):
        row = self.row(**{"Email1": "legacy@test.gov.uk", "Reference": "REF01", "Lead Government Department": "Legacy"})
        response = self.client.post(
            self.url,
            {
                "csv_file": self.upload(
                    [row], headers=(*CSV_HEADERS, "Email1", "Reference", "Lead Government Department")
                )
            },
        )
        self.assertContains(response, "Unsupported legacy columns: Email1, Reference, Lead Government Department")
        self.assertContains(response, "Expected CSV schema")
        self.assertFalse(Organisation.objects.exists())
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserProfile.objects.exists())

    def test_domain_link_history_records_the_importing_user(self):
        domain = AllowedEmailDomain.objects.create(domain="council.gov.uk")
        response = self.client.post(self.url, {"csv_file": self.upload([self.row(Domains=domain.domain)])})
        self.assertContains(response, "1 created")
        self.assertEqual(OrganisationEmailDomain.history.first().history_user, self.user)

    def test_import_logs_never_include_contact_email_or_database_error_details(self):
        email = "private.person@test.gov.uk"
        with self.assertLogs("OrganisationAdmin", level="INFO") as captured:
            self.client.post(self.url, {"csv_file": self.upload([self.row(**{"Contact email": email})])})
        self.assertNotIn(email, " ".join(captured.output))

        with patch.object(Organisation, "save", side_effect=IntegrityError(f"Failing row contains {email}")):
            with self.assertLogs("OrganisationAdmin", level="WARNING") as captured:
                response = self.client.post(
                    self.url,
                    {
                        "csv_file": self.upload(
                            [self.row(**{"Organisation": "Changed Council", "Contact email": email})]
                        )
                    },
                )
        self.assertContains(response, "Nothing was saved")
        self.assertNotContains(response, email)
        self.assertNotIn(email, " ".join(captured.output))

    def test_rejected_upload_does_not_log_email_addresses_even_in_other_columns(self):
        email = "private.person@test.gov.uk"
        with self.assertLogs("OrganisationAdmin", level="WARNING") as captured:
            response = self.client.post(
                self.url,
                {
                    "csv_file": self.upload(
                        [self.row(**{"Organisation": email, "GSS code": "invalid", "Contact email": email})]
                    )
                },
            )
        self.assertContains(response, "Nothing was saved")
        self.assertNotIn(email, " ".join(captured.output))


class OrganisationImportPermissionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="staff", is_staff=True)
        self.client.force_login(self.user)
        self.upload_url = reverse("admin:import-org-csv")
        self.template_url = reverse("admin:import-org-csv-template")

    def grant(self, codename):
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="webcaf", codename=codename))

    def test_staff_without_add_permission_cannot_open_upload_download_or_execute_import(self):
        for url, method in ((self.upload_url, "get"), (self.upload_url, "post"), (self.template_url, "get")):
            with self.subTest(url=url, method=method):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertFalse(Organisation.objects.exists())

    def test_add_permission_alone_allows_import_including_updates(self):
        self.grant("add_organisation")
        self.assertEqual(self.client.get(self.upload_url).status_code, 200)
        self.assertEqual(self.client.get(self.template_url).status_code, 200)
        for name in ("Council", "Renamed Council"):
            response = self.client.post(
                self.upload_url,
                {
                    "csv_file": SimpleUploadedFile(
                        "councils.csv", f"Organisation,GSS code,Type\n{name},E06000001,Council\n".encode()
                    )
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(Organisation.objects.get(gss_code="E06000001").name, name)
        self.assertEqual(Organisation.objects.count(), 1)

    def test_import_link_is_hidden_without_add_permission(self):
        self.grant("view_organisation")
        response = self.client.get(reverse("admin:webcaf_organisation_changelist"))
        self.assertNotContains(response, 'href="import-org-csv/"')
        self.grant("add_organisation")
        response = self.client.get(reverse("admin:webcaf_organisation_changelist"))
        self.assertContains(response, 'href="import-org-csv/"')

    def test_nonstaff_and_inactive_users_with_add_permission_are_refused(self):
        self.grant("add_organisation")
        for flags in ({"is_staff": False}, {"is_staff": True, "is_active": False}):
            User.objects.filter(pk=self.user.pk).update(**flags)
            self.user.refresh_from_db()
            self.client.force_login(self.user)
            for url in (self.upload_url, self.template_url):
                self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(self.upload_url).status_code, 302)
        self.assertFalse(Organisation.objects.exists())
