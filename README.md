# ivhrms

# Virtual environment information

## create new venv

python -m venv ivenv

## Activate env

source ivenv/Scripts/activate

# Istalled package

pip install django django-environ "psycopg[binary]" celery redis
pip freeze > requirements.txt

# Colour roles

Colour Hex Used for
Deep blue #31629C Primary buttons, links, focus rings
Navy #233460 Sidebar, headings
Sky blue #55AED0 Active-menu marker and icons on the navy sidebar
Mid blue #4EA0C6 Spare accent for charts and highlights later
