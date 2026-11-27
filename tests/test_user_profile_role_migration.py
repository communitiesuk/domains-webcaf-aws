from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class UserProfileRoleMigrationTest(TransactionTestCase):
    migrate_from = [("webcaf", "0045_alter_organisation_type_required")]
    migrate_to = [("webcaf", "0046_user_profile_roles")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def test_additive_migration_round_trip_preserves_legacy_profiles_and_history(self):
        # Restore the latest schema even on assertion failure, before the test
        # database is flushed or any other tests use the current models.
        latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(self.migrate, latest_targets)
        old_apps = self.migrate(self.migrate_from)
        user = old_apps.get_model("auth", "User").objects.create(username="legacy-member")
        other_user = old_apps.get_model("auth", "User").objects.create(username="other-legacy-member")
        organisation = old_apps.get_model("webcaf", "Organisation").objects.create(
            name="Legacy organisation", organisation_type="mhclg"
        )
        other_organisation = old_apps.get_model("webcaf", "Organisation").objects.create(
            name="Other legacy organisation", organisation_type="independent-assurer"
        )
        old_profile = old_apps.get_model("webcaf", "UserProfile")
        profiles = [
            old_profile.objects.create(user_id=user.pk, organisation_id=organisation.pk, role="organisation_lead"),
            old_profile.objects.create(
                user_id=user.pk, organisation_id=other_organisation.pk, role="organisation_user"
            ),
            old_profile.objects.create(user_id=other_user.pk, organisation_id=organisation.pk, role="reviewer"),
        ]
        expected_profiles = list(old_profile.objects.order_by("pk").values())
        old_history = old_apps.get_model("webcaf", "HistoricalUserProfile")
        for profile in profiles:
            old_history.objects.create(
                id=profile.pk,
                user_id=profile.user_id,
                organisation_id=profile.organisation_id,
                role=profile.role,
                history_date=user.date_joined,
                history_type="+",
            )
        # Old audit snapshots can still represent an organisation-less profile.
        old_history.objects.create(
            id=9876,
            user_id=user.pk,
            organisation_id=None,
            role="reviewer",
            history_date=user.date_joined,
            history_type="+",
        )
        expected_history = list(old_history.objects.order_by("history_id").values())

        new_apps = self.migrate(self.migrate_to)

        self.assertEqual(
            list(new_apps.get_model("webcaf", "UserProfile").objects.order_by("pk").values()), expected_profiles
        )
        self.assertEqual(
            list(new_apps.get_model("webcaf", "HistoricalUserProfile").objects.order_by("history_id").values()),
            expected_history,
        )
        for model in ["UserRole", "UserProfileRole", "HistoricalUserProfileRole"]:
            with self.subTest(model=model):
                self.assertFalse(new_apps.get_model("webcaf", model).objects.exists())

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT condeferrable FROM pg_constraint
                WHERE conrelid = 'webcaf_userprofilerole'::regclass
                  AND conname = 'user_profile_role_matches_organisation'
                """
            )
            self.assertEqual(cursor.fetchall(), [(False,)])

        role = new_apps.get_model("webcaf", "UserRole").objects.create(
            code="test-role", role_name="Test role", group="council"
        )
        new_apps.get_model("webcaf", "UserProfileRole").objects.create(
            user_profile_id=profiles[0].pk, user_role_id=role.pk, organisation_id=organisation.pk
        )

        restored_apps = self.migrate(self.migrate_from)

        self.assertEqual(
            list(restored_apps.get_model("webcaf", "UserProfile").objects.order_by("pk").values()), expected_profiles
        )
        self.assertEqual(
            list(restored_apps.get_model("webcaf", "HistoricalUserProfile").objects.order_by("history_id").values()),
            expected_history,
        )
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "webcaf_userprofile")
        self.assertNotIn("unique_profile_id_organisation", constraints)
        self.assertNotIn("unique_user_organisation_profile", constraints)
        for table in ["webcaf_userrole", "webcaf_userprofilerole", "webcaf_historicaluserprofilerole"]:
            self.assertNotIn(table, connection.introspection.table_names())

        # Reapplication recreates all schema constraints, including the SQL-only FK.
        self.migrate(self.migrate_to)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "webcaf_userprofilerole")
        self.assertIn("user_profile_role_matches_organisation", constraints)
