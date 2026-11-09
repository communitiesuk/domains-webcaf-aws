import csv
from io import BytesIO, StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from webcaf.webcaf.admin import OrganisationAdmin
from webcaf.webcaf.models import Organisation, UserProfile


def add_middleware(request):
    """Add the session and message middleware required by the admin view."""

    def get_response(request):
        return HttpResponse()

    session_middleware = SessionMiddleware(get_response)
    session_middleware.process_request(request)
    request.session.save()

    message_middleware = MessageMiddleware(get_response)
    message_middleware.process_request(request)
    return request


class OrganisationAdminImportCSVTest(TestCase):
    """Tests for the OrganisationAdmin import_csv method."""

    supported_headers = ["Organisation", "Lead Government Department", "Reference", "Type"]
    legacy_email_headers = ["Email1", "Email2", "Email3", "Email4", "Email5", "Email6"]

    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.admin = OrganisationAdmin(Organisation, self.site)
        self.superuser = User.objects.create_superuser(
            username="admin@test.gov.uk",
            email="admin@test.gov.uk",
            password="testpass123",  # pragma: allowlist secret
        )

    def create_csv_file(self, rows, headers=None):
        csv_buffer = StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=headers or self.supported_headers)
        writer.writeheader()
        writer.writerows(rows)
        csv_buffer.seek(0)
        csv_bytes = BytesIO(csv_buffer.getvalue().encode("utf-8"))
        return InMemoryUploadedFile(
            csv_bytes,
            "file",
            "test.csv",
            "text/csv",
            csv_bytes.getbuffer().nbytes,
            None,
        )

    def import_rows(self, rows, headers=None):
        csv_file = self.create_csv_file(rows, headers)
        request = self.factory.post("/admin/webcaf/organisation/import-org-csv/", {"csv_file": csv_file})
        request.user = self.superuser
        request = add_middleware(request)
        response = self.admin.import_csv(request)
        return response, [str(message) for message in get_messages(request)]

    @staticmethod
    def row(
        organisation="Test Organisation",
        lead_government_department="",
        reference="ORG001",
        organisation_type="",
    ):
        return {
            "Organisation": organisation,
            "Lead Government Department": lead_government_department,
            "Reference": reference,
            "Type": organisation_type,
        }

    def test_import_csv_get_request_renders_supported_schema(self):
        request = self.factory.get("/admin/webcaf/organisation/import-org-csv/")
        request.user = self.superuser

        response = self.admin.import_csv(add_middleware(request))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Organisation, Lead Government Department, Reference, Type")
        self.assertNotContains(response, "Email1")
        self.assertContains(response, "managed separately through council onboarding")

    def test_import_csv_template_uses_supported_schema(self):
        request = self.factory.get("/admin/webcaf/organisation/import-org-csv-template/")
        request.user = self.superuser

        response = self.admin.import_csv_template(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode(),
            '"Organisation","Lead Government Department","Reference","Type"\r\n',
        )

    def test_valid_import_creates_organisation_without_users_profiles_memberships_or_roles(self):
        users_before = list(User.objects.values("id", "username", "email", "is_active"))

        response, _messages = self.import_rows([self.row()])

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Organisation.objects.filter(name="Test Organisation").exists())
        self.assertEqual(list(User.objects.values("id", "username", "email", "is_active")), users_before)
        self.assertEqual(UserProfile.objects.count(), 0)

    def test_valid_import_preserves_existing_user_profile_membership_and_role(self):
        organisation = Organisation.objects.create(name="Existing Organisation", reference="ORG001")
        member = User.objects.create_user(
            username="existing@test.gov.uk",
            email="existing@test.gov.uk",
            first_name="Existing",
            last_name="Advisor",
        )
        profile = UserProfile.objects.create(
            user=member,
            organisation=organisation,
            role="cyber_advisor",
        )
        user_before = User.objects.values("username", "email", "first_name", "last_name", "is_active", "is_staff").get(
            pk=member.pk
        )
        profile_before = UserProfile.objects.values("user_id", "organisation_id", "role").get(pk=profile.pk)
        user_count_before = User.objects.count()
        profile_count_before = UserProfile.objects.count()

        self.import_rows([self.row(organisation="Existing Organisation", reference="ORG001")])

        self.assertEqual(User.objects.count(), user_count_before)
        self.assertEqual(UserProfile.objects.count(), profile_count_before)
        self.assertEqual(
            User.objects.values("username", "email", "first_name", "last_name", "is_active", "is_staff").get(
                pk=member.pk
            ),
            user_before,
        )
        self.assertEqual(
            UserProfile.objects.values("user_id", "organisation_id", "role").get(pk=profile.pk),
            profile_before,
        )

    def test_legacy_email_columns_are_rejected_without_storing_addresses_or_changing_records(self):
        organisation = Organisation.objects.create(name="Existing Organisation", reference="ORG001")
        member = User.objects.create_user(username="existing@test.gov.uk", email="existing@test.gov.uk")
        profile = UserProfile.objects.create(
            user=member,
            organisation=organisation,
            role="cyber_advisor",
        )
        legacy_emails = [f"legacy{i}@test.gov.uk" for i in range(1, 7)]
        headers = self.supported_headers + self.legacy_email_headers
        legacy_row = self.row(organisation="Rejected Organisation", reference="ORG999")
        legacy_row.update(dict(zip(self.legacy_email_headers, legacy_emails, strict=True)))
        organisation_ids_before = list(Organisation.objects.values_list("id", flat=True))
        user_before = User.objects.values("username", "email", "is_active").get(pk=member.pk)
        profile_before = UserProfile.objects.values("user_id", "organisation_id", "role").get(pk=profile.pk)

        response, admin_messages = self.import_rows([legacy_row], headers=headers)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(admin_messages), 1)
        self.assertIn(
            "Unsupported legacy email columns: Email1, Email2, Email3, Email4, Email5, Email6.",
            admin_messages[0],
        )
        self.assertIn(
            "Use the agreed council CSV schema: Organisation, Lead Government Department, Reference, Type.",
            admin_messages[0],
        )
        self.assertEqual(list(Organisation.objects.values_list("id", flat=True)), organisation_ids_before)
        self.assertFalse(Organisation.objects.filter(name="Rejected Organisation").exists())
        self.assertFalse(Organisation.objects.filter(contact_email__in=legacy_emails).exists())
        self.assertFalse(User.objects.filter(email__in=legacy_emails).exists())
        self.assertEqual(User.objects.values("username", "email", "is_active").get(pk=member.pk), user_before)
        self.assertEqual(
            UserProfile.objects.values("user_id", "organisation_id", "role").get(pk=profile.pk),
            profile_before,
        )

    def test_each_legacy_email_column_is_rejected(self):
        for legacy_header in self.legacy_email_headers:
            with self.subTest(legacy_header=legacy_header):
                headers = self.supported_headers + [legacy_header]
                row = self.row()
                row[legacy_header] = "legacy@test.gov.uk"

                _response, admin_messages = self.import_rows([row], headers=headers)

                self.assertIn(f"Unsupported legacy email columns: {legacy_header}.", admin_messages[0])
                self.assertEqual(Organisation.objects.count(), 0)
                self.assertFalse(User.objects.filter(email="legacy@test.gov.uk").exists())
                self.assertEqual(UserProfile.objects.count(), 0)

    def test_other_invalid_headers_are_rejected_against_agreed_schema(self):
        headers = ["Organisation", "Reference"]
        row = {"Organisation": "Rejected Organisation", "Reference": "ORG999"}

        _response, admin_messages = self.import_rows([row], headers=headers)

        self.assertIn(
            "The CSV headers must match the agreed council CSV schema: "
            "Organisation, Lead Government Department, Reference, Type.",
            admin_messages[0],
        )
        self.assertEqual(Organisation.objects.count(), 0)

    def test_duplicate_headers_are_rejected_before_importing_rows(self):
        headers = self.supported_headers + ["Organisation", "Type"]

        _response, admin_messages = self.import_rows([self.row()], headers=headers)

        self.assertIn("The CSV contains duplicate headers: Organisation, Type.", admin_messages[0])
        self.assertIn(
            "Use the agreed council CSV schema: Organisation, Lead Government Department, Reference, Type.",
            admin_messages[0],
        )
        self.assertEqual(Organisation.objects.count(), 0)

    def test_import_csv_finds_existing_organisation_by_reference(self):
        Organisation.objects.create(name="Existing Org", reference="ORG001")

        self.import_rows([self.row(organisation="Different Name", reference="ORG001")])

        self.assertEqual(Organisation.objects.count(), 1)

    def test_import_csv_finds_existing_organisation_by_name(self):
        Organisation.objects.create(name="Existing Org")

        self.import_rows([self.row(organisation="Existing Org", reference="")])

        self.assertEqual(Organisation.objects.count(), 1)

    def test_import_csv_sets_parent_organisation(self):
        rows = [
            self.row(organisation="Parent Organisation", reference="PARENT001"),
            self.row(
                organisation="Child Organisation",
                lead_government_department="Parent Organisation",
                reference="CHILD001",
            ),
        ]

        self.import_rows(rows)

        parent_org = Organisation.objects.get(name="Parent Organisation")
        child_org = Organisation.objects.get(name="Child Organisation")
        self.assertIsNone(parent_org.parent_organisation)
        self.assertEqual(child_org.parent_organisation, parent_org)

    def test_import_csv_parent_organisation_is_self(self):
        self.import_rows(
            [
                self.row(
                    organisation="Self Organisation",
                    lead_government_department="Self Organisation",
                )
            ]
        )

        self.assertIsNone(Organisation.objects.get(name="Self Organisation").parent_organisation)

    def test_import_csv_processes_multiple_rows(self):
        rows = [self.row(organisation=f"Organisation {number}", reference=f"ORG00{number}") for number in range(1, 4)]

        self.import_rows(rows)

        self.assertEqual(Organisation.objects.count(), 3)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(UserProfile.objects.count(), 0)

    def test_find_organisation_by_reference(self):
        organisation = Organisation.objects.create(name="Test Org", reference="ORG001")

        found_organisation = self.admin.find_organisation({"Reference": "ORG001", "Organisation": "Test Org"})

        self.assertEqual(found_organisation, organisation)

    def test_find_organisation_by_name_when_no_reference(self):
        organisation = Organisation.objects.create(name="Test Org")

        found_organisation = self.admin.find_organisation({"Reference": "", "Organisation": "Test Org"})

        self.assertEqual(found_organisation, organisation)

    def test_find_organisation_returns_none_when_not_found(self):
        found_organisation = self.admin.find_organisation(
            {"Reference": "NONEXISTENT", "Organisation": "Nonexistent Org"}
        )

        self.assertIsNone(found_organisation)

    def test_find_organisation_prefers_reference_over_name(self):
        organisation_by_reference = Organisation.objects.create(name="Org By Ref", reference="ORG001")
        Organisation.objects.create(name="Org By Name")

        found_organisation = self.admin.find_organisation({"Reference": "ORG001", "Organisation": "Org By Name"})

        self.assertEqual(found_organisation, organisation_by_reference)

    def test_import_csv_sets_organisation_type_when_provided(self):
        self.import_rows([self.row(organisation="Test Ministry", organisation_type="Ministerial department")])

        self.assertEqual(Organisation.objects.get(name="Test Ministry").organisation_type, "ministerial-department")

    def test_import_csv_ignores_invalid_organisation_type(self):
        self.import_rows([self.row(organisation_type="Invalid Type")])

        self.assertIsNone(Organisation.objects.get(name="Test Organisation").organisation_type)

    def test_import_csv_handles_empty_type_field(self):
        self.import_rows([self.row()])

        self.assertIsNone(Organisation.objects.get(name="Test Organisation").organisation_type)

    def test_import_csv_handles_whitespace_in_type(self):
        self.import_rows([self.row(organisation="Test Agency", organisation_type="  Executive agency  ")])

        self.assertEqual(Organisation.objects.get(name="Test Agency").organisation_type, "executive-agency")
