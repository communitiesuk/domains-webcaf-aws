from django.db import migrations

OLD_FRAMEWORK = "caf32"
NEW_FRAMEWORK = "caf40"

# The configuration period new assessments are created against for MVP /
# Private Beta. Named explicitly so that applying and reversing this migration
# always act on the same record, whenever they are run.
#
# 25/26 is deliberately not listed. That period is a record of what its
# assessments were carried out against, and they were carried out against
# CAF 3.2.
#
# 0025_update_configuration addresses the same records by name.
TARGET_PERIODS = ["26/27"]


def _set_default_framework(apps, framework):
    Configuration = apps.get_model("webcaf", "Configuration")
    # filter() rather than get(): an environment without these periods is left
    # alone rather than failing the migration.
    for configuration in Configuration.objects.filter(name__in=TARGET_PERIODS):
        configuration.config_data["default_framework"] = framework
        configuration.save(update_fields=["config_data"])


def set_caf40_as_default(apps, schema_editor):
    _set_default_framework(apps, NEW_FRAMEWORK)


def restore_caf32_as_default(apps, schema_editor):
    _set_default_framework(apps, OLD_FRAMEWORK)


class Migration(migrations.Migration):
    """CAF 4.0 becomes the framework new assessments start on.

    The configuration period is what the assessment creation flow reads
    (views/assesment.py), so changing the model default alone leaves new
    assessments on 3.2.

    Only the periods named in TARGET_PERIODS are touched, and both directions
    act on exactly that set. Reversing therefore restores the same records the
    forward migration changed, no matter how much time has passed or what the
    values happen to be.

    Existing assessments are unaffected: each one stores its own framework and
    keeps it.
    """

    dependencies = [
        ("webcaf", "0035_alter_assessment_framework_and_more"),
    ]

    operations = [
        migrations.RunPython(set_caf40_as_default, restore_caf32_as_default),
    ]
