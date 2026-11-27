from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase

from webcaf.webcaf.forms.user_profile import UserProfileForm
from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.role_assignments import grant_role, revoke_role


def profile_form(email, instance=None, organisation=None):
    if organisation is None:
        organisation = (
            instance.organisation
            if instance and instance.organisation_id
            else Organisation.objects.get_or_create(name="Form target organisation")[0]
        )
    return UserProfileForm(
        data={
            "email": email,
            "first_name": "New",
            "last_name": "Name",
            "role": "organisation_user",
            "action": "confirm",
        },
        instance=instance,
        organisation=organisation,
    )


class UserProfileFormTest(TestCase):
    def test_new_email_is_stripped_and_lowercased(self):
        form = profile_form("  New.Person@Example.gov.uk  ")
        self.assertTrue(form.is_valid(), form.errors)

        profile = form.save()

        self.assertEqual(profile.user.email, "new.person@example.gov.uk")
        self.assertEqual(profile.user.username, "new.person@example.gov.uk")
        self.assertEqual(profile.user.first_name, "New")
        self.assertEqual(profile.user.last_name, "Name")

    def test_invalid_emails_are_rejected_without_creating_records(self):
        for email in ("not-an-email", "new-email@@example.gov.uk", "", "person@"):
            with self.subTest(email=email):
                form = profile_form(email)
                self.assertFalse(form.is_valid())
                self.assertIn("email", form.errors)
        self.assertFalse(User.objects.exists())
        self.assertFalse(UserProfile.objects.exists())

    def test_case_insensitive_match_preserves_existing_user_and_memberships(self):
        # Bypass create_user's domain normalisation to model a legacy stored address.
        user = User.objects.create(
            username="Legacy.Person@Example.gov.uk",
            email="Person@Example.gov.uk",
            first_name="Original",
            last_name="Person",
        )
        organisation = Organisation.objects.create(name="Existing organisation")
        old_profile = UserProfile.objects.create(user=user, organisation=organisation, role="organisation_lead")
        form = profile_form("PERSON@EXAMPLE.GOV.UK")
        self.assertTrue(form.is_valid(), form.errors)

        new_profile = form.save()

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(new_profile.user_id, user.pk)
        user.refresh_from_db()
        self.assertEqual(user.username, "Legacy.Person@Example.gov.uk")
        self.assertEqual(user.email, "Person@Example.gov.uk")
        self.assertEqual(user.first_name, "Original")
        self.assertEqual(user.last_name, "Person")
        old_profile.refresh_from_db()
        self.assertEqual(old_profile.user_id, user.pk)
        self.assertEqual(old_profile.organisation_id, organisation.pk)
        self.assertEqual(old_profile.role, "organisation_lead")

    def test_adding_profile_for_existing_user_preserves_blank_names(self):
        user = User.objects.create_user(username="legacy-person", email="Person@example.gov.uk")
        form = profile_form("person@example.gov.uk")
        self.assertTrue(form.is_valid(), form.errors)

        profile = form.save()

        self.assertEqual(profile.user_id, user.pk)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "")
        self.assertEqual(user.last_name, "")

    def test_case_only_edit_keeps_the_same_user_and_profile(self):
        user = User.objects.create(username="Person@Example.gov.uk", email="Person@Example.gov.uk")
        profile = UserProfile.objects.create(
            user=user, role="organisation_user", organisation=Organisation.objects.create(name="Existing organisation")
        )
        form = profile_form("person@example.gov.uk", instance=profile)
        self.assertTrue(form.is_valid(), form.errors)

        saved_profile = form.save()

        self.assertEqual(saved_profile.pk, profile.pk)
        self.assertEqual(saved_profile.user_id, user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(UserProfile.objects.count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.username, "Person@Example.gov.uk")
        self.assertEqual(user.email, "Person@Example.gov.uk")
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.last_name, "Name")

    def test_match_created_after_validation_keeps_its_existing_email_and_username(self):
        form = profile_form("PERSON@example.gov.uk")
        self.assertTrue(form.is_valid(), form.errors)
        user = User.objects.create(
            username="legacy-person", email="Person@Example.gov.uk", first_name="Original", last_name="Person"
        )

        profile = form.save()

        self.assertEqual(profile.user_id, user.pk)
        self.assertEqual(User.objects.count(), 1)
        user.refresh_from_db()
        self.assertEqual(user.email, "Person@Example.gov.uk")
        self.assertEqual(user.username, "legacy-person")
        self.assertEqual(user.first_name, "Original")
        self.assertEqual(user.last_name, "Person")

    def test_existing_duplicates_are_not_merged_or_modified(self):
        first = User.objects.create_user(username="first", email="Person@example.gov.uk", first_name="First")
        second = User.objects.create_user(username="second", email="person@example.gov.uk", first_name="Second")
        profile = UserProfile.objects.create(
            user=first, role="organisation_lead", organisation=Organisation.objects.create(name="Existing organisation")
        )
        users_before = list(User.objects.order_by("pk").values())
        profiles_before = list(UserProfile.objects.order_by("pk").values())

        for instance in (None, profile):
            with self.subTest(edit=instance is not None):
                form = profile_form("PERSON@example.gov.uk", instance=instance)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors.as_data()["email"][0].code, "ambiguous_email")

        self.assertEqual(list(User.objects.order_by("pk").values()), users_before)
        self.assertEqual(list(UserProfile.objects.order_by("pk").values()), profiles_before)
        self.assertTrue(User.objects.filter(pk=second.pk).exists())

    def test_exact_email_duplicates_are_also_rejected(self):
        User.objects.create_user(username="first", email="person@example.gov.uk")
        User.objects.create_user(username="second", email="person@example.gov.uk")

        form = profile_form("person@example.gov.uk")

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["email"][0].code, "ambiguous_email")

    def test_staff_and_inactive_matches_are_not_ignored_when_checking_for_duplicates(self):
        User.objects.create_user(username="first", email="Person@example.gov.uk")
        second = User.objects.create_user(
            username="second", email="person@example.gov.uk", is_staff=True, is_active=False
        )

        form = profile_form("person@example.gov.uk")

        self.assertFalse(form.is_valid())
        second.refresh_from_db()
        self.assertTrue(second.is_staff)
        self.assertFalse(second.is_active)

    def test_username_conflict_is_a_validation_error(self):
        User.objects.create_user(username="new@example.gov.uk", email="different@example.gov.uk")

        form = profile_form("new@example.gov.uk")

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["email"][0].code, "username_conflict")
        self.assertEqual(User.objects.count(), 1)

    def test_save_rechecks_duplicates_created_after_validation(self):
        first = User.objects.create_user(username="first", email="Person@example.gov.uk", first_name="Original")
        form = profile_form("person@example.gov.uk")
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.create_user(username="second", email="person@example.gov.uk")

        with self.assertRaises(ValidationError) as error:
            form.save()

        self.assertIn("email", error.exception.message_dict)
        self.assertEqual(User.objects.count(), 2)
        self.assertFalse(UserProfile.objects.exists())
        first.refresh_from_db()
        self.assertEqual(first.first_name, "Original")

    def test_save_handles_username_conflict_created_after_validation(self):
        form = profile_form("new@example.gov.uk")
        self.assertTrue(form.is_valid(), form.errors)
        User.objects.create_user(username="new@example.gov.uk", email="different@example.gov.uk")

        with self.assertRaises(ValidationError) as error:
            form.save()

        self.assertIn("email", error.exception.message_dict)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserProfile.objects.exists())

    def test_two_prevalidated_forms_reuse_one_user(self):
        first = profile_form("Person@example.gov.uk")
        second = profile_form(
            "PERSON@example.gov.uk", organisation=Organisation.objects.create(name="Second organisation")
        )
        self.assertTrue(first.is_valid(), first.errors)
        self.assertTrue(second.is_valid(), second.errors)

        first_profile = first.save()
        second_profile = second.save()

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(first_profile.user_id, second_profile.user_id)

    def test_existing_membership_is_a_validation_error_without_modifying_it(self):
        user = User.objects.create_user(
            username="existing-member", email="Person@example.gov.uk", first_name="Original"
        )
        organisation = Organisation.objects.create(name="Existing membership organisation")
        profile = UserProfile.objects.create(user=user, organisation=organisation, role="organisation_lead")
        form = profile_form("PERSON@EXAMPLE.GOV.UK", organisation=organisation)

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors.as_data()["email"][0].code, "duplicate_membership")
        profile.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(profile.role, "organisation_lead")
        self.assertEqual(user.first_name, "Original")

    def test_membership_created_after_validation_is_refused_without_overwriting(self):
        form = profile_form("Person@example.gov.uk")
        self.assertTrue(form.is_valid(), form.errors)
        user = User.objects.create_user(username="existing-member", email="Person@example.gov.uk")
        profile = UserProfile.objects.create(
            user=user, organisation=form.instance.organisation, role="organisation_lead"
        )

        with self.assertRaises(ValidationError):
            form.save()

        profile.refresh_from_db()
        self.assertEqual(profile.role, "organisation_lead")
        self.assertEqual(UserProfile.objects.count(), 1)


