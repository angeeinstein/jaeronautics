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

## Updating and Rolling Back

Settings → Maintenance has the update button. The web process never installs
anything itself: it writes a request file, and a root-owned watcher acts on it.
The request carries no branch, remote or revision, so reaching that endpoint
cannot choose what gets deployed — which is the whole reason this is not a
sudoers rule.

**A rollback restores the program only.** It does *not* restore the database.
The schema is deliberately left where it is, because every migration here adds
tables and columns that older code simply ignores, while undoing one would mean
dropping tables and destroying everything written since the update. Data added
after the update therefore stays. If you also need the data as it was, the
rollback point names the dump taken just before the update, and you restore it
by hand.

Two things about the timing are easy to trip over:

- `update` runs `install.sh` **from the current checkout**, and the rollback
  point is written *before* that checkout moves. So a change to how the
  rollback point is recorded takes effect one update after it is deployed.
- Running the update when there is nothing new still records a rollback point —
  of the revision already running. The panel then offers a rollback to the
  version you are on, and taking it does nothing ("already running ...").
  A meaningful rollback needs an update that actually changed the revision.

The rollback point lives at `/etc/jaeronautics/rollback.conf`, owned by
`root:jaeronautics` at mode 0640 so the admin page can read it. At 0600 the page
cannot, and — because a missing file is the normal state before the first
update — the panel silently showed nothing at all. It now says why it is empty,
and an unreadable file is logged.

## Billing Shows Up in Stripe as a Trial

The association bills one shared calendar year, which is implemented by giving
the subscription `trial_end = 1 January` and charging the prorated remainder as
a separate one-off line item. Stripe takes that literally, so its customer
portal tells a member who has just paid that they are in a *free trial* until
31 December:

> Nach dem Ende Ihrer kostenlosen Testphase am 31. Dezember 2026 wird dieser
> Dienst nicht mehr verfügbar sein.

The invoice line directly beneath it does show the payment, so this is
confusing rather than untrue. The tidier construction is `billing_cycle_anchor`
with `proration_behavior`, letting Stripe compute the proration itself and
describe the subscription as a subscription. That is surgery on billing which
currently works and is well covered by tests, so it has not been done; revisit
it if members actually ask.

## Where Secrets Live (a recorded decision)

Four third-party secrets are stored as ordinary rows in the `settings` table:
`stripe_secret_key`, `stripe_webhook_secret`, `discourse_api_key` and
`discourse_connect_secret`. SMTP passwords live in `mail_accounts`. The Flask
`SECRET_KEY` and the database password are in `.env` instead.

**The decision: leave them in the database, unencrypted, and protect the copies
instead.** Application-level encryption would need a key, and on a single-server
deployment that key can only live next to the database — on the same disk, read
by the same process. It would raise the effort of a casual look at a backup file
without changing what an attacker who reaches the server can do, which is the
threat that actually matters here.

What the protection rests on, and what must stay true:

- The database user is scoped to this one database, and MariaDB listens on
  localhost (or a private host, for an external database).
- `/var/backups/jaeronautics` is `chmod 700` and root-owned. The database dump
  is gzipped, **not encrypted**, and contains every secret above, as does the
  copied `.env`. **Anywhere you copy a backup inherits that.** Do not put one in
  cloud storage, a shared drive or an email attachment without encrypting it
  first (`gpg -c` is enough).
- Audit entries and admin notifications redact these keys at the point of
  capture (`services/audit.py`), so they do not leak into the log page.
- The mail-account export requires the administrator's password to be re-entered
  and is audited, but the downloaded file contains **cleartext SMTP passwords**.
  Treat that file like a password list; delete it after use.

Revisit this if the database ever moves to managed or shared hosting, if
backups start leaving the machine automatically, or if more than a couple of
people hold administrator accounts. At that point the answer changes to keeping
the secrets in deployment configuration (`.env`, systemd credentials) rather
than in rows that every backup copies.

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

**Stripe is deliberately left alone (a recorded decision).** Erasure cancels the
subscription and stops there. It does not blank the customer's name or email in
Stripe, and it was briefly written to do so before this decision reversed it.
Three reasons:

