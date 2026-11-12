from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from tests.test_views.base_view_test import BaseViewTest
from webcaf.webcaf.models import Assessment, Configuration, UserProfile


class SetupAssessmentTestData(BaseViewTest):
    @classmethod
    def setUpTestData(cls):
        BaseViewTest.setUpTestData()
        cls.create_assessment_url = reverse("create-draft-assessment")
        cls.create_system_url = reverse("create-draft-assessment-system")
        cls.create_profile_url = reverse("create-draft-assessment-profile")
        cls.create_review_type_url = reverse("create-draft-assessment-choose-review-type")

    def setUp(self):
        self.client = Client()
        # login to authenticate test user
        self.client.force_login(self.test_user)
        session = self.client.session
        session["current_profile_id"] = self.user_profile.id
        session.save()


class TestCreateAssessmentViews(SetupAssessmentTestData):
    def setUp(self):
        # Only a GovAssure lead may start an assessment, so the create journey runs as one.
        self.client, self.user_profile = self._login_with_role("organisation_lead", self.test_organisation)

    def test_create_system(self):
        response = self.client.get(self.create_system_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "system/system-details.html")

    def test_create_profile(self):
        response = self.client.get(self.create_profile_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assessment-profile.html")

    def test_create_review_type(self):
        response = self.client.get(self.create_review_type_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assessment/choose-review-type.html")

    def test_foreign_session_profile_cannot_establish_assessment_context(self):
        foreign_user = self.org_map["Medium organisation"]["users"]["organisation_user"]
        foreign_profile = foreign_user.profiles.get()
        session = self.client.session
        session["current_profile_id"] = foreign_profile.id
        session.save()

        response = self.client.get(self.create_assessment_url)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("current_profile_id", self.client.session)

    def test_profile_baseline(self):
        """test selection of baseline caf profile, results in simply returning to the draft asssessment page"""

        # populate the session with required values for test case
        session = self.client.session
        session["draft_assessment"] = {"system": self.test_system.id}

        session.save()

        # form data
        valid_data = {"caf_profile": "baseline"}
        response = self.client.post(self.create_profile_url, valid_data)

        # The Assessment will not be saved outside the session, as all of system profile and review must be complete first
        # we do not expect to have prepopulated review type in this scenario
        self.assertEqual(
            self.client.session["draft_assessment"], {"system": self.test_system.id, "caf_profile": "baseline"}
        )
        # We should also be redirected to the review page to be shown this review type is mandatory for an enhanced profile
        self.assertRedirects(response, self.create_assessment_url)

    def test_profile_enhanced(self):
        """test selection of enhanced caf profile, that completes section 1, results an independent review type"""

        # populate the session with required values for test case
        session = self.client.session
        session["draft_assessment"] = {"system": self.test_system.id}
        session["current_profile_id"] = self.user_profile.id
        session.save()

        # form data
        valid_data = {"caf_profile": "enhanced"}
        response = self.client.post(self.create_profile_url, valid_data, follow=True)

        # we can now test that submission of the profile form has created the correct reviewtype in
        # the assessment
        assessment = Assessment.objects.get(id=response.context_data["draft_assessment"]["assessment_id"])
        self.assertEqual(assessment.review_type, "independent")
        edit_review_type_url = reverse(
            "edit-draft-assessment-choose-review-type", kwargs={"assessment_id": assessment.id}
        )

        # We should also be redirected to the review page to be shown this review type is mandatory for an enhanced profile
        self.assertRedirects(response, edit_review_type_url)


class TestCreateAssessmentRolePermissions(SetupAssessmentTestData):
    """Only a GovAssure lead may start an assessment, by page or by form submission."""

    roles_not_allowed = ["organisation_user", "cyber_advisor", "assessor", "reviewer"]

    def test_roles_not_allowed_cannot_open_the_create_pages(self):
        for role in self.roles_not_allowed:
            client, _ = self._login_with_role(role, self.test_organisation)
            for url in [
                self.create_assessment_url,
                self.create_system_url,
                self.create_profile_url,
                self.create_review_type_url,
            ]:
                with self.subTest(role=role, url=url):
                    self.assertEqual(client.get(url).status_code, 403)

    def test_roles_not_allowed_cannot_create_a_draft_by_submitting_the_forms(self):
        for role in self.roles_not_allowed:
            with self.subTest(role=role):
                client, _ = self._login_with_role(role, self.test_organisation)
                session = client.session
                session["draft_assessment"] = {"system": self.test_system.id, "caf_profile": "baseline"}
                session.save()

                responses = [
                    client.post(self.create_system_url, {"system": self.test_system.id}),
                    client.post(self.create_profile_url, {"caf_profile": "baseline"}),
                    client.post(self.create_review_type_url, {"review_type": "peer_review"}),
                ]

                self.assertEqual([response.status_code for response in responses], [403, 403, 403])
                self.assertFalse(Assessment.objects.filter(system=self.test_system).exists())

    def test_govassure_lead_can_create_a_draft_by_submitting_the_forms(self):
        client, _ = self._login_with_role("organisation_lead", self.test_organisation)
        session = client.session
        session["draft_assessment"] = {}
        session.save()

        client.post(self.create_system_url, {"system": self.test_system.id})
        client.post(self.create_profile_url, {"caf_profile": "baseline"})
        response = client.post(self.create_review_type_url, {"review_type": "peer_review"})

        assessment = Assessment.objects.get(system=self.test_system, status="draft")
        lead = self.org_map[self.test_organisation.name]["users"]["organisation_lead"]
        self.assertEqual(assessment.created_by, lead)
        self.assertRedirects(
            response,
            reverse("edit-draft-assessment", kwargs={"assessment_id": assessment.id}),
            fetch_redirect_response=False,
        )


class TestEditAssessmentViews(SetupAssessmentTestData):
    def setUp(self):
        super().setUp()
        self.assessment = Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="25/26",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )
        self.edit_assessment_url = reverse("edit-draft-assessment", kwargs={"assessment_id": self.assessment.id})
        self.edit_system_url = reverse("edit-draft-assessment-system", kwargs={"assessment_id": self.assessment.id})
        self.edit_profile_url = reverse("edit-draft-assessment-profile", kwargs={"assessment_id": self.assessment.id})
        self.edit_review_type_url = reverse(
            "edit-draft-assessment-choose-review-type", kwargs={"assessment_id": self.assessment.id}
        )

    def test_edit_system(self):
        response = self.client.get(self.edit_system_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "system/system-details.html")

    def test_edit_profile(self):
        response = self.client.get(self.edit_profile_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assessment-profile.html")

    def test_edit_review_type(self):
        response = self.client.get(self.edit_review_type_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assessment/choose-review-type.html")

    def test_edit_completed_profile_to_enhanced(self):
        """
        this tests the scenario where the assessment was initially created as a baseline
        profile not with independent review, that is then changed to enhanced so we expect
        the review type to be automatically set to independent
        """
        # populate the session with required values for test case
        session = self.client.session
        session["draft_assessment"] = {
            "system": self.assessment.system.id,
            "review_type": self.assessment.review_type,
            "assessment_id": self.assessment.id,
        }
        session["current_profile_id"] = self.user_profile.id
        session.save()

        # form data
        valid_data = {"caf_profile": "enhanced"}
        response = self.client.post(self.edit_profile_url, valid_data)
        assessment = Assessment.objects.get(id=self.assessment.id)
        self.assertEqual(assessment.review_type, "independent")
        self.assertRedirects(response, self.edit_review_type_url)

    @freeze_time("2050-01-15 10:00:00")
    def test_edit_assessment_context_contains_correct_assessment_period_and_cutoff_values(self):
        """
        Test that EditAssessmentView context contains correct values for:
        - current_assessment_period
        - cutoff_time
        - cutoff_date

        Uses freezegun to set the current date to 2050 to avoid clashes with existing data.
        """
        # Create a configuration for the 2050 assessment period
        Configuration.objects.create(
            name="2050 Assessment Period",
            config_data={
                "current_assessment_period": "49/50",
                "assessment_period_end": "31 March 2050 11:59pm",
                "default_framework": "caf32",
            },
        )

        # Create a draft assessment for the 2050 period
        assessment_2050 = Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="49/50",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )

        edit_url = reverse("edit-draft-assessment", kwargs={"assessment_id": assessment_2050.id})

        # login to authenticate test user
        self.client.force_login(self.test_user)
        session = self.client.session
        session["current_profile_id"] = self.user_profile.id
        session.save()

        # Make the GET request
        response = self.client.get(edit_url)

        # Assert the response is successful
        self.assertEqual(response.status_code, 200)

        # Verify context contains the expected values
        self.assertIn("current_assessment_period", response.context)
        self.assertIn("cutoff_time", response.context)
        self.assertIn("cutoff_date", response.context)

        # Check the actual values match what we set in the configuration
        self.assertEqual(response.context["current_assessment_period"], "49/50")
        self.assertEqual(response.context["cutoff_time"], "11:59PM")
        self.assertEqual(response.context["cutoff_date"], "31 March 2050")


class TestEditAssessmentRolePermissions(SetupAssessmentTestData):
    """
    Only a GovAssure lead or an organisation user may change a draft, by page or by form submission.
    A cyber advisor may still open the draft overview, which is how they view a draft.
    """

    roles_not_allowed = ["cyber_advisor", "assessor", "reviewer"]
    roles_not_allowed_to_view = ["assessor", "reviewer"]

    def setUp(self):
        super().setUp()
        self.assessment = Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="25/26",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )
        self.edit_urls = {
            name: reverse(name, kwargs={"assessment_id": self.assessment.id})
            for name in [
                "edit-draft-assessment",
                "edit-draft-assessment-system",
                "edit-draft-assessment-profile",
                "edit-draft-assessment-choose-review-type",
            ]
        }

    def _draft_in_session(self, client):
        session = client.session
        session["draft_assessment"] = {
            "assessment_id": self.assessment.id,
            "system": self.test_system.id,
            "caf_profile": "baseline",
            "review_type": "peer_review",
        }
        session.save()

    def test_roles_not_allowed_cannot_open_the_pages_that_change_a_draft(self):
        change_pages = [
            "edit-draft-assessment-system",
            "edit-draft-assessment-profile",
            "edit-draft-assessment-choose-review-type",
        ]
        for role in self.roles_not_allowed:
            client, _ = self._login_with_role(role, self.test_organisation)
            for name in change_pages:
                with self.subTest(role=role, page=name):
                    self.assertEqual(client.get(self.edit_urls[name]).status_code, 403)

    def test_roles_that_cannot_view_assessments_cannot_open_the_draft_overview(self):
        for role in self.roles_not_allowed_to_view:
            with self.subTest(role=role):
                client, _ = self._login_with_role(role, self.test_organisation)
                self.assertEqual(client.get(self.edit_urls["edit-draft-assessment"]).status_code, 403)

    def test_cyber_advisor_can_still_view_the_draft_overview(self):
        client, _ = self._login_with_role("cyber_advisor", self.test_organisation)

        response = client.get(self.edit_urls["edit-draft-assessment"])

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "assessment/draft-assessment.html")

    def test_roles_not_allowed_cannot_change_a_draft_by_submitting_the_forms(self):
        for role in self.roles_not_allowed:
            with self.subTest(role=role):
                client, _ = self._login_with_role(role, self.test_organisation)
                self._draft_in_session(client)

                responses = [
                    client.post(self.edit_urls["edit-draft-assessment-profile"], {"caf_profile": "enhanced"}),
                    client.post(self.edit_urls["edit-draft-assessment-system"], {"system": self.test_system.id}),
                    client.post(
                        self.edit_urls["edit-draft-assessment-choose-review-type"], {"review_type": "independent"}
                    ),
                ]

                self.assertEqual([response.status_code for response in responses], [403, 403, 403])
                self.assessment.refresh_from_db()
                self.assertEqual(
                    (self.assessment.caf_profile, self.assessment.review_type), ("baseline", "peer_review")
                )
                self.assertIsNone(self.assessment.last_updated_by)

    def test_allowed_roles_can_change_a_draft_by_submitting_the_forms(self):
        for role in ["organisation_lead", "organisation_user"]:
            with self.subTest(role=role):
                self.assessment.caf_profile = "baseline"
                self.assessment.save()
                client, _ = self._login_with_role(role, self.test_organisation)
                self._draft_in_session(client)

                response = client.post(self.edit_urls["edit-draft-assessment-profile"], {"caf_profile": "enhanced"})

                self.assertEqual(response.status_code, 302)
                self.assessment.refresh_from_db()
                self.assertEqual(self.assessment.caf_profile, "enhanced")
                self.assertEqual(
                    self.assessment.last_updated_by, self.org_map[self.test_organisation.name]["users"][role]
                )


