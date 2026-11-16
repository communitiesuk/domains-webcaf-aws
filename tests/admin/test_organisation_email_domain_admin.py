from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission, User
from django.forms.models import inlineformset_factory
from django.test import RequestFactory, TestCase
from django.urls import reverse

from webcaf.webcaf.admin import (
    AllowedEmailDomainAdmin,
    OrganisationAdmin,
    OrganisationEmailDomainAdmin,
    OrganisationEmailDomainAdminForm,
    OrganisationEmailDomainInline,
    OrganisationEmailDomainInlineFormSet,
)
from webcaf.webcaf.models import AllowedEmailDomain, Organisation, OrganisationEmailDomain


class OrganisationEmailDomainInlineTest(TestCase):
    def setUp(self):
        self.organisation = Organisation.objects.create(
            name="Test Council",
            organisation_type="council",
            gss_code="E06000001",
        )
        self.first_domain = AllowedEmailDomain.objects.create(domain="first.gov.uk")
        self.second_domain = AllowedEmailDomain.objects.create(domain="second.gov.uk")
        self.formset_class = inlineformset_factory(
            Organisation,
            OrganisationEmailDomain,
            form=OrganisationEmailDomainAdminForm,
            formset=OrganisationEmailDomainInlineFormSet,
            fields=["domain", "is_primary", "status"],
            extra=0,
            can_delete=True,
        )

    def formset_data(self, links):
        data = {
            "domains-TOTAL_FORMS": str(len(links)),
            "domains-INITIAL_FORMS": str(sum(link.pk is not None for link in links)),
            "domains-MIN_NUM_FORMS": "0",
            "domains-MAX_NUM_FORMS": "1000",
        }
        for index, link in enumerate(links):
            data[f"domains-{index}-id"] = str(link.pk or "")
            data[f"domains-{index}-organisation"] = str(self.organisation.pk)
            data[f"domains-{index}-domain"] = str(link.domain_id)
            data[f"domains-{index}-status"] = link.status
            if link.is_primary:
                data[f"domains-{index}-is_primary"] = "on"
        return data

    def test_organisation_admin_contains_domain_inline(self):
        model_admin = OrganisationAdmin(Organisation, AdminSite())

        self.assertIn(OrganisationEmailDomainInline, model_admin.inlines)

    def test_rejects_two_primary_domains_with_clear_error(self):
        first_link = OrganisationEmailDomain(
            organisation=self.organisation,
            domain=self.first_domain,
            is_primary=True,
        )
        second_link = OrganisationEmailDomain(
            organisation=self.organisation,
            domain=self.second_domain,
            is_primary=True,
        )
        formset = self.formset_class(
            data=self.formset_data([first_link, second_link]),
            instance=self.organisation,
            prefix="domains",
            can_change_existing_links=True,
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("at most one primary email domain", str(formset.non_form_errors()))

    def test_switches_primary_domain_in_one_submission(self):
        first_link = OrganisationEmailDomain.objects.create(
            organisation=self.organisation,
            domain=self.first_domain,
            is_primary=True,
        )
        second_link = OrganisationEmailDomain.objects.create(
            organisation=self.organisation,
            domain=self.second_domain,
        )
        first_link.is_primary = False
        second_link.is_primary = True
        formset = self.formset_class(
            data=self.formset_data([first_link, second_link]),
            instance=self.organisation,
            prefix="domains",
            can_change_existing_links=True,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        first_link.refresh_from_db()
        second_link.refresh_from_db()
        self.assertFalse(first_link.is_primary)
        self.assertTrue(second_link.is_primary)
        # The explicit pre-save demotion and the submitted form save are both
        # audited. Complete history is more important than deduplicating these
        # two records.
        self.assertEqual(first_link.history.count(), 3)
        self.assertFalse(first_link.history.latest().is_primary)

    def test_switch_records_history_when_existing_primary_is_not_in_submitted_forms(self):
        first_link = OrganisationEmailDomain.objects.create(
            organisation=self.organisation,
            domain=self.first_domain,
            is_primary=True,
        )
        second_link = OrganisationEmailDomain(
            organisation=self.organisation,
            domain=self.second_domain,
            is_primary=True,
        )
        formset = self.formset_class(
            data=self.formset_data([second_link]),
            instance=self.organisation,
            prefix="domains",
            queryset=OrganisationEmailDomain.objects.none(),
            can_change_existing_links=True,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        formset.save()

        first_link.refresh_from_db()
        self.assertFalse(first_link.is_primary)
        self.assertFalse(first_link.history.latest().is_primary)


class OrganisationEmailDomainAdminTest(TestCase):
    def test_saving_primary_demotes_existing_primary_and_records_history(self):
        organisation = Organisation.objects.create(
            name="Test Council",
            organisation_type="council",
            gss_code="E06000001",
        )
        first_domain = AllowedEmailDomain.objects.create(domain="first.gov.uk")
        second_domain = AllowedEmailDomain.objects.create(domain="second.gov.uk")
        existing_primary = OrganisationEmailDomain.objects.create(
            organisation=organisation,
            domain=first_domain,
            is_primary=True,
        )
        new_primary = OrganisationEmailDomain(
            organisation=organisation,
            domain=second_domain,
            is_primary=True,
        )
        model_admin = OrganisationEmailDomainAdmin(OrganisationEmailDomain, AdminSite())
        request = RequestFactory().post("/admin/webcaf/organisationemaildomain/add/")
        request.user = User.objects.create_superuser(username="admin", email="admin@example.com")

        model_admin.save_model(request, new_primary, form=None, change=False)

        existing_primary.refresh_from_db()
        self.assertFalse(existing_primary.is_primary)
        self.assertFalse(existing_primary.history.latest().is_primary)


class OrganisationEmailDomainAdminPermissionTest(TestCase):
    def setUp(self):
        self.organisation = Organisation.objects.create(
            name="Test Council",
            organisation_type="council",
            gss_code="E06000001",
        )
        self.first_domain = AllowedEmailDomain.objects.create(domain="first.gov.uk")
        self.second_domain = AllowedEmailDomain.objects.create(domain="second.gov.uk")
        self.existing_primary = OrganisationEmailDomain.objects.create(
            organisation=self.organisation,
            domain=self.first_domain,
            is_primary=True,
        )
        self.user = User.objects.create_user(
            username="link-manager",
            email="link-manager@example.com",
            is_staff=True,
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="webcaf",
                codename__in=[
                    "change_organisation",
                    "add_organisationemaildomain",
                    "view_allowedemaildomain",
                ],
            )
        )
        self.client.force_login(self.user)

    def grant_link_change_permission(self):
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="webcaf",
                codename="change_organisationemaildomain",
            )
        )

    def standalone_add_data(self):
        return {
            "organisation": str(self.organisation.pk),
            "domain": str(self.second_domain.pk),
            "is_primary": "on",
            "status": OrganisationEmailDomain.Status.ACTIVE,
            "_save": "Save",
        }

    def organisation_change_data(self):
        return {
            "name": self.organisation.name,
            "gss_code": self.organisation.gss_code,
            "organisation_type": self.organisation.organisation_type,
            "contact_name": "",
            "contact_email": "",
            "contact_role": "",
            "parent_organisation": "",
            "email_domain_links-TOTAL_FORMS": "1",
            "email_domain_links-INITIAL_FORMS": "0",
            "email_domain_links-MIN_NUM_FORMS": "0",
            "email_domain_links-MAX_NUM_FORMS": "1000",
            "email_domain_links-0-domain": str(self.second_domain.pk),
            "email_domain_links-0-is_primary": "on",
            "email_domain_links-0-status": OrganisationEmailDomain.Status.ACTIVE,
            "_save": "Save",
        }

    def assert_primary_is_unchanged(self):
        self.existing_primary.refresh_from_db()
        self.assertTrue(self.existing_primary.is_primary)
        self.assertFalse(
            OrganisationEmailDomain.objects.filter(
                organisation=self.organisation,
                domain=self.second_domain,
            ).exists()
        )

    def test_standalone_add_cannot_demote_primary_without_change_permission(self):
        response = self.client.post(
            reverse("admin:webcaf_organisationemaildomain_add"),
            self.standalone_add_data(),
        )

        self.assertEqual(response.status_code, 403)
        self.assert_primary_is_unchanged()

    def test_standalone_add_can_demote_primary_with_change_permission(self):
        self.grant_link_change_permission()

        response = self.client.post(
            reverse("admin:webcaf_organisationemaildomain_add"),
            self.standalone_add_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.existing_primary.refresh_from_db()
        self.assertFalse(self.existing_primary.is_primary)
        self.assertTrue(
            OrganisationEmailDomain.objects.filter(
                organisation=self.organisation,
                domain=self.second_domain,
                is_primary=True,
            ).exists()
        )

    def test_inline_add_cannot_demote_omitted_primary_without_change_permission(self):
        response = self.client.post(
            reverse("admin:webcaf_organisation_change", args=[self.organisation.pk]),
            self.organisation_change_data(),
        )

        self.assertEqual(response.status_code, 403)
        self.assert_primary_is_unchanged()

    def test_inline_add_can_demote_primary_with_change_permission(self):
        self.grant_link_change_permission()

        response = self.client.post(
            reverse("admin:webcaf_organisation_change", args=[self.organisation.pk]),
            self.organisation_change_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.existing_primary.refresh_from_db()
        self.assertFalse(self.existing_primary.is_primary)
        self.assertTrue(
            OrganisationEmailDomain.objects.filter(
                organisation=self.organisation,
                domain=self.second_domain,
                is_primary=True,
            ).exists()
        )


class AllowedEmailDomainAdminPermissionTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = AllowedEmailDomainAdmin(AllowedEmailDomain, AdminSite())
        self.user = User.objects.create_user(
            username="domain-manager",
            email="domain-manager@example.com",
            is_staff=True,
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="webcaf",
                codename__in=[
                    "add_organisationemaildomain",
                    "change_organisationemaildomain",
                    "delete_organisationemaildomain",
                    "view_allowedemaildomain",
                ],
            )
        )
        self.client.force_login(self.user)

    def request_for(self, user):
        request = self.factory.get("/admin/webcaf/allowedemaildomain/")
        request.user = user
        return request

    def test_association_permissions_do_not_authorise_global_allowlist_changes(self):
        request = self.request_for(self.user)

        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request))

    def test_global_allowlist_permission_explicitly_authorises_addition(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="webcaf", codename="add_allowedemaildomain")
        )
        refreshed_user = User.objects.get(pk=self.user.pk)

        self.assertTrue(self.model_admin.has_add_permission(self.request_for(refreshed_user)))

    def test_admin_add_endpoint_rejects_user_without_global_allowlist_permission(self):
        response = self.client.post(
            reverse("admin:webcaf_allowedemaildomain_add"),
            {"domain": "new.gov.uk", "_save": "Save"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(AllowedEmailDomain.objects.filter(domain="new.gov.uk").exists())

    def test_admin_add_endpoint_allows_user_with_global_allowlist_permission(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="webcaf", codename="add_allowedemaildomain")
        )

        response = self.client.post(
            reverse("admin:webcaf_allowedemaildomain_add"),
            {"domain": "new.gov.uk", "_save": "Save"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(AllowedEmailDomain.objects.filter(domain="new.gov.uk").exists())

    def test_global_allowlist_permissions_explicitly_authorise_changes_and_deletion(self):
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="webcaf",
                codename__in=["change_allowedemaildomain", "delete_allowedemaildomain"],
            )
        )
        refreshed_user = User.objects.get(pk=self.user.pk)
        request = self.request_for(refreshed_user)

        self.assertTrue(self.model_admin.has_change_permission(request))
        self.assertTrue(self.model_admin.has_delete_permission(request))

    def test_admin_add_endpoint_does_not_create_mixed_case_duplicate(self):
        AllowedEmailDomain.objects.create(domain="kent.gov.uk")
        superuser = User.objects.create_superuser(username="admin", email="admin@example.com")
        self.client.force_login(superuser)

        response = self.client.post(
            reverse("admin:webcaf_allowedemaildomain_add"),
            {"domain": "Kent.Gov.UK", "_save": "Save"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AllowedEmailDomain.objects.filter(domain="kent.gov.uk").count(), 1)
