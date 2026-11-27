# CS-816 membership and role schema, following the merged CS-680 migrations.

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


def check_memberships(apps, schema_editor):
    UserProfile = apps.get_model("webcaf", "UserProfile")
    profiles = UserProfile.objects.using(schema_editor.connection.alias)
    missing_organisations = profiles.filter(organisation_id__isnull=True).count()
    duplicate_memberships = (
        profiles.values("user_id", "organisation_id").annotate(total=models.Count("id")).filter(total__gt=1).count()
    )
    if missing_organisations or duplicate_memberships:
        raise RuntimeError(
            "CS-816 membership preflight failed: "
            f"{missing_organisations} profiles without an organisation; "
            f"{duplicate_memberships} duplicate user/organisation groups. "
            "Run the separately approved CS-680 reset first in Sandbox. "
            "Other environments require approved data preparation. No profiles were modified."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("webcaf", "0045_alter_organisation_type_required"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(check_memberships, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="userprofile",
            name="organisation",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="members", to="webcaf.organisation"
            ),
        ),
        migrations.AddConstraint(
            model_name="userprofile",
            constraint=models.UniqueConstraint(
                fields=("user", "organisation"), name="unique_user_organisation_profile"
            ),
        ),
        migrations.CreateModel(
            name="UserRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=64, unique=True)),
                ("role_name", models.CharField(max_length=255)),
                (
                    "group",
                    models.CharField(
                        choices=[("council", "Council"), ("mhclg", "MHCLG"), ("assurer", "Independent assurer")],
                        max_length=16,
                    ),
                ),
            ],
            options={
                "ordering": ["code"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(group__in=["council", "mhclg", "assurer"]), name="valid_user_role_group"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="HistoricalUserProfileRole",
            fields=[
                ("id", models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name="ID")),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                (
                    "history_type",
                    models.CharField(choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")], max_length=1),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organisation",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="webcaf.organisation",
                    ),
                ),
                (
                    "user_profile",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="webcaf.userprofile",
                    ),
                ),
                (
                    "user_role",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="webcaf.userrole",
                    ),
                ),
            ],
            options={
                "verbose_name": "historical user profile role",
                "verbose_name_plural": "historical user profile roles",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name="UserProfileRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("granted_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="profile_role_grants",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organisation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_assignments",
                        to="webcaf.organisation",
                    ),
                ),
                (
                    "user_profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_assignments",
                        to="webcaf.userprofile",
                    ),
                ),
                (
                    "user_role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="profile_assignments",
                        to="webcaf.userrole",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("user_profile", "user_role"), name="unique_profile_role"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="userprofile",
            constraint=models.UniqueConstraint(fields=("id", "organisation"), name="unique_profile_id_organisation"),
        ),
        # Django 5.2 cannot represent this composite FK as a model constraint.
        # Keep it immediate, on both INSERT/UPDATE and parent-key changes.
        migrations.RunSQL(
            sql="""
                ALTER TABLE "webcaf_userprofilerole"
                ADD CONSTRAINT "user_profile_role_matches_organisation"
                FOREIGN KEY ("user_profile_id", "organisation_id")
                REFERENCES "webcaf_userprofile" ("id", "organisation_id")
                ON UPDATE NO ACTION ON DELETE NO ACTION NOT DEFERRABLE
            """,
            reverse_sql="""
                ALTER TABLE "webcaf_userprofilerole"
                DROP CONSTRAINT "user_profile_role_matches_organisation"
            """,
        ),
    ]
