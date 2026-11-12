from django.core.exceptions import ValidationError
from django.test import TestCase

from webcaf.webcaf.models import Organisation


class OrganisationTest(TestCase):
    def test_saves_valid_gss_code(self):
        organisation = Organisation.objects.create(
            name="Test Council",
            organisation_type="council",
            gss_code="E06000001",
        )

        self.assertEqual(organisation.gss_code, "E06000001")

    def test_rejects_invalid_gss_code_format(self):
        invalid_codes = ["06000001", "EE0600001", "E0600000", "E060000001", "e06000001", "E0600000A"]

        for invalid_code in invalid_codes:
            with self.subTest(gss_code=invalid_code):
                with self.assertRaises(ValidationError) as context:
                    Organisation.objects.create(name=f"Council {invalid_code}", gss_code=invalid_code)

                self.assertIn(
                    "Enter a valid GSS code: one uppercase letter followed by eight digits (for example, E06000001).",
                    context.exception.message_dict["gss_code"],
                )

    def test_rejects_duplicate_gss_code(self):
        Organisation.objects.create(name="First Council", gss_code="E06000001")

        with self.assertRaises(ValidationError) as context:
            Organisation.objects.create(name="Second Council", gss_code="E06000001")

        self.assertEqual(
            context.exception.message_dict["gss_code"],
            ["Organisation with this GSS code already exists."],
        )

    def test_requires_gss_code_for_councils_and_combined_authorities(self):
        for organisation_type in Organisation.GSS_CODE_REQUIRED_ORGANISATION_TYPES:
            with self.subTest(organisation_type=organisation_type):
                with self.assertRaises(ValidationError) as context:
                    Organisation.objects.create(
                        name=f"Organisation {organisation_type}",
                        organisation_type=organisation_type,
                    )

                self.assertEqual(
                    context.exception.messages,
                    ["Councils and combined authorities must have a GSS code."],
                )

    def test_allows_other_organisation_types_without_gss_code(self):
        organisation = Organisation.objects.create(
            name="Assurance Company",
            organisation_type="other",
        )

        self.assertIsNone(organisation.gss_code)

    def test_rejects_existing_council_without_gss_code_on_next_save(self):
        organisation = Organisation(name="Existing Council", organisation_type="council")
        Organisation.objects.bulk_create([organisation])

        organisation.name = "Renamed Existing Council"

        with self.assertRaises(ValidationError) as context:
            organisation.save()

        self.assertEqual(
            context.exception.messages,
            ["Councils and combined authorities must have a GSS code."],
        )

    def test_stores_empty_optional_gss_codes_as_null(self):
        first = Organisation.objects.create(name="First Organisation", gss_code="")
        second = Organisation.objects.create(name="Second Organisation", gss_code="")

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertIsNone(first.gss_code)
        self.assertIsNone(second.gss_code)

    def test_generated_reference_is_unchanged(self):
        organisation = Organisation.objects.create(name="Test Organisation", gss_code="E06000001")
        generated_reference = organisation.reference

        organisation.name = "Renamed Organisation"
        organisation.save()

        self.assertIsNotNone(generated_reference)
        self.assertEqual(organisation.reference, generated_reference)

    def test_records_gss_code_in_history(self):
        organisation = Organisation.objects.create(name="Test Organisation", gss_code="E06000001")

        self.assertEqual(organisation.history.latest().gss_code, "E06000001")
