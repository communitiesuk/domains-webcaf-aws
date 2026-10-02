from contextlib import contextmanager

from django.contrib.auth.models import User
from django.db import DatabaseError, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from webcaf.webcaf.models import Organisation, System, UserProfile


class RestrictedRoleMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO webcaf_app")
            cursor.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO webcaf_app")

    @contextmanager
    def app_scope(self, *, organisation_id="", user_id="", access_scope="tenant"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE webcaf_app")
                cursor.execute("SELECT set_config('webcaf.user_id', %s, true)", [str(user_id)])
                cursor.execute("SELECT set_config('webcaf.organisation_id', %s, true)", [str(organisation_id)])
                cursor.execute("SELECT set_config('webcaf.access_scope', %s, true)", [access_scope])
            yield


class SystemRLSTests(RestrictedRoleMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.organisation_a = Organisation.objects.create(name="RLS organisation A")
        self.organisation_b = Organisation.objects.create(name="RLS organisation B")
        self.system_a = System.objects.create(name="RLS system A", organisation=self.organisation_a)
        self.system_b = System.objects.create(name="RLS system B", organisation=self.organisation_b)
        self.staff_user = User.objects.create_user(username="rls-admin", is_staff=True)
        self.non_staff_user = User.objects.create_user(username="rls-user")

    def test_restricted_role_and_policy_configuration_are_active(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT current_user, session_user, roles.rolsuper, roles.rolbypassrls,
                           pg_get_userbyid(tables.relowner), tables.relrowsecurity, tables.relforcerowsecurity
                    FROM pg_roles AS roles
                    CROSS JOIN pg_class AS tables
                    WHERE roles.rolname = current_user
                      AND tables.relname = 'webcaf_system'
                    """)
                current_user, session_user, is_superuser, bypasses_rls, owner, rls_enabled, rls_forced = (
                    cursor.fetchone()
                )

        self.assertEqual(current_user, "webcaf_app")
        self.assertNotEqual(session_user, current_user)
        self.assertFalse(is_superuser)
        self.assertFalse(bypasses_rls)
        self.assertNotEqual(owner, current_user)
        self.assertTrue(rls_enabled)
        self.assertTrue(rls_forced)

    def test_tenant_can_only_select_its_system_and_history(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            self.assertEqual(list(System.objects.values_list("id", flat=True)), [self.system_a.id])
            self.assertEqual(
                list(System.history.values_list("id", flat=True).order_by("id").distinct()),
                [self.system_a.id],
            )
            with self.assertRaises(System.DoesNotExist):
                System.objects.get(id=self.system_b.id)

    def test_tenant_can_create_update_and_delete_its_system(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            system = System.objects.create(name="Created in A", organisation=self.organisation_a)
            system_id = system.id
            history_count = System.history.filter(id=system_id).count()
            system.name = "Updated in A"
            system.save(update_fields=["name"])

            self.assertEqual(System.history.filter(id=system_id).count(), history_count + 1)
            system.delete()
            self.assertEqual(System.history.filter(id=system_id).count(), history_count + 2)

    def test_tenant_cannot_insert_or_move_system_to_another_organisation(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            with self.assertRaises(DatabaseError), transaction.atomic():
                System.objects.create(name="Wrong tenant", organisation=self.organisation_b)

            self.system_a.organisation = self.organisation_b
            with self.assertRaises(DatabaseError), transaction.atomic():
                self.system_a.save(update_fields=["organisation"])

    def test_tenant_cannot_update_or_delete_another_tenants_system(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            self.assertEqual(System.objects.filter(id=self.system_b.id).update(name="Hidden update"), 0)
            deleted_count, _ = System.objects.filter(id=self.system_b.id).delete()
            self.assertEqual(deleted_count, 0)

    def test_missing_context_exposes_no_system_rows(self):
        with self.app_scope(access_scope="public"):
            self.assertFalse(System.objects.exists())
            self.assertFalse(System.history.exists())

    def test_valid_staff_admin_scope_can_access_all_system_rows(self):
        with self.app_scope(user_id=self.staff_user.id, access_scope="admin"):
            self.assertCountEqual(
                System.objects.values_list("id", flat=True),
                [self.system_a.id, self.system_b.id],
            )

    def test_non_staff_admin_scope_exposes_no_system_rows(self):
        with self.app_scope(user_id=self.non_staff_user.id, access_scope="admin"):
            self.assertFalse(System.objects.exists())

    def test_context_does_not_leak_when_connection_is_reused(self):
        with self.app_scope(organisation_id=self.organisation_a.id):
            self.assertEqual(list(System.objects.values_list("id", flat=True)), [self.system_a.id])

        with self.app_scope(organisation_id=self.organisation_b.id):
            self.assertEqual(list(System.objects.values_list("id", flat=True)), [self.system_b.id])

        with self.app_scope(access_scope="public"):
            self.assertFalse(System.objects.exists())

    def test_context_does_not_leak_after_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.app_scope(organisation_id=self.organisation_a.id):
                self.assertEqual(list(System.objects.values_list("id", flat=True)), [self.system_a.id])
                raise RuntimeError("force rollback")

        with self.app_scope(access_scope="public"):
            self.assertFalse(System.objects.exists())


class SystemRLSRequestTests(RestrictedRoleMixin, TransactionTestCase):
    def setUp(self):
        self.organisation_a = Organisation.objects.create(name="Request organisation A")
        self.organisation_b = Organisation.objects.create(name="Request organisation B")
        self.system_a = System.objects.create(name="Request system A", organisation=self.organisation_a)
        self.system_b = System.objects.create(name="Request system B", organisation=self.organisation_b)

        self.user = User.objects.create_user(username="request-user")
        self.profile_a = UserProfile.objects.create(
            user=self.user,
            organisation=self.organisation_a,
            role="organisation_user",
        )
        self.foreign_user = User.objects.create_user(username="foreign-user")
        self.foreign_profile = UserProfile.objects.create(
            user=self.foreign_user,
            organisation=self.organisation_b,
            role="organisation_user",
        )
        self.client = Client()
        self.client.force_login(self.user)
        self._use_restricted_role()

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        super().tearDown()

    def _use_restricted_role(self):
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE webcaf_app")

    def test_first_landing_sets_context_before_system_query(self):
        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_profile"].id, self.profile_a.id)
        self.assertEqual(response.context["system_count"], 1)

    def test_foreign_session_profile_cannot_set_database_context(self):
        session = self.client.session
        session["current_profile_id"] = self.foreign_profile.id
        session.save()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_profile"].id, self.profile_a.id)
        self.assertEqual(response.context["system_count"], 1)
        self.assertEqual(self.client.session["current_profile_id"], self.profile_a.id)

    def test_stale_session_profile_falls_back_to_an_owned_profile(self):
        session = self.client.session
        session["current_profile_id"] = 999999
        session.save()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_profile"].id, self.profile_a.id)
        self.assertEqual(response.context["system_count"], 1)

    def test_profile_switch_changes_context_on_redirected_request(self):
        profile_b = UserProfile.objects.create(
            user=self.user,
            organisation=self.organisation_b,
            role="organisation_user",
        )

        response = self.client.post(
            reverse("change-organisation"),
            {"profile_id": profile_b.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_profile"].id, profile_b.id)
        self.assertEqual(response.context["system_count"], 1)
        self.assertEqual(self.client.session["current_profile_id"], profile_b.id)

    def test_staff_admin_request_can_access_all_systems(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        staff_user = User.objects.create_superuser(username="request-admin", password="password")
        self.client.force_login(staff_user)
        self._use_restricted_role()

        response = self.client.get(reverse("admin:webcaf_system_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.system_a.name)
        self.assertContains(response, self.system_b.name)

    def test_staff_request_outside_admin_remains_tenant_scoped(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        staff_user = User.objects.create_user(username="tenant-staff", is_staff=True)
        staff_profile = UserProfile.objects.create(
            user=staff_user,
            organisation=self.organisation_a,
            role="organisation_user",
        )
        self.client.force_login(staff_user)
        session = self.client.session
        session["current_profile_id"] = staff_profile.id
        session.save()
        self._use_restricted_role()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["system_count"], 1)

    def test_user_without_profile_cannot_access_system_rows(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        no_profile_user = User.objects.create_user(username="no-profile")
        self.client.force_login(no_profile_user)
        self._use_restricted_role()

        response = self.client.get(reverse("my-account"))

        self.assertEqual(response.status_code, 403)
