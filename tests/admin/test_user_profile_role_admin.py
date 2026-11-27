from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.role_assignments import grant_role


class UserProfileRoleAdminTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.operator = User.objects.create_superuser(username="admin-role-operator", password=None)
        cls.member = User.objects.create_user(username="admin-role-member")
        cls.organisation = Organisation.objects.create(
            name="Admin council", organisation_type="council", gss_code="E06000001"
        )
        cls.profile = UserProfile.objects.create(
            user=cls.member, organisation=cls.organisation, role="organisation_user"
        )

    def setUp(self):
        self.client.force_login(self.operator)
        self.add_url = reverse("admin:webcaf_userprofilerole_add")

    def post_grant(self, code, **extra):
        return self.client.post(
            self.add_url,
            {
                "user_profile": self.profile.pk,
                "user_role": UserRole.objects.get(code=code).pk,
                "_save": "Save",
                **extra,
            },
        )

    def test_admin_grants_stamp_actor_and_derive_organisation(self):
        other = Organisation.objects.create(name="Other admin organisation", organisation_type="mhclg")
        response = self.post_grant("caf_lead", organisation=other.pk, granted_by=self.member.pk)

        self.assertEqual(response.status_code, 302)
        assignment = UserProfileRole.objects.get()
        self.assertEqual(assignment.organisation, self.organisation)
        self.assertEqual(assignment.granted_by, self.operator)
        self.assertEqual(assignment.history.latest().history_user, self.operator)
        self.assertEqual(assignment.history.count(), 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, "organisation_user")

    def test_admin_refuses_ineligible_and_duplicate_grants_as_form_errors(self):
        response = self.post_grant("mhclg_super_user")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not eligible")
        self.assertFalse(UserProfileRole.objects.exists())

        self.assertEqual(self.post_grant("caf_lead").status_code, 302)
        response = self.post_grant("caf_lead")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already assigned")
        self.assertEqual(UserProfileRole.history.count(), 1)

    def test_admin_refuses_incompatible_combinations_without_writing_history(self):
        self.assertEqual(self.post_grant("approver").status_code, 302)
        response = self.post_grant("caf_lead")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approver must be the only role")
        self.assertEqual(UserProfileRole.objects.count(), 1)
        self.assertEqual(UserProfileRole.history.count(), 1)

    def test_admin_removal_records_history_actor(self):
        assignment = grant_role(self.profile.pk, UserRole.objects.get(code="caf_lead").pk, self.operator)
        response = self.client.post(
            reverse("admin:webcaf_userprofilerole_delete", args=[assignment.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        history = UserProfileRole.history.filter(id=assignment.pk)
        self.assertEqual(history.latest().history_type, "-")
        self.assertEqual(history.latest().history_user, self.operator)

    def test_staff_without_role_management_permissions_cannot_grant(self):
        staff = User.objects.create_user(username="unprivileged-role-staff", is_staff=True)
        self.client.force_login(staff)
        response = self.post_grant("caf_lead")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserProfileRole.objects.exists())

    def test_catalogue_identity_is_read_only_but_display_names_can_change(self):
        role = UserRole.objects.get(code="caf_lead")
        response = self.client.post(
            reverse("admin:webcaf_userrole_change", args=[role.pk]),
            {"role_name": "Council lead label", "code": "tampered", "group": "mhclg", "_save": "Save"},
        )
        self.assertEqual(response.status_code, 302)
        role.refresh_from_db()
        self.assertEqual(role.code, "caf_lead")
        self.assertEqual(role.group, "council")
        self.assertEqual(role.role_name, "Council lead label")

    def test_history_restore_and_bulk_actions_cannot_bypass_validation(self):
        model_admin = admin.site._registry[UserProfileRole]
        self.assertIsNone(model_admin.actions)
        request = RequestFactory().get("/")
        request.user = self.operator
        with self.assertRaises(PermissionDenied):
            model_admin.history_form_view(request, "1", "1")

    def test_admin_membership_form_rejects_missing_organisation_and_duplicate_membership(self):
        url = reverse("admin:webcaf_userprofile_add")
        response = self.client.post(url, {"user": self.member.pk, "role": "organisation_user", "_save": "Save"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")
        response = self.client.post(
            url, {"user": self.member.pk, "organisation": self.organisation.pk, "role": "reviewer", "_save": "Save"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserProfile.objects.count(), 1)
