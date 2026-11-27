from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from threading import Barrier, Event

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.role_assignments import grant_role, revoke_role, validate_role_eligibility


class RoleAssignmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_superuser(username="role-operator", password=None)
        cls.member = User.objects.create_user(username="role-member")
        cls.profiles = {}
        for organisation_type, code in [
            ("council", "E06000001"),
            ("mhclg", None),
            ("independent-assurer", None),
        ]:
            organisation = Organisation.objects.create(
                name=f"Role test {organisation_type}", organisation_type=organisation_type, gss_code=code
            )
            cls.profiles[organisation_type] = UserProfile.objects.create(
                user=cls.member, organisation=organisation, role="organisation_user"
            )
        cls.profile = cls.profiles["council"]

    def grant(self, code, profile=None, actor=None):
        return grant_role((profile or self.profile).pk, UserRole.objects.get(code=code).pk, actor or self.actor)

    def test_all_catalogue_roles_are_eligible_only_for_their_matching_organisation_type(self):
        groups = {"council": "council", "mhclg": "mhclg", "independent-assurer": "assurer"}
        for organisation_type, profile in self.profiles.items():
            for role in UserRole.objects.all():
                with self.subTest(organisation_type=organisation_type, role=role.code):
                    if role.group == groups[organisation_type]:
                        assignment = grant_role(profile.pk, role.pk, self.actor)
                        revoke_role(assignment.pk, self.actor)
                    else:
                        with self.assertRaises(ValidationError):
                            grant_role(profile.pk, role.pk, self.actor)

    def test_combined_authority_uses_council_eligibility(self):
        organisation = Organisation.objects.create(
            name="Combined authority", organisation_type="council", gss_code="E47000003"
        )
        profile = UserProfile.objects.create(user=self.member, organisation=organisation)

        assignment = self.grant("caf_lead", profile=profile)

        self.assertEqual(organisation.council_subtype, "Combined authority")
        self.assertEqual(assignment.organisation, organisation)

    def test_unknown_unclassified_and_legacy_types_fail_closed(self):
        for value in [None, "", "other", "combined-authority", "assurer"]:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_role_eligibility(value, UserRole.Group.COUNCIL)

    def test_approved_pairs_and_triple_can_be_granted_in_any_order(self):
        for codes in permutations(["caf_lead", "collaborator", "systems_mapper"]):
            with self.subTest(codes=codes):
                assignments = [self.grant(code) for code in codes]
                self.assertEqual(self.profile.role_assignments.count(), 3)
                for assignment in assignments:
                    revoke_role(assignment.pk, self.actor)

    def test_refused_grants_do_not_write_an_assignment_or_history(self):
        refused_pairs = [
            ("caf_lead", "quality_assurer"),
            ("systems_mapper", "quality_assurer"),
            ("deputy_caf_lead", "collaborator"),
            ("collaborator", "quality_assurer"),
        ]
        for pair in refused_pairs:
            for first, second in permutations(pair):
                with self.subTest(first=first, second=second):
                    assignment = self.grant(first)
                    before = UserProfileRole.history.count()
                    with self.assertRaises(ValidationError):
                        self.grant(second)
                    self.assertEqual(self.profile.role_assignments.count(), 1)
                    self.assertEqual(UserProfileRole.history.count(), before)
                    revoke_role(assignment.pk, self.actor)

    def test_approver_is_exclusive_in_both_grant_orders(self):
        for role in UserRole.objects.filter(group=UserRole.Group.COUNCIL).exclude(code="approver"):
            for first, second in permutations(["approver", role.code]):
                with self.subTest(first=first, second=second):
                    assignment = self.grant(first)
                    with self.assertRaises(ValidationError):
                        self.grant(second)
                    revoke_role(assignment.pk, self.actor)

    def test_mhclg_and_assurer_combinations_are_refused(self):
        for organisation_type in ["mhclg", "independent-assurer"]:
            profile = self.profiles[organisation_type]
            group = "mhclg" if organisation_type == "mhclg" else "assurer"
            for first, second in permutations(UserRole.objects.filter(group=group), 2):
                with self.subTest(first=first.code, second=second.code):
                    assignment = grant_role(profile.pk, first.pk, self.actor)
                    with self.assertRaises(ValidationError):
                        grant_role(profile.pk, second.pk, self.actor)
                    revoke_role(assignment.pk, self.actor)

    def test_grants_and_removals_record_actor_metadata_and_leave_legacy_role_unchanged(self):
        assignment = self.grant("caf_lead")
        assignment_id = assignment.pk
        remover = User.objects.create_superuser(username="role-remover", password=None)

        revoke_role(assignment_id, remover)

        history = list(UserProfileRole.history.filter(id=assignment_id).order_by("history_id"))
        self.assertEqual([row.history_type for row in history], ["+", "-"])
        self.assertEqual([row.history_user_id for row in history], [self.actor.pk, remover.pk])
        self.assertTrue(all(row.granted_by_id == self.actor.pk for row in history))
        self.assertEqual(history[0].granted_at, history[1].granted_at)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, "organisation_user")

    def test_duplicate_grant_is_refused_without_extra_history(self):
        assignment = self.grant("caf_lead")
        with self.assertRaises(ValidationError) as raised:
            self.grant("caf_lead")
        self.assertEqual(raised.exception.code, "duplicate_role")
        self.assertEqual(assignment.history.count(), 1)

    def test_unauthorised_or_inactive_actors_cannot_grant_or_revoke(self):
        assignment = self.grant("caf_lead")
        inactive = User.objects.create_superuser(username="inactive-role-operator", password=None, is_active=False)
        for actor in [self.member, inactive]:
            with self.subTest(actor=actor.pk):
                with self.assertRaises(PermissionDenied):
                    self.grant("collaborator", actor=actor)
                with self.assertRaises(PermissionDenied):
                    revoke_role(assignment.pk, actor)
        self.assertEqual(assignment.history.count(), 1)

    def test_django_add_permission_allows_grants_but_not_removals(self):
        operator = User.objects.create_user(username="add-only-operator", is_staff=True)
        operator.user_permissions.add(
            Permission.objects.get(codename="add_userprofilerole", content_type__app_label="webcaf")
        )
        assignment = self.grant("caf_lead", actor=operator)
        self.assertEqual(assignment.granted_by, operator)
        with self.assertRaises(PermissionDenied):
            revoke_role(assignment.pk, operator)

    def test_django_delete_permission_allows_removals_but_not_grants(self):
        assignment = self.grant("caf_lead")
        assignment_id = assignment.pk
        operator = User.objects.create_user(username="delete-only-operator", is_staff=True)
        operator.user_permissions.add(
            Permission.objects.get(codename="delete_userprofilerole", content_type__app_label="webcaf")
        )

        revoke_role(assignment_id, operator)

        self.assertFalse(UserProfileRole.objects.filter(pk=assignment_id).exists())
        history = UserProfileRole.history.filter(id=assignment_id)
        self.assertEqual(history.count(), 2)
        self.assertEqual(history.latest().history_user, operator)
        with self.assertRaises(PermissionDenied):
            self.grant("caf_lead", actor=operator)
        self.assertEqual(history.count(), 2)

    def test_missing_and_already_revoked_assignments_are_validation_errors(self):
        assignment = self.grant("caf_lead")
        assignment_id = assignment.pk
        revoke_role(assignment_id, self.actor)
        before = UserProfileRole.history.count()

        for missing_id in [assignment_id, 999999]:
            with self.subTest(assignment_id=missing_id):
                with self.assertRaisesMessage(ValidationError, "This role assignment no longer exists.") as raised:
                    revoke_role(missing_id, self.actor)
                self.assertEqual(raised.exception.code, "assignment_not_found")
                self.assertEqual(UserProfileRole.history.count(), before)

    def test_existing_ineligible_assignment_blocks_further_grants(self):
        UserProfileRole.objects.create(
            user_profile=self.profile,
            organisation=self.profile.organisation,
            user_role=UserRole.objects.get(code="mhclg_super_user"),
            granted_by=self.actor,
        )
        before = UserProfileRole.history.count()

        with self.assertRaises(ValidationError) as raised:
            self.grant("caf_lead")

        self.assertEqual(raised.exception.code, "ineligible_role")
        self.assertEqual(self.profile.role_assignments.count(), 1)
        self.assertEqual(UserProfileRole.history.count(), before)

    def test_existing_disallowed_pair_blocks_an_otherwise_compatible_grant(self):
        # Simulate assignments written outside the validated service. The new
        # role is compatible with Collaborator but cannot repair this pair.
        for code in ["collaborator", "quality_assurer"]:
            UserProfileRole.objects.create(
                user_profile=self.profile,
                organisation=self.profile.organisation,
                user_role=UserRole.objects.get(code=code),
                granted_by=self.actor,
            )
        before = UserProfileRole.history.count()

        with self.assertRaises(ValidationError) as raised:
            self.grant("systems_mapper")

        self.assertEqual(raised.exception.code, "role_combination_not_permitted")
        self.assertEqual(self.profile.role_assignments.count(), 2)
        self.assertEqual(UserProfileRole.history.count(), before)

    def test_stale_profile_and_role_ids_are_validation_errors(self):
        with self.assertRaises(ValidationError):
            grant_role(999999, UserRole.objects.get(code="caf_lead").pk, self.actor)
        with self.assertRaises(ValidationError):
            grant_role(self.profile.pk, 999999, self.actor)

    def test_organisation_reclassification_cannot_invalidate_existing_assignments(self):
        assignment = self.grant("caf_lead")
        organisation = self.profile.organisation
        organisation.organisation_type = "mhclg"
        with self.assertRaises(ValidationError):
            organisation.save(update_fields=["organisation_type"])
        organisation.refresh_from_db()
        self.assertEqual(organisation.organisation_type, "council")

        revoke_role(assignment.pk, self.actor)
        organisation.organisation_type = "mhclg"
        organisation.save(update_fields=["organisation_type"])

    def test_partial_save_checks_stored_type_without_overwriting_unsaved_type(self):
        profile = self.profiles["mhclg"]
        self.grant("mhclg_super_user", profile=profile)
        organisation = profile.organisation
        organisation.name = "Renamed MHCLG organisation"
        organisation.organisation_type = "council"

        for unsaved_gss_code in [None, "E06000002"]:
            with self.subTest(unsaved_gss_code=unsaved_gss_code):
                organisation.gss_code = unsaved_gss_code
                organisation.save(update_fields=["name"])

                self.assertEqual(organisation.organisation_type, "council")
                self.assertEqual(organisation.gss_code, unsaved_gss_code)
                stored = Organisation.objects.get(pk=organisation.pk)
                self.assertEqual(stored.name, "Renamed MHCLG organisation")
                self.assertEqual(stored.organisation_type, "mhclg")
                self.assertIsNone(stored.gss_code)
                self.assertEqual(profile.role_assignments.count(), 1)

    def test_full_clean_still_rejects_in_memory_reclassification(self):
        self.grant("caf_lead")
        organisation = self.profile.organisation
        organisation.organisation_type = "mhclg"

        with self.assertRaisesMessage(ValidationError, "Remove incompatible role assignments"):
            organisation.full_clean()

    def test_partial_save_preserves_council_assignments_with_unsaved_type_and_gss_changes(self):
        assignment = self.grant("caf_lead")
        organisation = self.profile.organisation
        organisation.name = "Renamed assigned council"
        organisation.organisation_type = "mhclg"
        organisation.gss_code = None

        organisation.save(update_fields=["name"])

        organisation.refresh_from_db()
        self.assertEqual(organisation.name, "Renamed assigned council")
        self.assertEqual(organisation.organisation_type, "council")
        self.assertEqual(organisation.gss_code, "E06000001")
        self.assertTrue(self.profile.role_assignments.filter(pk=assignment.pk).exists())

    def test_partial_save_cannot_hide_incompatible_stored_type(self):
        self.grant("caf_lead")
        organisation = self.profile.organisation
        Organisation.objects.filter(pk=organisation.pk).update(organisation_type="mhclg")
        organisation.name = "Should not be saved"

        with self.assertRaisesMessage(ValidationError, "Remove incompatible role assignments"):
            organisation.save(update_fields=["name"])

        stored = Organisation.objects.get(pk=organisation.pk)
        self.assertNotEqual(stored.name, "Should not be saved")


