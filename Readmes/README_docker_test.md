# Ivhrms test stack (Docker): nginx, web, Postgres, Redis, Celery

Open **http://localhost:8080**. Port 80 on your machine is not used (nginx listens on 80 only inside its container).

| Service | What it does |
|---|---|
| `nginx` | The front door. Serves the static files itself and passes everything else to the app. The only service published to your machine |
| `web` | The app under gunicorn. On every start it runs `migrate` and `collectstatic` |
| `db` | Postgres 16, its own empty database |
| `redis` | Message broker for Celery |
| `celery` | Worker and scheduler in one: the daily compliance scan (07:00) and vehicle scan (07:15), and the emails they send |

## Files (copy into the project folder, keeping the folders)

```
Dockerfile
.dockerignore
docker-compose.yml
docker/nginx.conf
config/settings/docker.py        (a new settings file; nothing existing is edited)
```

## Start (Git Bash)

```bash
cd /c/Django/Projects/Ivhrms
cp /c/Django/docker_test/Dockerfile /c/Django/docker_test/.dockerignore /c/Django/docker_test/docker-compose.yml .
cp -r /c/Django/docker_test/docker .
cp /c/Django/docker_test/config/settings/docker.py config/settings/
docker compose up -d --build
docker compose ps                         # wait until web shows "healthy" (the first start takes a minute or two)
docker compose exec web python manage.py createsuperuser
```

If 8080 is taken: `HRMS_PORT=8090 docker compose up -d`, then use 8090.

## Everyday

| Do this | Command |
|---|---|
| Logs of everything / one service | `docker compose logs -f` / `docker compose logs -f web` |
| Emails the system sends (console backend) | `docker compose logs -f celery` and `web` |
| Run a daily scan now | `docker compose exec web python manage.py run_compliance_scan` (and `run_vehicle_scan`) |
| Run the tests | `docker compose exec web python manage.py test apps.payroll` |
| Stop, keep the data | `docker compose down` |
| Stop and wipe the data | `docker compose down -v` |
| After changing code | `docker compose up -d --build` |

## Settings you can change (set before `docker compose up`, in Git Bash: `HRMS_PORT=8090 docker compose up -d`)

`HRMS_PORT` (default 8080), `HRMS_SECRET_KEY`, `HRMS_ALLOWED_HOSTS` (default `*`), `HRMS_CSRF_ORIGINS` (needed only for HTTPS).

## Things to know

- This is a **test** stack. Before it is used for real: a real secret key and database password, specific host names, HTTPS
  (nginx certificate, `CSRF_TRUSTED_ORIGINS`, and secure-cookie settings), a real email backend (it prints to the log now), and
  backups of the `pgdata` and `private_media` volumes.
- The data is new: this is a separate Postgres, not your local SQLite.
- Uploaded files in `private_media` (certificate scans, receipts) are never served by nginx; only the app can send them.
- If the build complains about `requirements.txt`, re-save it as UTF-8 (PowerShell can write it as UTF-16).
