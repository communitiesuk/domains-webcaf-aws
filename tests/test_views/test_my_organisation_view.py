from django.urls import reverse

from tests.test_views.base_view_test import BaseViewTest


class MyOrganisationViewTest(BaseViewTest):
    def get_my_organisation(self):
        client, profile = self._login_with_role("organisation_lead", self.test_organisation)
        return client.get(reverse("my-organisation", kwargs={"id": profile.id}))

    def test_shows_the_council_subtype_read_only(self):
        self.test_organisation.organisation_type = "council"
        self.test_organisation.gss_code = "E08000035"
        self.test_organisation.save()

        response = self.get_my_organisation()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Council subtype")
        self.assertContains(response, "Metropolitan district")

    def test_shows_no_council_subtype_for_other_organisation_types(self):
        self.test_organisation.organisation_type = "independent-assurer"
        self.test_organisation.save()

        response = self.get_my_organisation()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Council subtype")

    def test_does_not_show_contact_details_or_change_links(self):
        response = self.get_my_organisation()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Additional contact information")
        self.assertNotContains(response, "Parent organisation")
        self.assertNotContains(response, "Change")

    def test_contact_and_type_edit_pages_are_removed(self):
        client, profile = self._login_with_role("organisation_lead", self.test_organisation)

        for path in [f"/my-organisation/{profile.id}/contact", f"/my-organisation/{profile.id}/type"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)