class TestDraftAssessmentOrganisationChecks(SetupAssessmentTestData):
    """A permitted role in another organisation still cannot create or change this organisation's draft."""

    def setUp(self):
        super().setUp()
        self.other_lead_client, _ = self._login_with_role(
            "organisation_lead", self.org_map["Medium organisation"]["organisation"]
        )

    def test_lead_of_another_organisation_cannot_create_a_draft_for_this_organisations_system(self):
        session = self.other_lead_client.session
        session["draft_assessment"] = {"caf_profile": "baseline", "review_type": "peer_review"}
        session.save()

        response = self.other_lead_client.post(self.create_system_url, {"system": self.test_system.id})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Assessment.objects.filter(system=self.test_system).exists())

    def test_lead_of_another_organisation_cannot_change_this_organisations_draft(self):
        assessment = Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="25/26",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )
        url = reverse("edit-draft-assessment-profile", kwargs={"assessment_id": assessment.id})

        response = self.other_lead_client.post(url, {"caf_profile": "enhanced"})

        self.assertEqual(response.status_code, 403)
        assessment.refresh_from_db()
        self.assertEqual(assessment.caf_profile, "baseline")

    def _draft_for_this_organisation(self):
        return Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="25/26",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )

    def test_organisation_user_of_another_organisation_cannot_change_this_organisations_draft(self):
        other_user_client, _ = self._login_with_role(
            "organisation_user", self.org_map["Medium organisation"]["organisation"]
        )
        assessment = self._draft_for_this_organisation()
        url = reverse("edit-draft-assessment-profile", kwargs={"assessment_id": assessment.id})

        response = other_user_client.post(url, {"caf_profile": "enhanced"})

        self.assertEqual(response.status_code, 403)
        assessment.refresh_from_db()
        self.assertEqual(assessment.caf_profile, "baseline")

    def test_member_of_both_organisations_is_limited_to_the_active_one(self):
        """
        A user who holds a permitted role in this organisation and in another one,
        with the other one active, cannot create or change this organisation's drafts.
        """
        medium = self.org_map["Medium organisation"]["organisation"]
        for role in ["organisation_lead", "organisation_user"]:
            with self.subTest(role=role):
                user = self._create_user_with_role(role, self.test_organisation, f"both-{role}")
                UserProfile.objects.create(user=user, organisation=medium, role=role)
                active_profile = UserProfile.objects.get(user=user, organisation=medium)
                client = Client()
                client.force_login(user)
                session = client.session
                session["current_profile_id"] = active_profile.id
                session["draft_assessment"] = {"caf_profile": "baseline", "review_type": "peer_review"}
                session.save()

                assessment = self._draft_for_this_organisation()
                edit_url = reverse("edit-draft-assessment-profile", kwargs={"assessment_id": assessment.id})
                edit_response = client.post(edit_url, {"caf_profile": "enhanced"})
                assessment.refresh_from_db()
                self.assertEqual(edit_response.status_code, 403)
                self.assertEqual(assessment.caf_profile, "baseline")
                assessment.delete()

                if role == "organisation_lead":
                    create_response = client.post(self.create_system_url, {"system": self.test_system.id})
                    self.assertEqual(create_response.status_code, 403)
                    self.assertFalse(Assessment.objects.filter(system=self.test_system).exists())


