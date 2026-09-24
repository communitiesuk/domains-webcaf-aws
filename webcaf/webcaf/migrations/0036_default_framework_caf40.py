from datetime import datetime

from django.db import migrations
from django.utils import timezone

OLD_FRAMEWORK = "caf32"
NEW_FRAMEWORK = "caf40"

# Matches the format written by 0016_configuration_name, e.g. "31 March 2027 11:59pm".
PERIOD_END_FORMAT = "%d %B %Y %I:%M%p"


def _is_still_open(config_data, now):
    """Whether this configuration period ends in the future.

    A period that has already closed is a record of what that period ran on, so
    it keeps the framework it was assessed against. An unparseable or missing
    end date is treated as open, since the period cannot be shown to have ended.
    """
    raw = config_data.get("assessment_period_end")
    if not raw:
        return True
    try:
        return datetime.strptime(raw, PERIOD_END_FORMAT) >= now
    except (TypeError, ValueError):
        return True


def _switch_open_periods(apps, from_framework, to_framework):
    Configuration = apps.get_model("webcaf", "Configuration")
    now = datetime.now(tz=timezone.get_current_timezone()).replace(tzinfo=None)
    for configuration in Configuration.objects.all():
        config_data = configuration.config_data
        if config_data.get("default_framework") != from_framework:
            # A framework already chosen through the admin is left alone.
            continue
        if not _is_still_open(config_data, now):
            continue
        config_data["default_framework"] = to_framework
        configuration.save(update_fields=["config_data"])


def set_caf40_as_default(apps, schema_editor):
    _switch_open_periods(apps, OLD_FRAMEWORK, NEW_FRAMEWORK)


def restore_caf32_as_default(apps, schema_editor):
    _switch_open_periods(apps, NEW_FRAMEWORK, OLD_FRAMEWORK)


class Migration(migrations.Migration):
    """CAF 4.0 becomes the framework new assessments start on.

    The configuration period is what the assessment creation flow reads
    (views/assesment.py), so changing the model default alone leaves new
    assessments on 3.2.

    Only periods that have not yet ended are moved. A closed period records the
    framework its assessments were carried out against and must keep it — 25/26
    ran on CAF 3.2. Configuration.objects.get_default_config() only ever selects
    a period whose end date is in the future, so leaving closed periods alone
    has no effect on which framework a new assessment gets.

    This makes the migration depend on when it runs, which is intended: it moves
    whatever is still open at that point.

    Existing assessments are unaffected either way, each one stores its own
    framework and keeps it.
    """

    dependencies = [
        ("webcaf", "0035_alter_assessment_framework_and_more"),
    ]

    operations = [
        migrations.RunPython(set_caf40_as_default, restore_caf32_as_default),
    ]