- It would not work. Stripe copies `customer_name`, `customer_email` and
  `customer_address` onto an invoice when the invoice is finalised and stops
  updating them afterwards, so every invoice keeps the identity whatever is done
  to the customer object. Scrubbing the customer buys a tidy dashboard and the
  appearance of an erasure that did not happen.
- Stripe is the accounting record, and the retention that § 132 BAO requires of
  the association applies to it as much as to the rows here. The seven-year
  obligation that keeps `membership_periods` is the same one that keeps this.
- The erased member row keeps `stripe_customer_id` on purpose. With Stripe
  intact that id is the one remaining path from an anonymous membership period
  back to who paid it — access-controlled to whoever holds the Stripe dashboard,
  not readable from this application. If a payment is disputed years later, that
  is where the answer is.

So the member-facing promise is that *this* system forgets them, and the
deletion confirmation page says plainly that Stripe does not and why. The export
names the Stripe customer id so a member can ask Stripe directly; if the board
does want a customer removed there, delete it in the Stripe dashboard by hand.

There is no deletion hold and no retained identity record: an erasure is final
here, and a member who misbehaves and then deletes leaves anonymous forum posts
and nothing else. That is a choice, not an oversight. If it ever needs
revisiting, the GDPR-shaped answer is Art. 18 restriction — refusing or
suspending a deletion while a dispute is actually open — not keeping everyone's
identity indefinitely against a hypothetical one.

## Cloudflare Email Address Obfuscation

The site sits behind Cloudflare, and the zone has **Email Address Obfuscation**
switched on. Cloudflare rewrites every email address it finds in an HTML
response into `<a href="/cdn-cgi/l/email-protection" class="__cf_email__"
data-cfemail="...">[email&nbsp;protected]</a>` and injects a same-origin script
that decodes it in the browser. The CSP allows that script (`script-src 'self'`,
and it is served from `/cdn-cgi/`), so in a normal browser the addresses do
appear — but the markup is rewritten either way.

In prose that is only untidy. In the audit log it is not: the before/after
snapshots are rendered as JSON in a `<pre>`, and Cloudflare replaces the address
*inside a JSON string literal* with an anchor element, so an administrator
copying a payload out gets broken JSON instead of the record. Those blocks are
therefore wrapped in Cloudflare's own `<!--email_off-->` opt-out markers, which
keeps them verbatim whether or not the zone setting is on.

Downloads are unaffected: the data export is served as `application/json` and
Cloudflare only rewrites `text/html`.

**To turn it off** for the members site — recommended, since every page here is
behind a login and there are no addresses for a scraper to harvest:

- Zone-wide: Cloudflare dashboard → the zone → **Scrape Shield** (newer
  dashboards: **Security → Settings**) → *Email Address Obfuscation* → off.
- Or, to keep it on for the public site, leave the zone setting alone and add a
  **Configuration Rule** matching the members hostname with *Email Obfuscation*
  set to off.

Afterwards a page's HTML should contain no `__cf_email__` and no
`/cdn-cgi/scripts/.../email-decode.min.js`.

## When Queued Emails Stop Moving

A failed welcome email is retried after 15 minutes and again after 24 hours,
then marked `exhausted` and reported as a problem. The retries are driven by the
`jaeronautics-notifications.timer`, which fires every 15 minutes.

The health panel used to show only how many emails were queued, and to warn
only above twenty. That cannot distinguish three emails backing off normally
from three emails nobody is delivering — which is the failure that actually
happens, because it looks like nothing at all. It now also counts emails whose
`next_attempt_at` passed more than an hour ago (four missed runs) and reports
that as a problem naming the timer:

```bash
systemctl status jaeronautics-notifications.timer
systemctl list-timers | grep jaeronautics
```

To run a delivery pass by hand:

```bash
sudo -u jaeronautics /var/www/jaeronautics/.venv/bin/flask \
  --app aeronautics_members.app:create_app deliver-notifications
```

Emails addressed to domains that do not exist — invented addresses used while
testing — will never send. They exhaust after a day and are then listed as a
problem until the rows are removed; that is working as intended, not a fault to
chase.

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
