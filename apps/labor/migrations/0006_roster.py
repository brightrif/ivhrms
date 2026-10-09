from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labor", "0005_overtime_applies"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="laborallocation", name="uniq_allocation_start"),
        migrations.RemoveConstraint(model_name="laborallocation", name="one_open_allocation_per_worker"),
        migrations.AddField(
            model_name="laborallocation",
            name="is_main",
            field=models.BooleanField(default=False, help_text="The worker's main project: where attendance and hours start from."),
        ),
        migrations.AddField(
            model_name="laborprofile",
            name="serves_all_projects",
            field=models.BooleanField(default=False, help_text="Drivers, storekeepers and others who work for every project. They appear on every active project's team."),
        ),
        migrations.AddConstraint(
            model_name="laborallocation",
            constraint=models.UniqueConstraint(fields=("employee", "project", "effective_from"), name="uniq_allocation_start_per_project"),
        ),
        migrations.AddConstraint(
            model_name="laborallocation",
            constraint=models.UniqueConstraint(condition=models.Q(("effective_to__isnull", True)), fields=("employee", "project"), name="one_open_allocation_per_worker_per_project"),
        ),
        migrations.AddConstraint(
            model_name="laborallocation",
            constraint=models.UniqueConstraint(condition=models.Q(("effective_to__isnull", True), ("is_main", True)), fields=("employee",), name="one_main_allocation_per_worker"),
        ),
    ]
