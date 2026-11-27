from django.db import migrations

# Freeze the agreed AC2 catalogue here rather than importing runtime definitions.
# Codes identify roles; numeric IDs are deliberately not assigned by this seed.
ROLE_CATALOGUE = (
    ("caf_lead", "CAF Lead", "council"),
    ("deputy_caf_lead", "Deputy CAF Lead", "council"),
    ("collaborator", "Collaborator", "council"),
    ("systems_mapper", "Systems Mapper", "council"),
    ("approver", "Approver", "council"),
    ("quality_assurer", "Quality Assurer", "council"),
    ("independent_assurer_lead", "Independent Assurer Lead", "assurer"),
    ("independent_assurer", "Independent Assurer", "assurer"),
    ("independent_assurer_qa", "Independent Assurer QA", "assurer"),
    ("mhclg_super_user", "MHCLG Super User", "mhclg"),
    ("mhclg_assurance_lead", "MHCLG Assurance Lead", "mhclg"),
)


def seed_user_roles(apps, schema_editor):
    UserRole = apps.get_model("webcaf", "UserRole")
    for code, role_name, group in ROLE_CATALOGUE:
        UserRole.objects.using(schema_editor.connection.alias).update_or_create(
            code=code,
            defaults={"role_name": role_name, "group": group},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("webcaf", "0046_user_profile_roles"),
    ]

    operations = [
        # A data-only rollback must not delete pre-existing catalogue entries or
        # roles subsequently referenced by assignments/history. Reapply by code.
        migrations.RunPython(seed_user_roles, reverse_code=migrations.RunPython.noop),
    ]
