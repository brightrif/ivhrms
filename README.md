# ivhrms

# Virtual environment information

## create new venv

python -m venv ivenv

## Activate env

source ivenv/Scripts/activate

# Git command for first time

echo "# ivhrms" >> README.md
git init
git add README.md
git commit -m "first commit"
git branch -M main
git remote add origin git@github.com:brightrif/ivhrms.git
git push -u origin main

# Git for update

git add .
git commit -m "comments"
git push -u origin main

# Istalled package

pip install django django-environ "psycopg[binary]" celery redis
pip freeze > requirements.txt

# Colour roles

Colour Hex Used for
Deep blue #31629C Primary buttons, links, focus rings
Navy #233460 Sidebar, headings
Sky blue #55AED0 Active-menu marker and icons on the navy sidebar
Mid blue #4EA0C6 Spare accent for charts and highlights later