class AssignedUserProfileFormTest(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser(username="identity-operator", password=None)
        self.owner = User.objects.create_user(username="original-owner", email="Owner@example.gov.uk")
        self.replacement = User.objects.create_user(
            username="replacement-owner", email="replacement@example.gov.uk", first_name="Original"
        )
        self.organisation = Organisation.objects.create(name="Identity organisation", organisation_type="mhclg")
        self.profile = UserProfile.objects.create(
            user=self.owner, organisation=self.organisation, role="organisation_user"
        )
        self.role = UserRole.objects.get(code="mhclg_super_user")

    def test_email_cannot_rebind_an_assigned_profile_to_another_account(self):
        assignment = grant_role(self.profile.pk, self.role.pk, self.operator)
        before = list(assignment.history.values())

        for email in [self.replacement.email, "new-owner@example.gov.uk"]:
            with self.subTest(email=email):
                form = profile_form(email, instance=self.profile)
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors.as_data()["email"][0].code, "assigned_profile_identity")

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.owner.pk)
        self.assertEqual(list(assignment.history.values()), before)
        self.assertFalse(User.objects.filter(email="new-owner@example.gov.uk").exists())

    def test_case_only_email_and_name_edits_preserve_assigned_membership(self):
        assignment = grant_role(self.profile.pk, self.role.pk, self.operator)
        form = profile_form(self.owner.email.upper(), instance=self.profile)
        self.assertTrue(form.is_valid(), form.errors)

        saved = form.save()

        self.assertEqual(saved.user_id, self.owner.pk)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "New")
        self.assertEqual(self.owner.email, "Owner@example.gov.uk")
        self.assertEqual(assignment.history.count(), 1)

    def test_save_rechecks_identity_after_a_grant_without_mutating_accounts(self):
        form = profile_form(self.replacement.email, instance=self.profile)
        self.assertTrue(form.is_valid(), form.errors)
        assignment = grant_role(self.profile.pk, self.role.pk, self.operator)
        profile_history_count = self.profile.history.count()

        with self.assertRaises(ValidationError):
            form.save()

        self.profile.refresh_from_db()
        self.replacement.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.owner.pk)
        self.assertEqual(self.replacement.first_name, "Original")
        self.assertEqual(self.profile.history.count(), profile_history_count)
        self.assertEqual(assignment.history.count(), 1)

    def test_account_change_is_allowed_after_explicit_revocation(self):
        assignment = grant_role(self.profile.pk, self.role.pk, self.operator)
        revoke_role(assignment.pk, self.operator)
        form = profile_form(self.replacement.email, instance=self.profile)
        self.assertTrue(form.is_valid(), form.errors)

        saved = form.save()

        self.assertEqual(saved.user_id, self.replacement.pk)
        self.assertEqual(saved.pk, self.profile.pk)
        self.assertFalse(saved.role_assignments.exists())

    def test_late_grant_rolls_back_replacement_account_creation(self):
        email = "late-new-owner@example.gov.uk"
        form = profile_form(email, instance=self.profile)
        self.assertTrue(form.is_valid(), form.errors)
        assignment = grant_role(self.profile.pk, self.role.pk, self.operator)

        with self.assertRaises(ValidationError):
            form.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.user_id, self.owner.pk)
        self.assertFalse(User.objects.filter(email=email).exists())
        self.assertEqual(assignment.history.count(), 1)

    def test_model_validation_rejects_both_membership_identity_changes(self):
        grant_role(self.profile.pk, self.role.pk, self.operator)
        other_organisation = Organisation.objects.create(name="Other identity organisation", organisation_type="mhclg")

        for field, value in [("user", self.replacement), ("organisation", other_organisation)]:
            with self.subTest(field=field):
                profile = UserProfile.objects.get(pk=self.profile.pk)
                setattr(profile, field, value)
                with self.assertRaises(ValidationError):
                    profile.full_clean()


