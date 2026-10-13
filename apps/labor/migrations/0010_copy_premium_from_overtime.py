from django.db import migrations, models


def copy_premium(apps, schema_editor):
    # Existing rates keep their behaviour: the premium follows their overtime setting, holidays stay unpaid.
    LaborRate = apps.get_model("labor", "LaborRate")
    LaborRate.objects.update(weekend_premium=models.F("overtime_eligible"), paid_holidays=False)


class Migration(migrations.Migration):

    dependencies = [("labor", "0009_pay_switches")]

    operations = [migrations.RunPython(copy_premium, migrations.RunPython.noop)]
