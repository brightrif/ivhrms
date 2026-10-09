from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labor", "0007_roster_backfill"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="timeentry", name="uniq_time_entry_employee_date"),
        migrations.AddConstraint(
            model_name="timeentry",
            constraint=models.UniqueConstraint(fields=("employee", "date", "project"), name="uniq_time_entry_employee_date_project"),
        ),
    ]
