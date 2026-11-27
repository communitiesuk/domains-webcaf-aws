from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.permission import PermissionUtil
from webcaf.webcaf.utils.role_assignments import revoke_role


class UserRoleTest(TestCase):
    def test_stores_code_display_name_and_group(self):
        role = UserRole.objects.create(code="test-role", role_name="Test role", group=UserRole.Group.COUNCIL)

        role.refresh_from_db()

        self.assertEqual(role.code, "test-role")
        self.assertEqual(str(role), "Test role")
        self.assertEqual(role.group, UserRole.Group.COUNCIL)

    def test_role_code_is_unique_in_the_database(self):
        UserRole.objects.create(code="test-role", role_name="Test role", group=UserRole.Group.COUNCIL)

        with self.assertRaises(IntegrityError), transaction.atomic():
            UserRole.objects.create(code="test-role", role_name="Another label", group=UserRole.Group.COUNCIL)

    def test_database_accepts_all_role_groups(self):
        for group in UserRole.Group:
            with self.subTest(group=group):
                role = UserRole.objects.create(code=f"test-{group}", role_name="Test role", group=group)
                self.assertEqual(role.group, group)

    def test_database_rejects_unknown_or_missing_role_groups(self):
        for group in ["unknown", "", None]:
            with self.subTest(group=group):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    UserRole.objects.create(code="test-role", role_name="Test role", group=group)


class UserProfileRoleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="member")
        cls.granter = User.objects.create_user(username="granter")
        cls.organisation = Organisation.objects.create(name="Test organisation")
        cls.other_organisation = Organisation.objects.create(name="Other organisation")
        cls.profile = UserProfile.objects.create(user=cls.user, organisation=cls.organisation, role="organisation_user")
        cls.other_profile = UserProfile.objects.create(
            user=cls.granter, organisation=cls.other_organisation, role="organisation_user"
        )
        cls.role = UserRole.objects.create(code="test-role", role_name="Test role", group=UserRole.Group.COUNCIL)

    def create_assignment(self, **overrides):
        values = {
            "user_profile": self.profile,
            "user_role": self.role,
            "organisation": self.organisation,
            "granted_by": self.granter,
        }
        values.update(overrides)
        return UserProfileRole.objects.create(**values)

    def test_stores_assignment_references_and_grant_metadata(self):
        before = timezone.now()
        assignment = self.create_assignment()
        after = timezone.now()

        assignment.refresh_from_db()

        self.assertEqual(assignment.user_profile, self.profile)
        self.assertEqual(assignment.user_role, self.role)
        self.assertEqual(assignment.organisation, self.organisation)
        self.assertEqual(assignment.granted_by, self.granter)
        self.assertLessEqual(before, assignment.granted_at)
        self.assertLessEqual(assignment.granted_at, after)
        self.assertEqual(list(self.profile.role_assignments.all()), [assignment])
        self.assertEqual(list(self.role.profile_assignments.all()), [assignment])
        self.assertEqual(list(self.organisation.role_assignments.all()), [assignment])
        self.assertEqual(list(self.granter.profile_role_grants.all()), [assignment])

    def test_can_preserve_a_historical_grant_date_without_a_known_granter(self):
        granted_at = timezone.now() - timedelta(days=30)
        assignment = self.create_assignment(granted_at=granted_at, granted_by=None)

        assignment.refresh_from_db()

        self.assertEqual(assignment.granted_at, granted_at)
        self.assertIsNone(assignment.granted_by)
        self.assertEqual(assignment.history.latest().granted_at, granted_at)

    def test_database_rejects_duplicate_profile_role_assignments(self):
        self.create_assignment()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_assignment()

    def test_bulk_insert_cannot_bypass_unique_profile_role_constraint(self):
        self.create_assignment()

        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfileRole.objects.bulk_create(
                [UserProfileRole(user_profile=self.profile, user_role=self.role, organisation=self.organisation)]
            )

    def test_profile_can_store_multiple_distinct_roles(self):
        first = self.create_assignment()
        other_role = UserRole.objects.create(
            code="other-test-role", role_name="Other test role", group=UserRole.Group.COUNCIL
        )
        second = self.create_assignment(user_role=other_role)

        self.assertEqual(set(self.profile.role_assignments.all()), {first, second})

    def test_role_can_be_assigned_to_other_profiles_in_the_same_or_different_organisations(self):
        first = self.create_assignment()
        same_organisation_profile = UserProfile.objects.create(
            user=self.granter, organisation=self.organisation, role="organisation_user"
        )
        second = self.create_assignment(user_profile=same_organisation_profile)
        third = self.create_assignment(user_profile=self.other_profile, organisation=self.other_organisation)

        self.assertEqual(set(self.role.profile_assignments.all()), {first, second, third})

    def test_required_assignment_fields_are_not_nullable(self):
        for field in ["user_profile", "user_role", "organisation", "granted_at"]:
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.create_assignment(**{field: None})

    def test_database_rejects_assignment_to_another_organisation(self):
        with self.assertRaisesMessage(IntegrityError, "user_profile_role_matches_organisation"), transaction.atomic():
            self.create_assignment(organisation=self.other_organisation)

    def test_raw_sql_cannot_bypass_organisation_matching(self):
        with self.assertRaisesMessage(IntegrityError, "user_profile_role_matches_organisation"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO webcaf_userprofilerole
                        (user_profile_id, user_role_id, organisation_id, granted_by_id, granted_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [self.profile.pk, self.role.pk, self.other_organisation.pk, self.granter.pk, timezone.now()],
                )

    def test_bulk_insert_cannot_bypass_organisation_matching(self):
        with self.assertRaisesMessage(IntegrityError, "user_profile_role_matches_organisation"), transaction.atomic():
            UserProfileRole.objects.bulk_create(
                [UserProfileRole(user_profile=self.profile, user_role=self.role, organisation=self.other_organisation)]
            )

    def test_updating_assignment_organisation_cannot_break_matching(self):
        assignment = self.create_assignment()

        with self.assertRaisesMessage(IntegrityError, "user_profile_role_matches_organisation"), transaction.atomic():
            UserProfileRole.objects.filter(pk=assignment.pk).update(organisation=self.other_organisation)

        assignment.refresh_from_db()
        self.assertEqual(assignment.organisation, self.organisation)

    def test_updating_assignment_profile_cannot_break_matching(self):
        assignment = self.create_assignment()

        with self.assertRaisesMessage(IntegrityError, "user_profile_role_matches_organisation"), transaction.atomic():
            UserProfileRole.objects.filter(pk=assignment.pk).update(user_profile=self.other_profile)

    def test_updating_profile_organisation_cannot_invalidate_existing_assignments(self):
        self.create_assignment()

        for organisation in [self.other_organisation, None]:
            with self.subTest(organisation=organisation):
                with (
                    self.assertRaises(IntegrityError),
                    transaction.atomic(),
                ):
                    UserProfile.objects.filter(pk=self.profile.pk).update(organisation=organisation)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.organisation, self.organisation)

    def test_database_rejects_assignments_on_profiles_without_an_organisation(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=self.user, role="reviewer")

    def test_history_records_grants_changes_removals_and_actor_separately(self):
        assignment = UserProfileRole(
            user_profile=self.profile,
            user_role=self.role,
            organisation=self.organisation,
            granted_by=self.granter,
        )
        assignment._history_user = self.user
        assignment.save()
        assignment_id = assignment.pk
        granted_at = assignment.granted_at
        replacement_granter = User.objects.create_user(username="replacement-granter")

        assignment.granted_by = replacement_granter
        assignment.save(update_fields=["granted_by"])
        assignment.delete()

        history = list(UserProfileRole.history.filter(id=assignment_id).order_by("history_id"))
        self.assertEqual([record.history_type for record in history], ["+", "~", "-"])
        self.assertEqual(
            [record.granted_by_id for record in history],
            [self.granter.pk, replacement_granter.pk, replacement_granter.pk],
        )
        for record in history:
            self.assertEqual(record.user_profile_id, self.profile.pk)
            self.assertEqual(record.user_role_id, self.role.pk)
            self.assertEqual(record.organisation_id, self.organisation.pk)
            self.assertEqual(record.granted_at, granted_at)
            self.assertEqual(record.history_user_id, self.user.pk)
        self.assertFalse(UserProfileRole.objects.filter(pk=assignment_id).exists())

    def test_deleting_granter_preserves_assignment_and_historical_granter_id(self):
        assignment = self.create_assignment()
        granter_id = self.granter.pk

        self.granter.delete()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.granted_by)
        self.assertEqual(assignment.history.earliest().granted_by_id, granter_id)

    def test_assigned_catalogue_role_is_protected_from_deletion(self):
        assignment = self.create_assignment()

        with self.assertRaises(ProtectedError):
            self.role.delete()

        self.assertTrue(UserProfileRole.objects.filter(pk=assignment.pk).exists())

    def test_deleting_profile_requires_revocation_and_preserves_history(self):
        assignment = self.create_assignment()
        assignment_id = assignment.pk
        profile_id = self.profile.pk

        with self.assertRaisesMessage(ValidationError, "Revoke role assignments before deleting"):
            self.profile.delete()

        self.assertTrue(UserProfile.objects.filter(pk=profile_id).exists())
        self.assertEqual(assignment.history.count(), 1)
        operator = User.objects.create_superuser(username="membership-remover", password=None)
        revoke_role(assignment_id, operator)
        self.profile.delete()

        self.assertFalse(UserProfileRole.objects.filter(pk=assignment_id).exists())
        history = UserProfileRole.history.filter(id=assignment_id)
        self.assertEqual(history.count(), 2)
        self.assertEqual(history.latest().history_type, "-")
        self.assertEqual(history.latest().user_profile_id, profile_id)
        self.assertEqual(history.latest().history_user_id, operator.pk)
        self.assertEqual(str(history.latest().instance), f"Profile {profile_id}: role {self.role.pk}")
        self.assertTrue(UserRole.objects.filter(pk=self.role.pk).exists())

    def test_deleting_organisation_removes_assignment_and_preserves_history(self):
        assignment = self.create_assignment()
        assignment_id = assignment.pk
        organisation_id = self.organisation.pk

        self.organisation.delete()

        self.assertFalse(UserProfileRole.objects.filter(pk=assignment_id).exists())
        history = UserProfileRole.history.filter(id=assignment_id)
        self.assertEqual(history.count(), 2)
        self.assertEqual(history.latest().history_type, "-")
        self.assertEqual(history.latest().organisation_id, organisation_id)

    def test_profile_can_change_organisation_after_assignment_removal_without_losing_history(self):
        assignment = self.create_assignment()
        assignment_id = assignment.pk
        assignment.delete()

        self.profile.organisation = self.other_organisation
        self.profile.save(update_fields=["organisation"])

        self.assertEqual(
            UserProfileRole.history.filter(id=assignment_id).latest().organisation_id, self.organisation.pk
        )

    def test_assignments_do_not_change_legacy_role_behaviour_or_permissions(self):
        self.assertFalse(PermissionUtil.current_user_can_start_assessment(self.profile))
        assignment = self.create_assignment()
        self.profile.refresh_from_db()

        self.assertEqual(self.profile.role, "organisation_user")
        self.assertFalse(PermissionUtil.current_user_can_start_assessment(self.profile))

        self.profile.role = "organisation_lead"
        self.profile.save(update_fields=["role"])
        assignment.refresh_from_db()

        self.assertEqual(assignment.user_role, self.role)
        self.assertTrue(PermissionUtil.current_user_can_start_assessment(self.profile))

        assignment.delete()
        self.profile.refresh_from_db()

        self.assertEqual(self.profile.role, "organisation_lead")
        self.assertTrue(PermissionUtil.current_user_can_start_assessment(self.profile))

    def test_renaming_a_seeded_role_preserves_its_code_identity_and_assignment_links(self):
        role = UserRole.objects.get(code="caf_lead")
        role_id = role.pk
        assignment = self.create_assignment(user_role=role)

        role.role_name = "Renamed council lead"
        role.save(update_fields=["role_name"])
        role.refresh_from_db()
        assignment.refresh_from_db()

        self.assertEqual(role.pk, role_id)
        self.assertEqual(role.code, "caf_lead")
        self.assertEqual(str(role), "Renamed council lead")
        self.assertEqual(assignment.user_role_id, role_id)
        self.assertEqual(assignment.user_role.code, "caf_lead")
        self.assertEqual(assignment.history.count(), 1)
        self.assertEqual(assignment.history.latest().user_role_id, role_id)

    def test_legacy_profile_writes_do_not_create_assignments(self):
        self.assertFalse(UserProfileRole.objects.exists())

        self.profile.role = "organisation_lead"
        self.profile.save(update_fields=["role"])

        self.assertFalse(UserProfileRole.objects.exists())

    def test_normal_saves_cannot_transfer_an_assigned_membership(self):
        assignment = self.create_assignment()
        before = self.profile.history.count()
        for field, value in [("user", self.granter), ("organisation", self.other_organisation)]:
            for update_fields in [None, [field], [f"{field}_id"]]:
                with self.subTest(field=field, update_fields=update_fields):
                    profile = UserProfile.objects.get(pk=self.profile.pk)
                    setattr(profile, field, value)
                    with self.assertRaises(ValidationError) as raised:
                        with transaction.atomic():
                            profile.save(update_fields=update_fields)
                    self.assertEqual(raised.exception.code, "assigned_profile_identity")

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.user.pk)
        self.assertEqual(self.profile.organisation_id, self.organisation.pk)
        self.assertEqual(self.profile.history.count(), before)
        self.assertEqual(assignment.history.count(), 1)

    def test_reconstructed_profile_cannot_transfer_existing_assignments(self):
        self.create_assignment()
        profile = UserProfile(pk=self.profile.pk, user=self.granter, organisation=self.organisation)
        self.assertTrue(profile._state.adding)

        with self.assertRaises(ValidationError):
            profile.save(update_fields=["user"])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.user.pk)

    def test_unrelated_partial_save_ignores_unsaved_membership_identity(self):
        assignment = self.create_assignment()
        self.profile.user = self.granter
        self.profile.organisation = self.other_organisation
        self.profile.role = "organisation_lead"

        self.profile.save(update_fields=iter(["role"]))

        self.assertEqual(self.profile.user_id, self.granter.pk)
        stored = UserProfile.objects.get(pk=self.profile.pk)
        self.assertEqual(stored.user_id, self.user.pk)
        self.assertEqual(stored.organisation_id, self.organisation.pk)
        self.assertEqual(stored.role, "organisation_lead")
        self.assertEqual(assignment.history.count(), 1)

    def test_deferred_profile_can_save_an_unrelated_field(self):
        self.create_assignment()
        profile = UserProfile.objects.only("role").get(pk=self.profile.pk)
        profile.role = "organisation_lead"

        profile.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.user.pk)
        self.assertEqual(self.profile.organisation_id, self.organisation.pk)
        self.assertEqual(self.profile.role, "organisation_lead")

    def test_direct_identity_save_is_allowed_after_revocation(self):
        assignment = self.create_assignment()
        operator = User.objects.create_superuser(username="identity-revoker", password=None)
        revoke_role(assignment.pk, operator)
        self.profile.user = self.granter

        self.profile.save(update_fields=["user"])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.granter.pk)

    def test_new_profile_with_explicit_primary_key_can_be_inserted(self):
        profile = UserProfile(pk=98765, user=self.granter, organisation=self.organisation)

        profile.save()

        self.assertTrue(UserProfile.objects.filter(pk=98765, user=self.granter).exists())

    def test_stale_loaded_profile_cannot_recreate_a_deleted_membership(self):
        cases = [(None, False), (["role"], False), (None, True)]
        for index, (update_fields, deferred) in enumerate(cases):
            with self.subTest(update_fields=update_fields, deferred=deferred):
                user = User.objects.create_user(username=f"deleted-profile-member-{index}")
                original = UserProfile.objects.create(
                    user=user, organisation=self.organisation, role="organisation_user"
                )
                profiles = UserProfile.objects.only("role") if deferred else UserProfile.objects.all()
                stale = profiles.get(pk=original.pk)
                profile_id = original.pk
                original.delete()
                history_count = UserProfile.history.filter(id=profile_id).count()
                self.assertFalse(stale._state.adding)
                stale.role = "organisation_lead"

                with self.assertRaisesMessage(ValidationError, "The profile changed."):
                    stale.save(update_fields=update_fields)

                self.assertFalse(UserProfile.objects.filter(pk=profile_id).exists())
                self.assertEqual(UserProfile.history.filter(id=profile_id).count(), history_count)

    def test_database_rejects_duplicate_memberships_even_for_a_different_legacy_role(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=self.user, organisation=self.organisation, role="organisation_lead")

    def test_catalogue_and_assignments_are_registered_in_admin(self):
        self.assertTrue(admin.site.is_registered(UserRole))
        self.assertTrue(admin.site.is_registered(UserProfileRole))