class ConcurrentUserProfileCreationTest(TransactionTestCase):
    def test_simultaneous_membership_creation_returns_one_validation_error(self):
        organisation = Organisation.objects.create(name="Concurrent membership organisation")
        user = User.objects.create_user(username="existing-member", email="Person@example.gov.uk")
        barrier = Barrier(2)
        original_save = UserProfile.save

        def synchronised_save(profile, *args, **kwargs):
            if profile.pk is None:
                barrier.wait(timeout=10)
            return original_save(profile, *args, **kwargs)

        def attempt(email):
            close_old_connections()
            try:
                form = profile_form(email, organisation=organisation)
                if not form.is_valid():
                    raise AssertionError(form.errors)
                try:
                    form.save()
                    return "created"
                except ValidationError:
                    return "refused"
            finally:
                close_old_connections()

        with patch.object(UserProfile, "save", synchronised_save), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ["Person@example.gov.uk", "PERSON@example.gov.uk"]))

        self.assertCountEqual(results, ["created", "refused"])
        self.assertEqual(UserProfile.objects.filter(user=user, organisation=organisation).count(), 1)

    def test_simultaneous_case_variants_create_only_one_user(self):
        barrier = Barrier(2)
        original_create = QuerySet.create
        organisations = [Organisation.objects.create(name=f"Concurrent organisation {index}") for index in range(2)]

        def synchronised_create(queryset, **kwargs):
            if queryset.model is User:
                barrier.wait(timeout=10)
            return original_create(queryset, **kwargs)

        def create_profile(values):
            email, organisation = values
            close_old_connections()
            try:
                form = profile_form(email, organisation=organisation)
                if not form.is_valid():
                    raise AssertionError(form.errors)
                return form.save().user_id
            finally:
                close_old_connections()

        with patch.object(QuerySet, "create", synchronised_create), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(create_profile, zip(("Person@example.gov.uk", "PERSON@example.gov.uk"), organisations))
            )

        self.assertEqual(results[0], results[1])
        self.assertEqual(User.objects.filter(email__iexact="person@example.gov.uk").count(), 1)


