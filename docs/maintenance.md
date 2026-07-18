# Maintenance Notes

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app aeronautics_members.app:create_app db-init
flask --app aeronautics_members.app:create_app run --debug
```

## Update Translations

```powershell
.\.venv\Scripts\pybabel.exe extract -F aeronautics_members\babel.cfg -o aeronautics_members\messages.pot aeronautics_members
.\.venv\Scripts\pybabel.exe update -i aeronautics_members\messages.pot -d aeronautics_members\translations -D messages
.\.venv\Scripts\pybabel.exe compile -d aeronautics_members\translations -D messages -f
```

## Database Migrations

The schema is managed with Alembic (via Flask-Migrate). Migration scripts live
in `migrations/versions/`.

`db-init` is safe to run on every deploy and picks the right action
automatically:

- **Fresh database** – runs the migrations to build the whole schema.
- **Legacy database** created before migrations existed – reconciles legacy
  columns, creates any new tables, and stamps it at the baseline revision.
- **Already migrated** – applies any pending migrations.

```powershell
flask --app aeronautics_members.app:create_app db-init
```

To change the schema, edit the models in `db_models.py`, then autogenerate and
review a migration:

```powershell
$env:FLASK_APP = "aeronautics_members.app:create_app"
flask db migrate -m "describe the change"   # writes migrations/versions/<id>_*.py
# review the generated script, then apply it
flask db upgrade
```

On existing servers, apply new migrations during an update with
`flask db upgrade` (or simply re-run `db-init`, which upgrades to head).

## Log Retention

Audit logs and notification delivery records grow over time. A monthly
`cleanup-logs` timer (installed by `install.sh`) prunes only old rows. Retention
is generous by design and configurable via environment variables (0 = keep
forever):

- `AUDIT_LOG_RETENTION_DAYS` (default `0`) — audit logs are kept forever unless
  you set a positive number of days.
- `NOTIFICATION_RETENTION_DAYS` (default `365`) — notification delivery records
  older than a year are pruned.

Run it manually at any time (flags override the environment defaults):

```bash
flask --app aeronautics_members.app:create_app cleanup-logs
flask --app aeronautics_members.app:create_app cleanup-logs --audit-days 1095 --notification-days 365
```

## Run the Tests

The test suite runs against an ephemeral SQLite database (no MySQL, Redis, or
Stripe credentials required). Install the dev dependencies and run pytest:

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

- `tests/test_membership_cycle.py` covers the proration / calendar-year billing
  math and date helpers with no database.
- `tests/test_webhook.py` drives the Stripe webhook end to end (signature
  verification and side-effecting helpers are stubbed) and verifies webhook
  idempotency.

Tests point the app at SQLite via the `DATABASE_URL` environment variable, which
`create_app` honors as an override for the default MySQL connection string.

## Linting

Ruff runs the pyflakes checks (real bugs: undefined names, unused imports):

```powershell
ruff check .
```

## Continuous Integration

`.github/workflows/ci.yml` runs `ruff check` and the pytest suite on every push
and pull request. The tests use SQLite, so CI needs no database or other
services.

## Send a Welcome Email Manually

```powershell
flask --app aeronautics_members.app:create_app send-welcome-email "member@example.com"
```
