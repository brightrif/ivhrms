python manage.py shell -c "from django.conf import settings; print(settings.SETTINGS_MODULE); print(settings.INSTALLED_APPS)"

find apps -path "_/migrations/_.py" ! -name "**init**.py" -delete

python manage.py shell -c "
from django.conf import settings
print(settings.TEMPLATES[0]['DIRS'])
print((settings.BASE_DIR / 'templates/django/forms/field.html').exists())
"
ls -la templates/django/forms
