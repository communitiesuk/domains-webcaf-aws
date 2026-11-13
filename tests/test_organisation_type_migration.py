import importlib

from django.apps import apps
from django.test import TestCase

from webcaf.webcaf.models import Organisation

migration = importlib.import_module("webcaf.webcaf.migrations.0038_organisation_types_council_combined_authority_other")


class OrganisationTypeMigrationTest(TestCase):
    """
    Exercises the data step of migration 0038 against the current models.
    Organisation.objects.create() validates only the GSS code, so rows with a
    removed type can still be created here, as they exist in deployed databases.
    """

    def create_organisation(self, name, organisation_type, **kwargs):
        return Organisation.objects.create(name=name, organisation_type=organisation_type, **kwargs)

    def test_moves_every_removed_type_to_other(self):
        for index, removed_type in enumerate(migration.REMOVED_ORGANISATION_TYPES):
            self.create_organisation(f"Organisation {index}", removed_type)

        migration.set_removed_types_to_other(apps, None)

        self.assertSetEqual(set(Organisation.objects.values_list("organisation_type", flat=True)), {"other"})

    def test_keeps_retained_and_empty_types(self):
        council = self.create_organisation("Test Council", "council", gss_code="E06000001")
        combined_authority = self.create_organisation(
            "Test Combined Authority", "combined-authority", gss_code="E47000001"
        )
        other = self.create_organisation("Test Company", "other")
        no_type = self.create_organisation("Untyped Organisation", None)

        migration.set_removed_types_to_other(apps, None)

        for organisation, expected_type in [
            (council, "council"),
            (combined_authority, "combined-authority"),
            (other, "other"),
            (no_type, None),
        ]:
            with self.subTest(organisation=organisation.name):
                organisation.refresh_from_db()
                self.assertEqual(organisation.organisation_type, expected_type)
                self.assertFalse(
                    organisation.history.filter(
                        history_change_reason__startswith=migration.CHANGE_REASON_PREFIX
                    ).exists()
                )

    def test_records_the_removed_type_in_history(self):
        organisation = self.create_organisation("Cabinet Office", "ministerial-department")

        migration.set_removed_types_to_other(apps, None)

        latest = organisation.history.latest()
        self.assertEqual(latest.organisation_type, "other")
        self.assertEqual(latest.history_type, "~")
        self.assertEqual(latest.history_change_reason, "CS-680 removed organisation type: ministerial-department")
        self.assertEqual(organisation.history.earliest().organisation_type, "ministerial-department")

    def test_keeps_reference_and_other_fields(self):
        organisation = self.create_organisation(
            "Cabinet Office",
            "ministerial-department",
            contact_name="Test Contact",
            contact_role="Test Role",
            contact_email="contact@example.gov.uk",
        )
        before = Organisation.objects.values().get(pk=organisation.pk)

        migration.set_removed_types_to_other(apps, None)

        after = Organisation.objects.values().get(pk=organisation.pk)
        self.assertIsNotNone(before["reference"])
        self.assertEqual(after, {**before, "organisation_type": "other"})
        self.assertEqual(organisation.history.latest().reference, before["reference"])

    def test_reverse_restores_removed_types_and_removes_its_history(self):
        organisation = self.create_organisation("Cabinet Office", "ministerial-department")
        migration.set_removed_types_to_other(apps, None)

        migration.restore_removed_types(apps, None)

        organisation.refresh_from_db()
        self.assertEqual(organisation.organisation_type, "ministerial-department")
        self.assertFalse(
            organisation.history.filter(history_change_reason__startswith=migration.CHANGE_REASON_PREFIX).exists()
        )

    def test_reverse_keeps_a_type_changed_after_the_migration(self):
        organisation = self.create_organisation("Cabinet Office", "ministerial-department")
        migration.set_removed_types_to_other(apps, None)
        Organisation.objects.filter(pk=organisation.pk).update(organisation_type="council", gss_code="E06000001")

        migration.restore_removed_types(apps, None)

        organisation.refresh_from_db()
        self.assertEqual(organisation.organisation_type, "council")