class TestOrganisationUserCanEditButNotCreate(SetupAssessmentTestData):
    """An organisation user may change an existing draft but may not start one."""

    def test_organisation_user_can_edit_a_draft_but_cannot_create_one(self):
        client, _ = self._login_with_role("organisation_user", self.test_organisation)
        session = client.session
        session["draft_assessment"] = {"caf_profile": "baseline", "review_type": "peer_review"}
        session.save()

        create_response = client.post(self.create_system_url, {"system": self.test_system.id})

        self.assertEqual(create_response.status_code, 403)
        self.assertFalse(Assessment.objects.filter(system=self.test_system).exists())

        assessment = Assessment.objects.create(
            system=self.test_system,
            status="draft",
            assessment_period="25/26",
            review_type="peer_review",
            framework="caf32",
            caf_profile="baseline",
        )
        session = client.session
        session["draft_assessment"] = {
            "assessment_id": assessment.id,
            "system": self.test_system.id,
            "caf_profile": "baseline",
            "review_type": "peer_review",
        }
        session.save()

        edit_response = client.post(
            reverse("edit-draft-assessment-profile", kwargs={"assessment_id": assessment.id}),
            {"caf_profile": "enhanced"},
        )

        self.assertEqual(edit_response.status_code, 302)
        assessment.refresh_from_db()
        self.assertEqual(assessment.caf_profile, "enhanced")
