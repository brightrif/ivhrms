from django.db import migrations


def mark_existing_as_main(apps, schema_editor):
    """Until now a worker had exactly one project at a time, so every existing row was their main one."""
    apps.get_model("labor", "LaborAllocation").objects.update(is_main=True)


class Migration(migrations.Migration):

    dependencies = [
        ("labor", "0006_roster"),
    ]

    operations = [
        migrations.RunPython(mark_existing_as_main, migrations.RunPython.noop),
    ]
