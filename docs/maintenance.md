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
pip install --require-hashes -r requirements-dev.lock
python -m pytest
```

- `tests/test_membership_cycle.py` covers the proration / calendar-year billing
  math and date helpers with no database.
- `tests/test_webhook.py` drives the Stripe webhook end to end (signature
  verification and side-effecting helpers are stubbed) and verifies webhook
  idempotency.

Tests point the app at SQLite via the `DATABASE_URL` environment variable, which
`create_app` honors as an override for the default MySQL connection string.

## Data Export and Account Deletion

`aeronautics_members/services/privacy.py` implements the two data-protection
rights that need code. Both are self-service, and both have an admin equivalent
for requests that arrive by email or on paper.

**Export** — `/account/data-export`, or
`/admin/accounts/<id>/data-export`. Downloads everything held about one account
as JSON: profile, membership periods and invoice ids, change requests, forum
link, emails sent, and the audit entries about that account. It deliberately
leaves out actions the person took *on other people*, since those entries
describe somebody else.

**Deletion is anonymisation, not `DELETE`.** The personal columns are
overwritten and the rows stay. Two reasons, and both would have to stop applying
before that could change:

- The membership periods and their Stripe invoice ids are an accounting record.
  Under § 132 BAO that has to be kept for seven years, and GDPR Art. 17(3)(b)
  yields to exactly that kind of obligation.
- `membership_periods`, `audit_logs`, notification and email history all
  reference the member and the user. Deleting a user who was ever an
  administrator would take the record of everything they did with it.

Erased rows are marked with `deleted_at` and kept indefinitely; there is no
purge job, by decision rather than by omission.

What erasure does, in order:

1. **Cancels the Stripe subscription first.** If that fails, nothing is erased —
   otherwise Stripe keeps billing yearly against a customer no longer
   identifiable from this database.
2. **Anonymises the Discourse account**, keeping the posts attached to the now
   anonymous user. A forum outage does not block it: the retry goes on the
   outbox as a `forum_anonymise` work item.
3. **Overwrites the profile**, clears the password and nonces (so outstanding
   reset and verification links die), removes all roles, deletes avatar files
   and pending change requests, and clears the payload of queued emails.
4. **Clears the audit snapshots about that person.** Profile approvals store the
   full before/after profile, so leaving them would leave a second copy of
   exactly what was erased. Entries where the person was the *actor* are left
   alone. The erasure's own audit entry carries no before-state, for the same
   reason.

`load_user` returns `None` for an erased account, so sessions issued before the
erasure stop working at the next request rather than at the next login.

**No refunds.** Cancelling stops future invoices; it does not return money.
Members join for a calendar year knowing that, and a refund is a separate
booking and a board decision — so if one is ever warranted, issue it in the
Stripe dashboard before deleting the account. Nothing in this application moves
money.

**Leaving and deleting are two steps, in that order.** A member who only wants
to stop being a member should cancel in the Stripe portal: the membership then
runs to the end of the period they paid for and simply does not renew. Deleting
the account cancels immediately and forfeits the rest of the year. The account
page and the deletion confirmation page both say so, and offer the cancel route,
when there is still paid time to lose. Deletion is not blocked on it.

Administrators are warned but not blocked when deleting a member with an active
subscription — expulsion is a real case. Two things are refused outright,
because they break the system rather than being a judgement call: erasing the
last remaining administrator, and an administrator erasing themselves from the
admin page (they can use their own account page, where the confirmation goes to
their mailbox).

Stripe keeps its own copy of the customer under its own retention rules. The
export names the Stripe customer id so a member can ask Stripe directly; if you
also want the customer removed there, delete it in the Stripe dashboard.

## Dependencies

`requirements.txt` lists the packages the application asks for.
`requirements.lock` and `requirements-dev.lock` list what actually gets
installed: those packages, everything they in turn depend on, each pinned to an
exact version and checked against a recorded SHA-256 hash. `install.sh` deploys
from `requirements.lock` and CI installs `requirements-dev.lock`, so the
versions under test are the versions in production, and a package that has been
tampered with on the index fails the install instead of being deployed.

After changing `requirements.txt`, regenerate **both** lock files — they are
resolved independently, so updating only one lets the shared pins drift apart:

```powershell
pip install pip-tools
pip-compile --generate-hashes --strip-extras --no-header --output-file=requirements.lock requirements.txt
pip-compile --generate-hashes --strip-extras --no-header --output-file=requirements-dev.lock requirements-dev.txt
```

`pip-compile` strips the explanatory header comments; put them back (git will
show what was removed), then run the tests. `tests/test_dependency_lock.py`
fails if the locks and `requirements.txt` disagree, so a forgotten regeneration
shows up as a red build rather than as a surprise during a deploy.

To take newer versions of the transitive dependencies without changing anything
in `requirements.txt`, add `--upgrade` to both commands.

If a locked version cannot be built on a particular machine — a package with no
wheel for a newer Python, say — `USE_DEPENDENCY_LOCK=0 ./install.sh` falls back
to `requirements.txt`. That install is unpinned and unverified, so treat it as a
way to get unstuck, not as a setting to leave in place.

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
