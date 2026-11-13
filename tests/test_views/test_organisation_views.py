from django.test import TestCase

from webcaf.webcaf.models import Organisation
from webcaf.webcaf.views.organisation import OrganisationContactForm, OrganisationTypeForm


class OrganisationTypeFormTest(TestCase):
    def test_does_not_offer_admin_managed_types_to_other_organisations(self):
        organisation = Organisation.objects.create(name="Test Organisation", organisation_type="other")

        form = OrganisationTypeForm(instance=organisation)
        choice_ids = {choice[0] for choice in form.fields["organisation_type"].choices}

        self.assertTrue(choice_ids.isdisjoint(Organisation.GSS_CODE_REQUIRED_ORGANISATION_TYPES))

    def test_existing_council_can_only_retain_its_admin_managed_type(self):
        organisation = Organisation.objects.create(
            name="Test Council",
            organisation_type="council",
            gss_code="E06000001",
        )

        form = OrganisationTypeForm(instance=organisation)

        self.assertEqual(list(form.fields["organisation_type"].choices), [("council", "Council")])

    def test_existing_council_without_gss_code_returns_form_error(self):
        organisation = Organisation(name="Existing Council", organisation_type="council")
        Organisation.objects.bulk_create([organisation])

        form = OrganisationContactForm(
            data={
                "contact_name": "Test Contact",
                "contact_role": "Test Role",
                "contact_email": "contact@example.gov.uk",
            },
            instance=organisation,
        )

        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.non_field_errors(),
            ["Councils and combined authorities must have a GSS code."],
        )
