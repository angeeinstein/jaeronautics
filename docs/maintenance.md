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

## Send a Welcome Email Manually

```powershell
flask --app aeronautics_members.app:create_app send-welcome-email "member@example.com"
```
