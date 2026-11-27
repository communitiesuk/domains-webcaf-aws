from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole, UserRole
from webcaf.webcaf.utils.role_combinations import SUPPORTED_ROLE_CODES

# Expected AC2 data is independent from the migration's frozen seed definition.
EXPECTED_ROLES = {
    "caf_lead": ("CAF Lead", "council"),
    "deputy_caf_lead": ("Deputy CAF Lead", "council"),
    "collaborator": ("Collaborator", "council"),
    "systems_mapper": ("Systems Mapper", "council"),
    "approver": ("Approver", "council"),
    "quality_assurer": ("Quality Assurer", "council"),
    "independent_assurer_lead": ("Independent Assurer Lead", "assurer"),
    "independent_assurer": ("Independent Assurer", "assurer"),
    "independent_assurer_qa": ("Independent Assurer QA", "assurer"),
    "mhclg_super_user": ("MHCLG Super User", "mhclg"),
    "mhclg_assurance_lead": ("MHCLG Assurance Lead", "mhclg"),
}


class SeedUserRolesMigrationTest(TransactionTestCase):
    migrate_from = [("webcaf", "0046_user_profile_roles")]
    migrate_to = [("webcaf", "0047_seed_user_roles")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)

    def setUp(self):
        super().setUp()
        latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(self.migrate, latest_targets)
        self.migrate(self.migrate_from)
        # The data-only reverse intentionally preserves roles. Clear only the
        # disposable test database's catalogue to simulate an unseeded environment.
        UserRole.objects.all().delete()

    def create_assignment(self, role):
        user = User.objects.create_user(username="seed-test-member")
        organisation = Organisation.objects.create(name="Seed test organisation")
        profile = UserProfile.objects.create(user=user, organisation=organisation, role="organisation_user")
        return UserProfileRole.objects.create(
            user_profile=profile, user_role=role, organisation=organisation, granted_by=user
        )

    def test_seeds_exactly_the_eleven_agreed_entries_with_expected_names_and_groups(self):
        self.migrate(self.migrate_to)

        actual = {
            code: (name, group) for code, name, group in UserRole.objects.values_list("code", "role_name", "group")
        }
        self.assertEqual(UserRole.objects.count(), 11)
        self.assertEqual(actual, EXPECTED_ROLES)
        self.assertEqual(set(actual), SUPPORTED_ROLE_CODES)
        self.assertNotIn("council_claimant", actual)

    def test_database_rejects_duplicate_codes_for_each_seeded_role(self):
        self.migrate(self.migrate_to)

        for code, (name, group) in EXPECTED_ROLES.items():
            with self.subTest(code=code):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    UserRole.objects.create(code=code, role_name=name, group=group)

    def test_reapplying_seed_does_not_duplicate_roles_or_change_their_ids(self):
        self.migrate(self.migrate_to)
        expected = list(UserRole.objects.order_by("pk").values())

        self.migrate(self.migrate_from)
        self.migrate(self.migrate_to)

        self.assertEqual(list(UserRole.objects.order_by("pk").values()), expected)
        # Automatically allocated IDs remain safe for subsequent catalogue inserts.
        extra_role = UserRole.objects.create(code="additional-role", role_name="Additional role", group="council")
        self.assertNotIn(extra_role.pk, [role["id"] for role in expected])

    def test_existing_codes_keep_their_ids_assignments_history_and_unrelated_entries(self):
        unrelated = UserRole.objects.create(code="unrelated-role", role_name="Existing custom role", group="mhclg")
        role = UserRole.objects.create(code="caf_lead", role_name="Pre-seed label", group="council")
        role_id = role.pk
        assignment = self.create_assignment(role)
        expected_assignment = UserProfileRole.objects.values().get(pk=assignment.pk)
        expected_history = list(assignment.history.order_by("history_id").values())

        self.migrate(self.migrate_to)

        role.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(role.pk, role_id)
        self.assertEqual(role.code, "caf_lead")
        self.assertEqual(role.role_name, "CAF Lead")
        self.assertEqual(role.group, "council")
        self.assertEqual(UserRole.objects.count(), 12)
        self.assertEqual(unrelated.role_name, "Existing custom role")
        self.assertEqual(UserProfileRole.objects.values().get(pk=assignment.pk), expected_assignment)
        self.assertEqual(list(assignment.history.order_by("history_id").values()), expected_history)

    def test_data_only_reverse_preserves_catalogue_assignments_and_history(self):
        self.migrate(self.migrate_to)
        assignment = self.create_assignment(UserRole.objects.get(code="caf_lead"))
        expected_roles = list(UserRole.objects.order_by("pk").values())
        expected_assignment = UserProfileRole.objects.values().get(pk=assignment.pk)
        expected_history = list(assignment.history.order_by("history_id").values())

        self.migrate(self.migrate_from)

        self.assertEqual(list(UserRole.objects.order_by("pk").values()), expected_roles)
        self.assertEqual(UserProfileRole.objects.values().get(pk=assignment.pk), expected_assignment)
        self.assertEqual(list(assignment.history.order_by("history_id").values()), expected_history)

    def test_seeding_does_not_rewrite_legacy_profiles_or_create_assignments(self):
        user = User.objects.create_user(username="legacy-seed-test-member")
        organisation = Organisation.objects.create(name="Legacy seed organisation", organisation_type="mhclg")
        profile = UserProfile.objects.create(user=user, organisation=organisation, role="reviewer")
        expected_profile = UserProfile.objects.values().get(pk=profile.pk)
        expected_history = list(profile.history.order_by("history_id").values())

        self.migrate(self.migrate_to)

        self.assertEqual(UserProfile.objects.values().get(pk=profile.pk), expected_profile)
        self.assertEqual(list(profile.history.order_by("history_id").values()), expected_history)
        self.assertFalse(UserProfileRole.objects.exists())
        self.assertFalse(UserProfileRole.history.exists())