class ConcurrentAssignedProfileEditTest(TransactionTestCase):
    def run_grant_and_rebinding(self, grant_first, operation="form"):
        operator = User.objects.create_superuser(username="concurrent-identity-operator", password=None)
        owner = User.objects.create_user(username="concurrent-owner", email="owner@example.gov.uk")
        replacement = User.objects.create_user(username="concurrent-replacement", email="replacement@example.gov.uk")
        organisation = Organisation.objects.create(name="Concurrent identity organisation", organisation_type="mhclg")
        profile = UserProfile.objects.create(user=owner, organisation=organisation, role="organisation_user")
        profile_id = profile.pk
        role, _ = UserRole.objects.get_or_create(
            code="mhclg_super_user", defaults={"role_name": "MHCLG Super User", "group": "mhclg"}
        )
        form = profile_form(replacement.email, instance=profile)
        self.assertTrue(form.is_valid(), form.errors)
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
                actor = User.objects.get(pk=operator.pk)
                if not first:
                    self.assertTrue(first_locked.wait(timeout=10), "First operation did not acquire the lock")
                with connection.execute_wrapper(hold_first_lock if first else signal_second_lock):
                    try:
                        if first == grant_first:
                            grant_role(profile_id, role.pk, actor)
                        else:
                            if operation == "save":
                                profile.user = replacement
                                profile.save(update_fields=["user"])
                            elif operation == "delete":
                                profile.delete()
                            else:
                                form.save()
                        return "saved"
                    except ValidationError:
                        return "refused"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(attempt, True)
            second = executor.submit(attempt, False)
            self.assertEqual(first.result(timeout=20), "saved")
            self.assertEqual(second.result(timeout=20), "refused" if grant_first or operation == "delete" else "saved")

        if operation == "delete" and not grant_first:
            self.assertFalse(UserProfile.objects.filter(pk=profile_id).exists())
            self.assertFalse(UserProfileRole.objects.filter(user_profile_id=profile_id).exists())
            self.assertFalse(UserProfileRole.history.filter(user_profile_id=profile_id).exists())
            return
        profile.refresh_from_db()
        self.assertEqual(profile.user_id, owner.pk if grant_first else replacement.pk)
        assignment = UserProfileRole.objects.get(user_profile=profile)
        self.assertEqual(assignment.history.count(), 1)
        self.assertEqual(assignment.granted_by_id, operator.pk)

    def test_concurrent_grant_prevents_identity_rebinding(self):
        self.run_grant_and_rebinding(grant_first=True)

    def test_grant_after_unassigned_profile_edit_records_an_explicit_grant(self):
        self.run_grant_and_rebinding(grant_first=False)

    def test_concurrent_grant_prevents_direct_identity_save(self):
        self.run_grant_and_rebinding(grant_first=True, operation="save")

    def test_grant_after_direct_identity_save_uses_the_saved_membership(self):
        self.run_grant_and_rebinding(grant_first=False, operation="save")

    def test_concurrent_grant_prevents_membership_deletion(self):
        self.run_grant_and_rebinding(grant_first=True, operation="delete")

    def test_membership_deletion_prevents_concurrent_grant(self):
        self.run_grant_and_rebinding(grant_first=False, operation="delete")

    def test_opposite_unassigned_membership_moves_use_a_stable_lock_order(self):
        organisations = [
            Organisation.objects.create(name=f"Move organisation {index}", organisation_type="mhclg")
            for index in range(2)
        ]
        profiles = [
            UserProfile.objects.create(
                user=User.objects.create_user(username=f"moving-member-{index}"), organisation=organisation
            )
            for index, organisation in enumerate(organisations)
        ]
        barrier = Barrier(2)

        def move(index):
            close_old_connections()
            try:
                profile = UserProfile.objects.get(pk=profiles[index].pk)
                profile.organisation = organisations[1 - index]
                barrier.wait(timeout=10)
                profile.save(update_fields=["organisation"])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(move, range(2)))

        for index, profile in enumerate(profiles):
            profile.refresh_from_db()
            self.assertEqual(profile.organisation_id, organisations[1 - index].pk)
