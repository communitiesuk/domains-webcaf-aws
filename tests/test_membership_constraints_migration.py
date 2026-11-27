from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from webcaf.webcaf.models import Organisation, UserProfile, UserProfileRole


class MembershipConstraintMigrationTest(TransactionTestCase):
    before = [("webcaf", "0045_alter_organisation_type_required")]
    after = [("webcaf", "0046_user_profile_roles")]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)

    def setUp(self):
        super().setUp()
        self.latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(self.restore_schema)
        self.migrate(self.before)
        self.user = User.objects.create_user(username="membership-migration-member")
        self.organisation = Organisation.objects.create(
            name="Membership migration organisation", organisation_type="mhclg"
        )

    def restore_schema(self):
        # Invalid fixtures are removed only from the disposable test database.
        if "webcaf_userprofilerole" in connection.introspection.table_names():
            UserProfileRole.objects.all().delete()
        old_apps = MigrationExecutor(connection).loader.project_state(self.before).apps
        old_apps.get_model("webcaf", "UserProfile").objects.all().delete()
        self.migrate(self.latest_targets)

    def test_preflight_refuses_missing_organisations_without_modifying_data(self):
        profile = UserProfile.objects.create(user=self.user, role="reviewer")
        before = list(UserProfile.objects.values())
        history = list(profile.history.values())

        with self.assertRaisesMessage(RuntimeError, "1 profiles without an organisation"):
            self.migrate(self.after)

        self.assertEqual(list(UserProfile.objects.values()), before)
        self.assertEqual(list(profile.history.values()), history)
        self.assertNotIn("webcaf_userrole", connection.introspection.table_names())

    def test_preflight_refuses_duplicate_memberships_without_merging_or_deleting(self):
        for role in ["organisation_user", "organisation_lead"]:
            UserProfile.objects.create(user=self.user, organisation=self.organisation, role=role)
        before = list(UserProfile.objects.order_by("pk").values())

        with self.assertRaisesMessage(RuntimeError, "1 duplicate user/organisation groups"):
            self.migrate(self.after)

        self.assertEqual(list(UserProfile.objects.order_by("pk").values()), before)
        self.assertNotIn("webcaf_userrole", connection.introspection.table_names())

    def test_valid_data_is_preserved_and_both_database_constraints_are_applied(self):
        profile = UserProfile.objects.create(user=self.user, organisation=self.organisation, role="organisation_user")
        before = UserProfile.objects.values().get(pk=profile.pk)

        self.migrate(self.after)

        self.assertEqual(UserProfile.objects.values().get(pk=profile.pk), before)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=self.user, role="reviewer")
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=self.user, organisation=self.organisation, role="reviewer")
        other_org = Organisation.objects.create(name="Other membership organisation", organisation_type="mhclg")
        UserProfile.objects.create(user=self.user, organisation=other_org)
        other_user = User.objects.create_user(username="other-membership-member")
        UserProfile.objects.create(user=other_user, organisation=self.organisation)

    def test_historical_profiles_can_still_represent_a_missing_organisation(self):
        self.migrate(self.after)
        row = UserProfile.history.create(
            id=9876,
            user_id=self.user.pk,
            organisation_id=None,
            role="reviewer",
            history_type="+",
            history_date=timezone.now(),
        )
        self.assertIsNone(row.organisation_id)
