from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from webcaf.webcaf.models import AllowedEmailDomain, Organisation, OrganisationEmailDomain


class OrganisationEmailDomainTest(TestCase):
    def setUp(self):
        self.first_organisation = Organisation.objects.create(
            name="First Council",
            organisation_type="council",
            gss_code="E06000001",
        )
        self.second_organisation = Organisation.objects.create(
            name="Second Council",
            organisation_type="council",
            gss_code="E06000002",
        )
        self.domain = AllowedEmailDomain.objects.create(domain=" Example.GOV.UK. ")

    def test_domain_can_be_linked_to_several_organisations(self):
        first_link = OrganisationEmailDomain.objects.create(
            organisation=self.first_organisation,
            domain=self.domain,
            is_primary=True,
        )
        second_link = OrganisationEmailDomain.objects.create(
            organisation=self.second_organisation,
            domain=self.domain,
            is_primary=True,
        )

        self.assertEqual(self.domain.domain, "example.gov.uk")
        self.assertEqual(set(self.domain.organisations.all()), {self.first_organisation, self.second_organisation})
        self.assertTrue(first_link.is_primary)
        self.assertTrue(second_link.is_primary)

    def test_organisation_can_have_only_one_primary_domain(self):
        OrganisationEmailDomain.objects.create(
            organisation=self.first_organisation,
            domain=self.domain,
            is_primary=True,
        )
        second_domain = AllowedEmailDomain.objects.create(domain="second.gov.uk")

        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganisationEmailDomain.objects.create(
                organisation=self.first_organisation,
                domain=second_domain,
                is_primary=True,
            )

    def test_domain_can_only_be_linked_to_an_organisation_once(self):
        OrganisationEmailDomain.objects.create(organisation=self.first_organisation, domain=self.domain)

        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganisationEmailDomain.objects.create(organisation=self.first_organisation, domain=self.domain)

    def test_retired_domain_cannot_be_primary(self):
        link = OrganisationEmailDomain(
            organisation=self.first_organisation,
            domain=self.domain,
            is_primary=True,
            status=OrganisationEmailDomain.Status.RETIRED,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "A retired domain cannot be an organisation's primary domain.",
        ):
            link.full_clean()

    def test_retiring_association_preserves_shared_domain_and_other_association(self):
        first_link = OrganisationEmailDomain.objects.create(
            organisation=self.first_organisation,
            domain=self.domain,
        )
        second_link = OrganisationEmailDomain.objects.create(
            organisation=self.second_organisation,
            domain=self.domain,
        )

        first_link.status = OrganisationEmailDomain.Status.RETIRED
        first_link.save(update_fields=["status"])

        self.assertTrue(AllowedEmailDomain.objects.filter(pk=self.domain.pk).exists())
        self.assertTrue(OrganisationEmailDomain.objects.filter(pk=second_link.pk).exists())
        self.assertTrue(AllowedEmailDomain.allows_email("user@example.gov.uk"))

    def test_removing_association_preserves_shared_domain_and_other_association(self):
        first_link = OrganisationEmailDomain.objects.create(
            organisation=self.first_organisation,
            domain=self.domain,
        )
        second_link = OrganisationEmailDomain.objects.create(
            organisation=self.second_organisation,
            domain=self.domain,
        )

        first_link.delete()

        self.assertTrue(AllowedEmailDomain.objects.filter(pk=self.domain.pk).exists())
        self.assertTrue(OrganisationEmailDomain.objects.filter(pk=second_link.pk).exists())
        self.assertTrue(AllowedEmailDomain.allows_email("user@example.gov.uk"))

    def test_records_association_history(self):
        link = OrganisationEmailDomain.objects.create(
            organisation=self.first_organisation,
            domain=self.domain,
        )
        link.status = OrganisationEmailDomain.Status.RETIRED
        link.save(update_fields=["status"])

        self.assertEqual(link.history.count(), 2)
        self.assertEqual(link.history.latest().status, OrganisationEmailDomain.Status.RETIRED)