class ConcurrentRoleGrantTest(TransactionTestCase):
    def run_race(self, codes):
        actor = User.objects.create_superuser(username="concurrent-operator", password=None)
        member = User.objects.create_user(username="concurrent-member")
        organisation = Organisation.objects.create(
            name="Concurrent council", organisation_type="council", gss_code="E06000001"
        )
        profile = UserProfile.objects.create(user=member, organisation=organisation)
        role_ids = [
            UserRole.objects.get_or_create(code=code, defaults={"role_name": code, "group": "council"})[0].pk
            for code in codes
        ]
        barrier = Barrier(2)

        def attempt(role_id):
            close_old_connections()
            try:
                operator = User.objects.get(pk=actor.pk)
                barrier.wait(timeout=10)
                try:
                    grant_role(profile.pk, role_id, operator)
                    return "granted"
                except ValidationError:
                    return "refused"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, role_ids))
        self.assertCountEqual(results, ["granted", "refused"])
        self.assertEqual(UserProfileRole.objects.count(), 1)
        self.assertEqual(UserProfileRole.history.count(), 1)

    def test_concurrent_incompatible_grants_cannot_both_succeed(self):
        self.run_race(["caf_lead", "approver"])

    def test_concurrent_duplicate_grants_cannot_both_succeed(self):
        self.run_race(["caf_lead", "caf_lead"])

    def test_concurrent_revocations_create_only_one_removal_history_record(self):
        actor = User.objects.create_superuser(username="concurrent-remover", password=None)
        organisation = Organisation.objects.create(name="Removal organisation", organisation_type="mhclg")
        profile = UserProfile.objects.create(user=actor, organisation=organisation)
        role, _ = UserRole.objects.get_or_create(
            code="mhclg_super_user", defaults={"role_name": "MHCLG Super User", "group": "mhclg"}
        )
        assignment = grant_role(profile.pk, role.pk, actor)
        assignment_id = assignment.pk
        barrier = Barrier(2)

        def synchronise_lock(execute, sql, params, many, context):
            # Both removals must find the assignment before either can delete it.
            if '"webcaf_organisation"' in sql and "FOR UPDATE" in sql:
                barrier.wait(timeout=10)
            return execute(sql, params, many, context)

        def attempt(_):
            close_old_connections()
            try:
                operator = User.objects.get(pk=actor.pk)
                with connection.execute_wrapper(synchronise_lock):
                    try:
                        revoke_role(assignment_id, operator)
                        return "removed"
                    except ValidationError:
                        return "refused"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, range(2)))

        self.assertCountEqual(results, ["removed", "refused"])
        self.assertFalse(UserProfileRole.objects.filter(pk=assignment_id).exists())
        history = list(UserProfileRole.history.filter(id=assignment_id).order_by("history_id"))
        self.assertEqual([row.history_type for row in history], ["+", "-"])
        self.assertTrue(all(row.history_user_id == actor.pk for row in history))

    def run_reclassification_race(self, grant_first, reconstructed=False, full_save=False):
        actor = User.objects.create_superuser(username="reclassification-operator", password=None)
        organisation = Organisation.objects.create(
            name="Reclassification council", organisation_type="council", gss_code="E06000001"
        )
        profile = UserProfile.objects.create(user=actor, organisation=organisation)
        role, _ = UserRole.objects.get_or_create(
            code="caf_lead", defaults={"role_name": "CAF Lead", "group": "council"}
        )
        first_locked = Event()
        second_attempting_lock = Event()

        def hold_first_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if '"webcaf_organisation"' in sql and "FOR UPDATE" in sql:
                first_locked.set()
                self.assertTrue(second_attempting_lock.wait(timeout=10), "Second operation did not attempt the lock")
            return result

        def signal_second_lock(execute, sql, params, many, context):
            if '"webcaf_organisation"' in sql and "FOR UPDATE" in sql:
                second_attempting_lock.set()
            return execute(sql, params, many, context)

        def attempt(first):
            close_old_connections()
            try:
                operator = User.objects.get(pk=actor.pk)
                current = Organisation.objects.get(pk=organisation.pk)
                if reconstructed:
                    current = Organisation(
                        pk=current.pk,
                        name=current.name,
                        organisation_type=current.organisation_type,
                        gss_code=None,
                    )
                    self.assertTrue(current._state.adding)
                if not first:
                    self.assertTrue(first_locked.wait(timeout=10), "First operation did not acquire the lock")
                with connection.execute_wrapper(hold_first_lock if first else signal_second_lock):
                    try:
                        if first == grant_first:
                            grant_role(profile.pk, role.pk, operator)
                        else:
                            current.organisation_type = "mhclg"
                            if reconstructed or full_save:
                                current.save()
                            else:
                                current.save(update_fields=["organisation_type"])
                        return "saved"
                    except ValidationError:
                        return "refused"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(attempt, True)
            second = executor.submit(attempt, False)
            self.assertEqual(first.result(timeout=20), "saved")
            self.assertEqual(second.result(timeout=20), "refused")

        organisation.refresh_from_db()
        self.assertEqual(organisation.organisation_type, "council" if grant_first else "mhclg")
        self.assertEqual(profile.role_assignments.count(), 1 if grant_first else 0)
        self.assertEqual(UserProfileRole.history.count(), 1 if grant_first else 0)

    def test_grant_blocks_concurrent_incompatible_reclassification(self):
        self.run_reclassification_race(grant_first=True)

    def test_reclassification_blocks_concurrent_ineligible_grant(self):
        self.run_reclassification_race(grant_first=False)

    def test_grant_blocks_reclassification_using_a_reconstructed_organisation(self):
        self.run_reclassification_race(grant_first=True, reconstructed=True)

    def test_reconstructed_reclassification_blocks_concurrent_ineligible_grant(self):
        self.run_reclassification_race(grant_first=False, reconstructed=True)

    def test_grant_blocks_a_normal_full_save_reclassification(self):
        self.run_reclassification_race(grant_first=True, full_save=True)

    def test_normal_full_save_reclassification_blocks_concurrent_ineligible_grant(self):
        self.run_reclassification_race(grant_first=False, full_save=True)
