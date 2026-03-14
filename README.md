# jaeronautics

Membership management app for Joanneum Aeronautics, built with Flask, Stripe, SQLAlchemy, and Flask-Babel.

## Quick Install

Run this on a clean Linux server. It bootstraps `curl` if needed, downloads the latest installer, and then the installer handles install, update, repair, or full uninstall.

```bash
bash -lc 'set -e; SUDO=""; if [ "$(id -u)" -ne 0 ]; then if ! command -v sudo >/dev/null 2>&1; then echo "Please run as root or install sudo."; exit 1; fi; SUDO="sudo"; fi; if command -v curl >/dev/null 2>&1; then FETCH="curl -fsSL"; elif command -v wget >/dev/null 2>&1; then FETCH="wget -qO-"; else if command -v apt-get >/dev/null 2>&1; then $SUDO apt-get update && $SUDO apt-get install -y curl; elif command -v dnf >/dev/null 2>&1; then $SUDO dnf install -y curl; elif command -v yum >/dev/null 2>&1; then $SUDO yum install -y curl; else echo "Could not install curl automatically."; exit 1; fi; FETCH="curl -fsSL"; fi; $FETCH https://raw.githubusercontent.com/angeeinstein/jaeronautics/main/install.sh | $SUDO bash'
```

During installation the script can ask for:

- the domain for nginx
- whether to use a local MariaDB instance
- Stripe keys and price/webhook configuration
- whether HTTPS should be enabled with Let's Encrypt

## Repository Layout

```text
jaeronautics/
|-- aeronautics_members/    # Flask app package
|-- install.sh              # Linux install/update/repair/uninstall entrypoint
|-- deploy/                 # Example nginx and systemd files
|-- docs/                   # Small operational notes
|-- .env.example            # Safe environment template
|-- requirements.txt
`-- wsgi.py                 # Gunicorn entrypoint
```

## Features

- Public membership signup flow
- Stripe Checkout, SEPA, and invoice-based subscriptions
- Admin login and settings area
- Welcome email sending
- English and German translations

## Local Setup

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` and fill in your real values.
3. Initialize the database tables.
4. Run the Flask app.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app aeronautics_members.app:create_app db-init
flask --app aeronautics_members.app:create_app run --debug
```

## Production

- Gunicorn entrypoint: `wsgi:application`
- Installer entrypoint: `sudo bash install.sh`
- Example service file: `deploy/systemd/aeronautics.service`
- Example nginx config: `deploy/nginx/aeronautics.conf`

## Installer Modes

The Linux installer is lifecycle-aware:

- On a fresh host it installs packages, clones or updates the repo, provisions MariaDB/nginx/systemd, writes `.env`, and starts the app.
- On an existing installation it offers `update`, `repair/reconfigure`, or `uninstall`.
- It first syncs the repository copy of `install.sh` and then re-runs itself so the current session always uses the newest installer logic.

You can also run it directly from a cloned checkout:

```bash
sudo bash install.sh --mode update
sudo bash install.sh --mode repair
sudo bash install.sh --mode uninstall
```

## Notes

- Secrets are intentionally not committed. Keep them in `.env`.
- If credentials from an earlier push ever reached GitHub, rotate them before continuing to use the project.
- The app still falls back to the legacy `var/www/aeronautics-members/.env` location so the current local setup keeps working during the transition.
