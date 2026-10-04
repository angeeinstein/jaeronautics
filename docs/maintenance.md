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

`tests/test_migrations_match_models.py` builds the schema both ways — once by
running every migration from the baseline, once from `db.create_all()` — and
fails if they disagree on a table or a column. Every other test gets its schema
from the models, so without that comparison a model change shipped without its
migration would leave the suite green and the next *fresh* install missing a
column. It compares names rather than types, since SQLite reports types loosely
and a type comparison would fail on dialect differences while catching nothing
real.

`migrations/env.py` passes `disable_existing_loggers=False` to `fileConfig`.
The default is `True`, which switches off every logger that already exists — and
`db-init` runs the migrations in-process on every install and update, so
anything the application logged afterwards in that process would go nowhere.

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
python -m pytest -n auto
```

`-n auto` runs the tests on every core (pytest-xdist): about 3 minutes instead
of 11 on four cores. Every test builds its own app and database, so they do
not get in each other's way. While working on something, run the test files
that cover it (`python -m pytest tests/test_teams_flow.py`); before the last
commit of a piece of work, the whole suite. CI runs the whole suite, in
parallel, on every push.

The tests of the old forum's one-off import (moving the board, the people,
BBCode, the MyBB converter) are skipped by default; the import is done.
`python -m pytest -m forum_import` runs them if it is ever needed again.

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

## Renewing on 1 January

Everybody renews on the same day, and the portal only records a year once its
payment is confirmed. A card is charged within the hour; a SEPA debit is
confirmed days later (usually about five business days). So that nobody paying
by SEPA stops being a member meanwhile, access continues for up to
`RENEWAL_GRACE_DAYS` (21) after the year ends, while:

- the Stripe subscription is still running and not set to cancel,
- Stripe has already renewed it for the new year, and
- the status is `paid` or `processing` -- a failed debit sets `failed`, which
  ends the grace at once, as a declined card always did.

It never follows a revoked year, so a lost chargeback still ends access. The
welcome email is only sent for somebody's first year, not when a renewal clears.

## Changing a Fee

The same for the membership (Settings -> Billing) and for a team (its form,
*Fee*):

1. In Stripe, create a new recurring price **on the same product** as the old
   one, with the **same interval** (yearly for the membership). Leave "Trial
   period days" on the price empty.
2. Enter the new price ID and save. The portal checks it with Stripe and
   refuses one that does not fit.
3. New members pay the new fee at once. Every running subscription is moved
   to the new price in the background -- through the external-work worker,
   one item per subscription, within minutes -- with no proration: nothing is
   charged or refunded now, the next charge is the new amount. Subscriptions
   that are ending are left alone.
4. Each person moved is emailed the new fee, the old one, from when, and how
   to cancel or leave before then -- **14 days before their renewal**
   (`FEE_NOTICE_DAYS` in `services/payments.py`), or at once when the renewal
   is closer than that. The email waits in the same queue; whoever has
   cancelled or left by then is not sent it, and a fee changed again before
   it went out replaces it, so nobody is told twice.

Stripe itself never announces a price change. For SEPA debits it does email
the amount before every collection (two days ahead, under the mandate members
accept at checkout), which covers the banking rule; the portal's own email is
the real notice. Change a fee at least two weeks before the next renewal, so
the email still goes out on time -- and update the fee rules in the legal
texts (`legal/`, see "Legal Texts") for the membership. A move that keeps failing shows under the health check's failed
external work. Archive the old price in Stripe once nobody is on it.

## Legal Texts

The statutes, rules of procedure, membership terms, privacy statement and the
rest are Markdown files in the repository, one folder per text and one file
per version, named after the day it takes effect:

```
legal/statutes/2019-03-17.md
legal/privacy/2026-10-04.md
```

The portal shows `/legal` (the list), `/legal/<text>` (the version in force)
and `/legal/<text>/<day>` (an older one, marked as no longer in force). The
version in force is the newest file whose day has come; a file dated in the
future stays hidden, also by its address, until that day and then switches
over by itself.

**Changing a text:** add a new file next to the old one -- never edit a
version people have already accepted -- commit it, and update the server.
Write it in German. The first line is the title (`# Statuten`); each `## `
heading becomes an entry in the contents list, and `## §5. …` gets the
address `#paragraph-5`. Raw HTML is shown as text, not run; lists, bold,
links and tables work. The tests in `tests/test_legal_texts.py` check every
file in `legal/` (folder known, name a real day, title present), so a
mistake fails CI rather than the page.

**Which texts there are** is the list `LEGAL_TEXTS` in
`services/legal_texts.py`: folder, German title, English name for links,
and whether it is accepted at signup. A registered text without a file is
not shown. Adding a text is a line there and a folder here. Currently
accepted at signup: statutes, rules of procedure, membership terms, privacy
statement. The withdrawal notice and impressum are registered but have no
file yet. Team rules are not here: each team keeps its own on its page.

**At signup** the one checkbox names every text accepted then, each a link.
A click opens the text in a window over the form (`static/legal-dialog.js`
fetches `/legal/<text>?part=body`); without JavaScript it opens in a new tab.
Either way nothing typed into the form is lost. The member keeps which
version of each text was accepted, and when (`legal_versions_accepted`,
`legal_accepted_at`; in the member's data export). Members who signed up
before October 2026 have neither; for them the signup date stands for the
version.

**The membership terms and privacy statement are placeholders** -- obvious
nonsense, marked as such -- until the real texts are written. Replace them
before this reaches the live portal.

**Footer:** Impressum, Privacy and Statutes link to the addresses set by
`IMPRESSUM_URL`, `PRIVACY_URL` and `STATUTES_URL`. Impressum and Privacy
default to the main website; Statutes, unset, links to `/legal/statutes`.

## Legal Notes

`docs/legal-notes.md` lists what the portal does with personal data and money,
for the privacy statement, terms and statutes. Update it in the same commit as
any change that collects, sends or keeps data differently, or changes how fees
are taken.

## Stripe Webhook Events

The endpoint (`https://<portal>/stripe-webhook`, in Stripe under Developers →
Webhooks) needs exactly these 13 events, on the test and the live account:

| Event | What the portal does with it |
|---|---|
| `checkout.session.completed` | A Checkout finished: membership, team subscription or team payment |
| `checkout.session.async_payment_succeeded` | A SEPA debit for a one-time payment cleared |
| `checkout.session.async_payment_failed` | A SEPA debit for a one-time payment failed |
| `customer.subscription.updated` | Cancellation set or taken back, status changes |
| `customer.subscription.deleted` | A subscription ended |
| `invoice.paid` | A subscription payment, first or renewal |
| `invoice.payment_succeeded` | The same, as Stripe also reports it; a payment is recorded once |
| `invoice.payment_failed` | A renewal failed |
| `payment_intent.processing` | A SEPA debit is on its way |
| `payment_intent.succeeded` | A membership payment confirmed |
| `payment_intent.payment_failed` | A membership payment failed |
| `charge.refunded` | A refund, taken off what a team is owed |
| `charge.dispute.closed` | A chargeback decided |

Any other event is accepted and ignored; leaving them out only spares the log
and Stripe's retries.

## Billing Shows Up in Stripe as a Trial

The association bills one shared calendar year, which is implemented by giving
the subscription `trial_end = 1 January` and charging the prorated remainder as
a separate one-off line item. Stripe takes that literally, so its customer
portal tells a member who has just paid that they are in a *free trial* until
31 December:

> Nach dem Ende Ihrer kostenlosen Testphase am 31. Dezember 2026 wird dieser
> Dienst nicht mehr verfügbar sein.

The invoice line directly beneath it does show the payment, so this is
confusing rather than untrue.

**The portal text itself cannot be changed.** Stripe's Customer Portal exposes
branding, the business name, links and which features are enabled — not the
copy. There is no setting that rewrites that sentence.

**Only half the cases are wrong.** `build_membership_cycle` splits on
`FREE_PERIOD_START_MONTH = 10`:

- Joining January–September charges the prorated remainder now, so "free trial"
  is simply wrong — the member has just paid.
- Joining October–December charges nothing until 1 January, so "free trial" is
  exactly right. Any fix must leave this case alone rather than replacing
  accurate wording with a construction that then has to be told not to charge.

**The tidier construction is now available.** When this was built, Checkout could
not anchor a billing cycle, which is why `trial_end` was used. It can now — in
`stripe==13.0.1` (API `2025-09-30.clover`), `subscription_data` accepts
`billing_cycle_anchor` and `proration_behavior`, alongside `trial_end`. Anchoring
to 1 January with `proration_behavior="create_prorations"` would leave the
subscription `active` rather than `trialing`, let Stripe compute the proration,
and make `build_prorated_line_item` unnecessary on the Checkout path.

Two things would have to be established in the sandbox first, and neither can be
settled by reading the parameter docs:

- **When the proration is collected.** The docs say what the anchor does, not
  whether `mode=subscription` charges the first invoice at the session or defers
  it to the anchor. If it defers, members would join without paying, which is far
  worse than confusing wording.
- **Whether the amounts agree.** Ours is day-based — `€10 × remaining/365`,
  half-up, verified against Stripe at €2,85. Stripe prorates by seconds. The
  checkout page prints the amount before the member pays, so a one-cent
  divergence would have the page and the charge disagree.

This is surgery on billing that currently works, is covered by tests, and has
been verified end to end against real Stripe. **Decision: left as it is.**
Revisit only if members actually ask, and test the two points above in the
sandbox before changing anything.

## Selling Anything Else Through the Same Stripe Account

The portal receives every event of the association's Stripe account, and it
used to take every paid invoice for a membership payment -- matched to a member
by customer or email. The teams' fees, or a payment link for an event, would
then have given the payer a year of association membership, and a team
subscription ending would have cancelled theirs.

`services/stripe_scope.py` sorts each event before anything acts on it:

| Verdict | When | What happens |
| --- | --- | --- |
| the membership | `purpose: membership` in the metadata (every checkout since this change), the old checkout metadata (`activation_mode`, `membership_starts_on`), a subscription the portal already knows, or the membership's price or **product** | handled as always |
| something else | `purpose` set to anything else, everything on it for another product, or a payment that paid no invoice | ignored, logged, answered 200 |
| Stripe unreachable | telling needed Stripe, and it could not be asked (no connection, outage, rate limit, key problem, anything unforeseen) | answered 503, so Stripe delivers the event again later -- for up to three days |
| nothing to go on | no metadata, no known subscription, no price or product -- or Stripe says what was asked about does not exist | ignored, and an administrator is told (`stripe_event_scope_unclear`) |

Only the first row is acted on. That costs the membership nothing: its
invoices and subscriptions always carry the portal's metadata in the event
itself, so they are recognised without asking Stripe and never come with
nothing to go on. What does need asking -- a payment, to find its invoice --
is reported a second time by the invoice event, which does the work. A
membership invoice made by hand in the Stripe dashboard is the one exception:
it arrives with nothing to go on, and the administrator notice is the prompt
to grant that member's period by hand.

The product counts rather than the price, so subscriptions from before a fee
change -- still on last year's price -- are still recognised. The same sorting
keeps a team subscription from being taken for the membership when the portal
looks one up (rejoining, the nightly reconcile, account deletion).

**Anything sold later must be recognisable as not the membership:** set
`purpose` (e.g. `team:drones`) in the metadata of its Checkout session,
subscription and payment intent, and give it a product of its own in Stripe --
not a second price under the membership's product. A payment link made in the
dashboard carries no metadata, so for those the separate product is what tells
them apart.

## Renaming Somebody on the Forum

Approving a name change with *also change the forum username* ticked queues a
`forum_rename` job, run straight away and retried if the forum is down. It uses
Discourse's own rename, because the sync that carries every other change leaves
the username alone on purpose: `auth overrides username` is off so that
returning students keep the name their old posts are under. Until 2026-09-29
the approval relied on that sync, and the portal then followed the forum's
answer -- the old name -- straight back, so no rename ever happened.

- **Posts stay theirs.** A post belongs to the account, not to the name, so
  every post shows the new name at once. Discourse rewrites @mentions and
  quotes of the old name in the background.
- **A name the forum refuses** (taken there by an account the portal does not
  know, such as an archived one) is not retried: the portal goes back to the
  name the forum has and sends an administrator notice, `forum_rename_refused`.
- **Somebody not on the forum yet** simply arrives there under the new name.

## Keeping the Forum's Idea of a Membership Current

The forum holds its own copy of who may read what, as group memberships. Every
way a membership *changes* is an event somebody causes, and each of those syncs
the forum where it happens: a payment lands, an administrator switches an
account off, a photograph is approved, an email address changes. Those are
immediate and need nothing.

One thing is not an event at all. A membership ends because its last day has
passed — nobody clicks anything, nothing is written, no code runs. The portal
is never wrong about it, because access is decided by date on every request;
it is the copy on the forum that goes stale, and the person goes on reading the
archive until somebody happens to open their record.

`reconcile-billing` catches most of it, but only for members with a Stripe
reference: its query begins `WHERE stripe_customer_id IS NOT NULL OR
stripe_subscription_id IS NOT NULL`. A membership paid by transfer, an invoiced
company, an honorary member appointed by the association — none of them are in
it.

So a second timer asks the question directly, at 03:45, half an hour after the
billing reconciliation so that anything Stripe has just been found to have
lapsed is already recorded here:

```
jaeronautics-forum-drift.timer  ->  flask sync-forum-members --only-changed
```

**What it costs is the point.** For each linked member it compares the state the
portal says they should be in against the state stored on their forum account.
That comparison is local — no call leaves the machine for the 749 people whose
answer has not changed — and only the ones that differ are synced. On an
ordinary night it makes no API calls at all and prints:

    Nothing has drifted: the forum agrees with this portal.

Two things it deliberately leaves alone: somebody who has never had a forum
account at all (there is nothing there to be out of date), and an account that
was erased, whose state is `anonymised` and terminal — putting them back into
groups would undo the erasure.

Run it by hand whenever you want to know, and it will tell you what is wrong
before it fixes it:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     sync-forum-members --only-changed
```

### When the forum was down for hours

A forum update that fails is retried after 1 minute, 15 minutes, 1 hour and
6 hours, and then given up — Maintenance shows "Forum tasks failed". Nothing is
lost: the next change for that member puts the task back in the queue, and
**Retry** next to the count sends all of them again at once. Use it once the
forum is reachable again.

## Rate Limits

Every form that takes a password or an email address has two limits:

| Setting | Default | Counts |
| --- | --- | --- |
| `RATELIMIT_LOGIN` | 10 per 15 minutes | Login attempts for one email address from one network |
| `RATELIMIT_LOGIN_PER_IP` | 1000 per 15 minutes | All login attempts from one network |
| `RATELIMIT_REGISTER` | 5 per hour | "Forgot password" for one email address from one network |
| `RATELIMIT_REGISTER_PER_IP` | 1000 per hour | All "forgot password" requests from one network |
| `RATELIMIT_MEMBERSHIP` | 10 per 15 minutes | Signups for one email address from one network |
| `RATELIMIT_MEMBERSHIP_PER_IP` | 1000 per hour | All signups from one network |

The tight one stops somebody guessing one account's password; the loose one
only stops a flood. A lecture hall on the campus network shares one address,
so the loose limits are set for an intake evening -- 250 students on the
university Wi-Fi, several tries each. Signed-in actions (resending
a confirmation, downloading your data) are counted per account, not per network.

The counts live in Redis. If Redis is unreachable, the limits fall back to each
web worker's memory rather than failing the request, so logging in keeps
working; they go back to Redis once it is reachable again.

Change them in `config.py`, not in `.env`: the installer rewrites `.env` on
every update and keeps only the keys it knows, so a value set there is gone
after the next update.

### Checking them from outside

`scripts/edge-check.ps1` (PowerShell 7, from any PC) goes the whole way --
Cloudflare, the tunnel, nginx, the application -- and checks that many
readers at once are answered quickly, that the eleventh login or signup for
one address is refused while forty different addresses from one network are
not, and that a flood of anonymous forum page loads is cut off. It creates
nothing: the addresses are at example.com and the signup forms are incomplete.

    pwsh ./scripts/edge-check.ps1

It spends the running network's allowance for a quarter of an hour. The forum
side should be cut off after about fifty loads in ten seconds -- Discourse's
own per-address limit for visitors who are not signed in; signed-in people
are counted per person, and the portal's calls come in signed in as `system`.

Its first run on Azure found a real fault: after half a minute of thirty
readers, every request hung for thirty seconds, the CPU sat idle and no log
said why -- except `dmesg`: `nf_conntrack: table full, dropping packet`. nginx
opened a new connection to gunicorn for every request, the kernel tracks each
closed one for a while, and on a 1 GiB VM its table holds 7,168. nginx now
keeps a few connections to gunicorn open and reuses them (the `upstream`
block, `--keep-alive 75` on gunicorn, longer than nginx's 60 s so nginx always
closes first), and the installer sizes the table to 65,536 wherever the
kernel tracks connections at all. If a load test ever hangs with an idle CPU
again, `sudo dmesg -T | grep -i conntrack` is the first thing to look at.

## Slow Stripe or Forum

The web server runs 3 workers with 4 threads each, so up to twelve requests are
served at once and a page waiting on Stripe or the forum holds one thread, not
the whole site. Calls out give up in time to show an error rather than hang: a
forum call made while somebody waits on a page after 8 seconds (20 from the
background worker), a Stripe call after 5 seconds to connect and 15 to answer,
retried once.

## Backup and Restore

One file holds everything that makes this installation different from a fresh
one. A fresh install plus that file is the old portal again — the way to move
to a new server (Azure), and the way back after losing one.

### What is in a backup

| In it | Not in it |
| --- | --- |
| The whole database: every account and password hash, membership, role, setting and credential entered on the settings pages (Stripe, Discourse, SMTP), mail accounts, audit log, old forum archive, queued work | The database password, host and port, the domain, paths, nginx and systemd files — they belong to the machine and the installer writes them |
| `storage/` — pictures waiting for review, imported forum avatars | `storage/backups` itself |
| From `.env`: `SECRET_KEY` (so logins and emailed links keep working), the Stripe keys and `MAIL_ACCOUNTS_JSON` | The rate-limit counters in Redis — temporary by nature |

Two things a portal backup cannot cover: **the forum** keeps its own data (back
it up under Discourse → Admin → Backups), and **Stripe** keeps payments and
subscriptions itself — the portal only holds the IDs that point there. A
complete recovery is a portal backup plus a forum backup.

### It is encrypted, and the passphrase is not stored

A backup is every member's address and every credential in one file, so it is
only ever written encrypted: AES-256-GCM, with the key derived (scrypt) from a
passphrase chosen when the backup is made. The passphrase is not kept anywhere.
**Without it the backup cannot be restored, by anyone.** Keep it somewhere safe
and apart from the file.

A wrong passphrase, a file cut short or a file altered in any way is refused
before anything is changed.

### Making one

**Settings → Maintenance → Backup & Restore** (super admins only). Enter the
passphrase twice and press **Back up**; the card shows each step as it runs,
then checks the finished file by reading it back completely. The newest five
are kept in `storage/backups` for download; older ones are removed when a new
one is made. Where backups should live for good — Azure storage, a schedule —
is decided once this has proven itself.

From a shell, the same thing:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app create-backup
```

### Restoring

Copy the file to the server and run the installer with it:

```bash
sudo ./install.sh --restore /path/to/portal-backup-20261001-101500.jabackup
```

- **On a new server** it installs first and then restores. It does not ask
  for a first admin account: the backup brings its own, and one made now would
  be deleted by the restore anyway.
- **On an existing installation** it replaces everything there — every
  account, including any made while installing.

It asks for the passphrase (`--passphrase-file FILE` for a run without a
terminal), stops the portal, restores the database, files and `.env` values,
and starts it again.

**Versions.** A backup from an older portal version is rebuilt at its own
database version and then upgraded, exactly as an update would. A backup from
a *newer* version than the installation is refused — update the installation
first.

### After a restore: background jobs are paused

A restored copy holds the real Stripe keys, the real mail accounts and the real
forum's API key. On a test machine it would otherwise start emailing members
and pushing accounts to the live forum as soon as its timers fired. So after a
restore every background job — email delivery, the forum queue, the nightly
billing and forum checks, log cleanup — stands down, and every admin page says
so.

Sign in with an account from the backup and open **Backup & Restore**. The
card lists the background timers (a timer that fires while paused still checks
in, so this shows they are installed). Tick *This is the server members use*
and press **Resume background jobs**. The card then checks, line by line:

- the database is at the current version;
- Stripe accepts the key (and knows the membership price);
- the forum accepts its API key;
- every mail account can sign in;
- each frequent timer has run since resuming, the daily ones are scheduled;
- the queue that built up while paused drains.

Each line turns from a spinner to a tick — or a red cross with the reason.
**Check again** repeats the checks after fixing something.

Things a person does on a paused copy still happen (a password reset email, a
manual forum resync); the pause covers what the machine does on its own.

### Data protection

A backup keeps people as they were when it was made. Someone who deletes their
account afterwards is still in older backups, and restoring one brings them
back. So keep backups only as long as they are useful, and after restoring an
older one, check the audit log for accounts erased since then.

### Try it before you need it

A backup that has never been restored is a hope, not a backup. Restore one onto
a spare machine once — the Azure move is the natural occasion.

## Rebuilding the Portal Somewhere Else

A full **backup** (above) carries everything, settings included. The settings
export described here is the lighter tool for carrying only the settings.

A fresh install starts with a blank settings page, and filling it in is forty
minutes of copying values that all have to be exactly right and none of which
announce themselves when they are wrong. A missing lecture group is an import
that refuses; a missing Connect secret is a login that silently does not work.

So take them with you:

```bash
# On the machine being replaced.
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     dump-portal-settings --out /var/tmp/portal-settings.json --with-secrets

# On the new one, after install.sh has finished.
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     restore-portal-settings /var/tmp/portal-settings.json --dry-run
```

Somewhere the **application user** can write. These commands run as
`jaeronautics`, which owns very little of the machine and `/root` least of all;
put it in `/var/tmp` and move it afterwards as yourself.

It carries the general, notification, forum and Stripe settings, the
institutional email domains, and the mail accounts. It never removes anything:
a setting the file does not mention is left alone, because the file is a record
of one machine rather than a description of every machine.

**`--with-secrets` makes the file a password list.** The Stripe secret key, the
Stripe webhook secret, the Discourse API key, the Connect secret and every SMTP
password, in the clear. It is written `0600` and it is not encrypted, and
anywhere you copy it inherits that: `gpg -c` it before it leaves the machine,
and delete it once the new install has taken it.

Without the flag those values are exported as `<not exported>`, which the
restore skips rather than writing — an empty box says what it is, and
`<not exported>` reads like a key somebody configured.

### What it deliberately does not carry

**`.env`.** `SECRET_KEY` and the database password belong to the machine rather
than to the configuration, and putting an old database password onto a new
install would break the thing it was restoring. Copy it separately if you want
the old values:

```bash
cp /var/www/jaeronautics/.env /root/portal-env-$(date +%F)
```

That file is the same kind of password list, for the same reasons.

**Stripe secrets typed at the installer's prompts.** The installer writes the
Stripe secret key and webhook secret into `.env`, and the portal reads the
settings page first and `.env` only when the page is empty. A portal whose
Stripe keys were only ever given to the installer therefore exports **no**
Stripe secrets, even with `--with-secrets`, and a fresh install with those
prompts left blank cannot take payments. Enter them on the Billing settings
page instead; from then on they travel with the file. Found on the first
rebuild, 2026-09-25.

**The Discourse API key should not travel either, in the end.** The portal's
own key is bound to `system` and permanent — see §2 of
[forum-content-import.md](forum-content-import.md) — and a new machine is a
good moment to make a new one rather than carry the old one across.

**Members, payments and the audit log.** Those are the database, and the
database dump is how they travel.

### The one that fails silently

`discourse_connect_secret` has to be **identical** on both sides. A fresh
install generates its own, so unless it is restored from the file, it must also
be set in Discourse's site settings. Nothing reports this: logins simply do not
work.

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

## Teams

Groups inside the association with their own members and leads -- off until
switched on. What was decided and why is in [teams-plan.md](teams-plan.md);
this is how to use them.

**Setting up** (site admins, Admin → Teams):

1. Switch teams on. While off, members see nothing of them; site admins can
   already open each team's pages to set them up.
2. **New Team**: a name, how people join (by approval of the leads, or open to
   every member), optionally a maximum size and a forum group, whether it
   **has rooms that need an access list**, and its fee. Everything the team
   says about itself -- descriptions, picture, logo, question for applicants,
   rules -- is on its management page (*Team page and settings*), kept by its
   leads; admins can open it too.
3. **Give the lead role** by email address. A role counts only while its
   holder is a member of the association *and* of the team, so the lead joins
   the team like anybody else and an admin approves them.

**A team's About page** (`/teams/<short name>/about`): every signed-in visitor
sees the logo, name, an "About the team" text, one optional picture, the fee,
the team's rules and the form to apply or join. The overview shows each team's
short description and leads there. **The team's own page**
(`/teams/<short name>`) is for its members: their membership and who is in the
team, without the texts; anybody else is sent to the About page. The texts,
picture and rules are edited by the team's leads (Manage → Settings) and by
site admins (Admin → Teams). Rules are optional; a team with rules needs them
ticked to apply or join, and each membership keeps when they were accepted and
which version -- the day they last changed. Changing the rules makes a new
version for whoever applies next (members already in are not asked again) and
is logged with the old and new text.

**Running a team** (its leads, Teams → Manage), in sections down the side:
*Applications* (invite with the meeting details, approve, not accept),
*Members*, *Former members*, *Team page* (descriptions, picture, logo),
*Applying* (open or closed, the question, the rules), *Access list* (only for
teams that have one) and *Roles*. Each settings section is saved on its own.
Also: notes about a person (not shown to them, but in their data export,
without the author), removing somebody (immediately, with a reason that stays
in the record), and an export of the current members. Under *Roles* the leads
appoint the team's **treasurer** from its members, and take the role back;
leads themselves are appointed by site admins.

**Access list.** Only for teams with rooms: site admins switch it on in the
team's admin form; off, the leads do not see it and nothing is sent. In the
leads' *Access list* section: who receives it, on which days of the
year (`15.10, 15.03`), and whether it goes out by itself on those days. The
leads' page has *Preview and send* for sending it by hand. It marks who is new
since the last list sent and lists who has left since.

**Forum group.** Create the group in Discourse first, then name it in the
team's settings; members are added and removed with each forum sync. Renaming
it later leaves people in the old group -- delete that one in Discourse.

**A fee** (site admins, in the team's form under *Fee*):

1. In Stripe, create one product for the team (e.g. "Rocket Team fee"), not the
   membership's. Give it the price(s) you may use: a **recurring** one (every 6
   months for two periods a year, yearly for one) and/or a **one-time** one.
2. In the team's form: *Payment* → Subscription or Once per period, the
   matching price ID (`price_...`), and the days the periods start
   (`01.10, 01.03`). Saving checks the price with Stripe; the fee members see
   ("€10.00 every 6 months", "€25.00 per period") is taken from it. Switching
   later is the other mode with the other price -- the product stays.
3. Stripe's webhook endpoint needs the events listed under *Stripe Webhook
   Events*. Everything for teams is marked `purpose: team` and never touches
   the membership.

How it runs: approved (or joining an open team), a person sees *Pay and join*.
They pay the period under way in full, whenever they join. The first payment
makes them a member. *Leave* runs to the end of what is paid (no refund), *Stay
after all* takes it back. Removal by a lead, the association membership ending
and erasure cancel a subscription at once, without refund. For six months
after a membership ended unpaid the person may come back by paying, without
applying. Payments are listed in the `payments` table and on the team's Money
page.

- **Subscription**: Stripe charges at each period start; joining in the last 3
  days before one pays for the coming period instead. Receipts and renewal
  emails come from Stripe. A renewal Stripe finally gives up on ends the team
  membership.
- **Once per period**: nothing renews by itself. 14 days before the period ends
  the member is emailed and sees *Pay for next period* on the Teams page;
  paying then continues without a gap. Whoever has not paid leaves on the last
  day, and the leads get one summary. Stripe sends a receipt.

Somebody who cancels their association membership -- on Stripe's billing page,
say -- stays a member to the end of what they paid for. As soon as the portal
hears of it, each of their team memberships is set to end on the same day:
the team subscription stops then without renewing (no refund for time paid
beyond it), and they and the leads are emailed. Taking the cancellation back
lifts it again. Leaving a team on one's own is not touched. The nightly job
does the same for anything a webhook missed.

A new price applies to people joining at once, and running subscriptions
move to it from their next renewal, their holders emailed two weeks before;
see *Changing a Fee*. A different interval is refused while subscriptions run.

Changing how a team charges while it has members -- something set up once
and seldom touched -- is handled rather than refused:

- **Free from now on**: every running subscription stops at the end of what
  is paid, and those members stay in the team, for free; they are emailed.
  Approved people not yet paying are members at once.
- **Charging from now on**: members already in stay free until the next period
  starts, are emailed, and see *Pay to stay* (nothing is charged before the
  period starts). Whoever has not paid by then leaves, and may come back by
  paying within six months.
- **The period dates** cannot change while subscriptions run on them: switch
  to free, let them run out, then set the new dates.
- **Archiving a team, or switching teams off**, is refused while anybody still
  pays by subscription: switch the team to free first. Archiving also asks for
  the team's name to be typed.

**New members** are pointed to the Teams page, not asked at signup: a team can
only be joined once the association membership is active. The welcome email,
the page after paying and the account page (for members in no team yet) carry
the link while teams are switched on.

**Leaving** is a page of its own: what it means (until when, no refund, the
forum group and access list, applying again to come back), an optional message
to the leads, and a box to tick.

**Every night** (with `reconcile-billing`): team memberships of people no
longer in the association end, approvals for a team that charges lapse when
unpaid after 14 days (not while a SEPA debit is on its way), memberships whose
leaving day has passed, or that are unpaid for more than 35 days, end in case
Stripe's word never arrived, and access lists due that day go out.

**Unfinished signups** (also every night): a signup never paid for is removed
90 days after the signup or the last attempt to pay, completely -- account,
profile and its log and email records -- since nothing has to be kept for it.
The person is emailed 7 days before, and removal waits until that email has
been out 7 days. Only bare signups: if any row in any table points at the
account other than its own log, email and background-task rows -- a role, a
team, a forum account, a picture, a change request, a membership period, a
payment -- or it has a Stripe subscription, it is left alone. That is read
from the schema, so a table added later keeps such signups rather than
breaking the delete. One log entry records how many went. Run by hand with
`flask cleanup-pending-signups`.

**Money** (Teams → Money, or the button on the management page). What the
team's members paid -- exactly that: Stripe's fees are the association's --
less refunds and lost chargebacks, what was transferred to the team, and what
is still open, also by the period it paid for, with a CSV export. Seen by the
team's leads and its **treasurer** (a team role, given like the lead role, that
sees the money but not the people), and by the association's **Treasurer**
and site admins for every team (Admin → Money), also archived ones.

- **Bank details**: the team's leads and treasurer, the association's
  Treasurer and site admins can set them. The IBAN is checked by its check
  digits. Every change is in the log with before and after, and the
  association's Treasurer is emailed when somebody else made it.
- **Transferring**: the association's Treasurer (or an admin) opens the team's
  Money page, scans the GiroCode with the banking app -- it fills in account,
  open amount and reference -- sends it, then *Mark as transferred*. A
  transfer is recorded with the account it went to and cannot exceed what is
  open.

**How payments are built.** `services/payments.py` is the one place that talks
to Stripe for anything sold: the connection, the person's Stripe customer (one
per person, shared by the membership and every team), opening a Checkout
without opening two, cancelling, and handing each webhook event to the handler
registered for its `purpose`. The membership keeps its own rules in
`services/billing.py`; teams have theirs in `services/team_payments.py`.
Something new -- a balance for the coffee machine -- gets a purpose and a
handler of its own.

## Roles and Permissions

**Access is decided by capability, never by role name.** A route says what it
does — `@requires(Permission.SYSTEM_UPDATE)` — and `permissions.py` alone says
which roles carry that. Templates ask the same question:
`current_user.can('logs.view')`.

That indirection is the point. Checking roles at each call site works until a
second kind of privileged user appears: adding a forum moderator would mean
finding every route a moderator should reach and editing its check, which is
exactly the kind of sweep that gets one route wrong and nobody notices for a
year.

`ROLE_PERMISSIONS` is the whole access model:

| Capability | Treasurer | Admin | Super Admin |
|---|---|---|---|
| `admin.access` — open the admin workspace | yes | yes | yes |
| `teams.money` — every team's money, transfers, bank details | yes | yes | yes |
| `accounts.view`, `accounts.billing`, `accounts.privacy` | no | yes | yes |
| `approvals.review`, `forum.moderate`, `logs.view` | no | yes | yes |
| `notifications.manage` — test email, undelivered queue | no | yes | yes |
| `settings.general`, `teams.manage` | no | yes | yes |
| `settings.credentials` — Stripe/Discourse keys, mail accounts | no | no | yes |
| `system.update` — install a version, roll one back | no | no | yes |
| `roles.manage` — grant or revoke access | no | no | yes |

There is **no role implication**: `superadmin` is not "admin plus extra" by
inheritance, its bundle simply contains the admin bundle. One mechanism rather
than two, and the table shows the whole truth.

### Changing an account's roles

One form on the account page, one endpoint: **the whole set is posted**, and
`services/access.py` works out the difference. There is no grant-this and
revoke-that per role, because the guards that matter are about the *resulting*
state — is anybody left who can install an update — and a per-role endpoint has
to re-derive that each time, in each of its copies. It also means a role added
to the table is assignable with no new route and no new button.

The account list is read-only for roles, deliberately: deciding from a list
means deciding without seeing what else the account holds or who else could do
the job.

**A role another ticked role already covers is not stored separately.** Ticking
Admin as well as Super Admin describes the same account as Super Admin alone,
because the second bundle contains the first — so offering both as distinct
states asks a question with no answer. `minimal_roles()` drops the covered one,
the form marks it *included in Super Admin*, and saving says so rather than
dropping it silently. Roles that merely overlap, such as a moderator and a
treasurer, both survive; only genuine containment is collapsed.

The page also lists what the account **can currently do**, because that is the
question a role list is really being asked, and it is what makes an unticked
Admin box on a Super Admin unalarming. Each role additionally discloses its own
capabilities, so deciding what to hand somebody does not mean reading this file.

**Compared with Stripe's team-role dialog**, which is the closest widely used
model: checkboxes, plain-language descriptions, per-role disclosure, "only a
super administrator can grant this role" and "the account creator gets it
automatically" all match. Two things there are deliberately not copied —
grouped, collapsed role categories with a search box, which earn their keep at
Stripe's dozen-plus groups and are chrome around one decision at two; and the
two-day cooling-off on sensitive actions after a role change, an
account-takeover brake that at this scale would mostly lock the one
administrator out of the update button.

One thing differs on purpose. Stripe lets Super Administrator and Administrator
both be ticked and explains the union with a banner; this collapses the
redundant one instead. Stripe's roles are largely orthogonal, so containment is
the exception there and collapsing would look like a bug; here containment is
currently the only relationship between the two roles, which is precisely why
the redundant state was confusing. The union banner is borrowed regardless,
since a list of checkboxes otherwise leaves that rule to be inferred.

Three refusals, all enforced in the service so they hold however the change
arrives:

- **Not your own account.** Removing your own last privileged role locks you
  out, and a confirmation dialog is not where that should be discovered.
- **Not the last holder of a protected capability.** `PROTECTED_PERMISSIONS`
  names what must never reach zero holders — administering the site, installing
  an update, granting access — and the check counts what would remain *after*
  the change, ignoring erased accounts, which cannot sign in to do anything.
- **Not an erased account.**

### Two surfaces that are not routes

Most access is decided by `@requires(...)`, but two places choose by capability
without being a route, and both were missed in the first pass:

- **Who receives the admin digests.** `get_admin_recipient_emails()` selected
  `Role.slug == "admin"`, so an account holding super admin without the admin
  row beside it silently stopped being told about errors and review tasks. It
  now asks for `NOTIFICATIONS_RECEIVE`, which is deliberately separate from
  `NOTIFICATIONS_MANAGE`: a future role may need to act on review tasks without
  every error report reaching its holders' inboxes, or the reverse.
- **Where signing in lands you.** `get_member_portal_target()` asked the same
  literal, sending a super-admin-only account to the member page.

Both are covered by tests that use a role which is not called "admin", since a
test with an admin in it would have passed against the old code.

### Adding a role

Add an entry to `ROLE_PERMISSIONS`, grant it, done — no route, template or
decorator changes. `seed_default_roles()` creates the row from that table on the
next start. A moderator would be:

```python
"moderator": frozenset({Permission.ADMIN_ACCESS, Permission.FORUM_MODERATE}),
```

`tests/test_roles.py::TestAddingARoleNeedsNoOtherChange` does exactly this and
then checks the claim rather than asserting it: the row appears, the holder
reaches `/admin/reviews`, is bounced from settings, logs and updates, counts
towards the capabilities it carries, is offered only the navigation it can use —
and no file outside `permissions.py` mentions the role at all.

Navigation, dashboard cards, settings tabs and the account-list buttons are
keyed on the same capability the page behind them requires, so a partial role
sees a coherent interface instead of links that bounce it. Hiding is a
convenience; `requires(...)` is what decides.

The account directory's role filter asks the same question. Filtering on
`Role.slug == "admin"` would file an account holding only a newer role under
"Member Only" and would need editing for every role added, so it filters on
`roles_with(Permission.ADMIN_ACCESS)` and builds its dropdown from the table.

### Where the first super admin comes from

The awkward question when splitting a privilege out of an existing role, with
three answers:

- **An installation that already exists.** Migration `f2b6a90c1d73` grants the
  role to every current administrator. That is not an escalation — those
  accounts could already press the update button — and skipping it would be a
  silent *demotion* locking the site out of its own update mechanism, which is
  how the fix would have had to arrive. Erased accounts are skipped.
- **A fresh installation.** No administrators exist when the migration runs, so
  nothing is granted. `create-admin`, which `install.sh` calls, takes the role
  when nobody can yet install an update; the first account created is one.
  `--superadmin` / `--no-superadmin` force it either way.
- **Recovery**, when the last one leaves or erases their account:

  ```bash
  sudo -u jaeronautics /var/www/jaeronautics/.venv/bin/flask \
    --app aeronautics_members.app:create_app grant-superadmin someone@example.com
  ```

  It needs a shell on the server, which is the right bar — anyone with one can
  already read this database and change this code.

### Not locking everybody out

The guards count **capability holders, not superadmins**, so a future role
carrying `system.update` starts counting towards "somebody can still install an
update" without the guards being touched. `PROTECTED_PERMISSIONS` names the
capabilities that must never reach zero holders.

Concretely: an account cannot strip its own role from the interface, the last
account that can install an update cannot be erased (`last_superadmin` blocker),
and revoking *admin* from a super admin removes both roles, since removing one
row would otherwise leave the access untouched.

## What a `member` row means

**A membership this system administers.** Somebody who pays, can be reached,
and renews. That is the definition the whole schema hangs off, and it is
narrower than "member of the association".

The people carried over from the old forum *are* association members. They
joined years ago, nobody formally ended it, and technically they still are —
which is why the website says roughly seven hundred members and is not lying.
They have no `member` row all the same, and not because they are not members:

* There is no live relationship to administer. Nobody collects from them,
  nothing renews, and **they cannot be contacted at all** — no private address
  was ever kept, so a general assembly invitation cannot reach them. Their
  university addresses are dead except for the ones still studying, which is
  exactly the group the forum claim reconnects.
* The record does not exist to write down. The old forum kept a username, a
  university address and a year group; never a name, a postal address or a
  phone number. Of the ten fields `member` requires, the import supplies none.

So the counts on the dashboard are deliberately **software counts, not
association counts**, and the archive is shown beside them rather than added
to them. Every number an admin acts on — how many are active, how many are
overdue, who to email — should mean "relationships this system manages", and a
combined figure would be quotable in public and useless for every decision
made from that page.

Screens avoid claiming either way about the rest. An imported row reads *Old
forum*, and the account page says the membership is **not recorded** rather
than absent, because "no membership" would be false about people the
association still counts.

If somebody eventually decides those memberships should be formally ended, or
reconstructed by asking people for their details, that is a board decision and
a data-entry job — not a schema change. The rows are ready for either.

## Account status vs membership status

Two states, deliberately separate, because they answer different questions and
are allowed to disagree:

| | Question | Where | Reversible |
| --- | --- | --- | --- |
| **Membership** | Have they paid, are they covered? | `Member.is_active`, the period ledger | by paying |
| **Account** | May they sign in and use the forum? | `User.disabled_at` | by an admin |
| **Erased** | Is the person gone from the record? | `User.deleted_at` | **no** |

Both directions occur. Somebody paid up for the year can be barred from signing
in. An account in good standing can sit here with no membership yet, or a
lapsed one. Until this existed the only way to stop somebody was to erase them,
which is irreversible and destroys the record you would want to keep if you
were suspending rather than deleting.

**Disabling never touches the membership.** Not the subscription, not the
ledger, not the end date — barring somebody is not a refund, and suspending a
person by cancelling what they paid for would be a different act with different
consequences. `User.account_status` and `Member.is_active` are shown as two
rows on the account page and two columns in the directory so they can never be
read as one thing.

What it does do:

* `load_user` returns None, so **open sessions end at the next request**. An
  account is usually switched off because somebody should stop using it now.
* Login is refused with a message that says so, rather than "invalid email or
  password" — the credentials were right, and sending them into a password
  reset that cannot help is worse than useless.
* `get_desired_state` returns inactive, so the **forum syncs them out**.
  Discourse holds its own group memberships; barring somebody here would
  otherwise mean nothing there and they would carry on posting.

The guards match the role editor's, because switching an account off removes
its access as surely as taking the role away: not your own account, and not the
last holder of a protected capability. `_active_holders_excluding` ignores
disabled accounts for the same reason — a switched-off admin is not cover, and
counting them would let the last working one be disabled after them.

The reason and who did it are recorded. "Disabled, and nobody remembers why" is
the state to avoid, and reactivating clears both rather than leaving a stale
reason attached to a working account.

## Member categories

Membership is not only for students, and never was — the statutes allow it.
`member.member_category` records which kind, and everything about the
categories lives in `aeronautics_members/member_categories.py`: the list, the
labels, the descriptions, and which categories are asked for a year group.

| Category | Year group | Who |
| --- | --- | --- |
| `student` | required | Currently studying at FH Joanneum |
| `alumni` | offered, optional | Studied here and is still a member |
| `staff` | not asked | Works at the institute, e.g. a lecturer |
| `partner` | not asked | Joined on behalf of a company |
| `honorary` | not asked | Appointed by the association |

**The year group cannot stand in for the category.** An alumnus has one, and so
may a lecturer who studied here, so the two say different things about a
person. An earlier version of this tried to derive "is a student" from whether
a year group was present; it works only while there are exactly two kinds of
member, and it broke the moment alumni existed. `Member.is_student` reads the
category.

Alumni are *offered* the year group but not required to give one: somebody who
studied here in 2006 may genuinely not know their cohort, and refusing their
membership over a label nobody needs would be absurd.

**Adding a category** is an edit to `member_categories.py` plus a migration
only if existing rows need re-pointing. The forms, the show/hide behaviour and
the admin screens all read from that module — the browser gets the rule through
`data-year-group-categories`, rendered from Python, so `member-kind-toggle.js`
never restates it. `tests/test_member_categories.py` fails if a new category is
added without a label, a description or a year group rule.

**Nothing about money is in there.** Every category pays the same annual fee
today, and there is one `stripe_price_id` for everyone. If alumni are ever
charged differently, `member_category` is what the billing code would key off,
and the work is in `services/billing.py` — a price per category, plus deciding
what happens when somebody's category changes mid-period.

**Changing category is an identity change**, so it goes through the existing
request-and-approve flow rather than being self-service: `member_category` is
in `IDENTITY_MEMBER_FIELDS`, which is what puts it in the audit snapshot and
the approval screen.

## The two email addresses

Members give two, and they do different jobs. Confusing them is easy and was
the cause of a real bug, so:

| | `email_private` | `email_work` |
| --- | --- | --- |
| Login | yes (`users.email` mirrors it) | never |
| Portal mail | all of it | only its own confirmation |
| Confirmed | `users.email_verified_at` | `member.email_work_verified_at` |
| Required | always | students only |
| Domain checked | no | students only |

**`email_private` outlives the membership.** It is how the association reaches
somebody after they graduate, which is exactly when the other address stops
working — so it must not be a university one, and the signup form says so.

**`email_work` is evidence, not a contact.** For a student, a live address on
an allowed domain is the only thing here that says they study here *now*. It
gets its own confirmation link, sent to that address, because an unconfirmed
address proves nothing at all — anyone can type one.

The allowed domains are an admin setting (`institutional_email_domains`,
Settings → General), read through `services/institutional_email.py`. Subdomains
count; the boundary is checked on a dot so `notfh-joanneum.at` cannot pass as
`fh-joanneum.at`. An empty setting falls back to the built-in list rather than
refusing everybody, because a cleared box must not stop signups.

Which categories must give one, and whose domain is checked, lives in
`member_categories.py` beside the year group rules. Only students, for the same
reason: a partner gives a company address no list could anticipate, and an
alumnus's university address has usually already stopped working.

**The login cannot be an institutional address either.** The same domain list
rejects one in `email_private`, on every form that edits it. That is the one
mistake this form could not otherwise notice — a university address there is
well-formed and simply wrong, and the member finds out the day they graduate
and cannot log in. Rejecting it is also why the signup page carries no hint
text under the email fields: the explanation is the error message, so it
reaches the person who got it wrong instead of the many who did not.

**A confirmation belongs to the address, never to the member.** Changing
`email_work` withdraws it and clears the nonce, in `apply_member_profile`. That
is not tidiness — the forum claim is decided on this flag, so carrying a
confirmation across a change would let somebody confirm an address they can
read, edit the field to another student's, and claim that student's archived
forum account and posts.

## Planned: Archival Forum Accounts (not built)

Roughly 500–600 people have used the forum over the last decade. The intention
is to bring them into this system as a record of who posted what, showing name,
year group and avatar — not as members. **Decisions taken, so the design is not
re-argued from scratch:**

- **The posts stay in Discourse.** Content is migrated with Discourse's own
  tooling into the new instance; this database gains a person record per old
  user, linked through `ForumAccount`. No post ever lands in these tables.
- **An account is claimable.** If a former student returns they take over their
  existing record rather than getting a second one, so their old posts stay
  attributed to them. Expected to be rare; the point is not to foreclose it.

### It is not a role

Roles here are capability bundles, and an archival account has no capabilities.
An `"alumni"` entry in `ROLE_PERMISSIONS` would be an empty set that shows up as
a grantable checkbox in the role editor and says nothing true. This is a
*status* — the same axis as `deleted_at`, a fact about the person's relationship
to the association rather than about what they may do. Keep it off the
permission table.

### What is already safe

- No roles means `can()` is false for everything, so no route, tab or button
  opens.
- `get_admin_recipient_emails()` selects on `NOTIFICATIONS_RECEIVE`, so archival
  accounts are never mailed a digest.
- `check_password` returns `False` when `password_hash` is `NULL`, so a
  passwordless row cannot be signed into directly.

### What would have to change

- **Every count.** `get_admin_dashboard_metrics()` and `get_membership_summary()`
  now exclude erased rows through a `present` filter; archival rows need the same
  treatment or *Linked Members* reads 613 beside *Active Memberships* 9. The
  filter is the pattern to copy — one marker column, excluded everywhere.
- **The account directory.** 50 per page becomes 13 pages, and "Member Only"
  fills with people who are not members. Needs its own filter value and probably
  a default that hides them.
- **The nightly clean-up of unfinished signups** removes bare `pending_checkout`
  members after 90 days (with a notice 7 days before). An import that sets that
  status by accident would have its rows emailed and removed. Give archival
  rows a status of their own.

### Identity: what actually names a person

`User.id`. It is what Discourse knows people by — `build_sso_payload` sends
`"external_id": str(user.id)` and `ForumAccount.external_id` stores the same —
and it is what every foreign key in this database points at. Email address and
forum username are mutable attributes with unique constraints; changing either
orphans nothing, because nothing identifies by them.

There is deliberately **no human-facing account number**. Add one only if the
board finds itself needing to quote an account out loud, and note that `user.id`
is sequential: fine in an authenticated admin URL, wrong for a public profile
link, which would advertise how many accounts exist.

### Not "alumni" — two facts, not one label

A status meaning "used to be a student" collides with the truth that such a
person can rejoin and become an active member. The way out is to store no label,
because the two facts are already separate and already available:

- **Can they sign in?** `password_hash IS NULL` says no.
- **Are they a member?** The coverage ledger says.

An imported forum person is simply *no password, no coverage*. Someone who
returns gets a password and a membership — the same row, with nothing to
un-label and no state machine to get wrong. "No membership since 2016" is text
the directory *derives*; it is not a column.

The only thing worth storing is how the row got there (`imported_at` or
similar), which is provenance rather than status, and it is what gates the rule
below.

### Email addresses are history, never identity

The old accounts used university addresses, which are disabled when a student
leaves. Worse, it is not known whether the university reissues an address to a
later student with the same name. If it does, a *current* student could hold the
address of someone who graduated a decade ago.

So an archival account must never be reachable by its recorded address:

- No password reset and no registration may match against one. The front door is
  already shut (`check_password` returns `False` with a `NULL` hash); this is the
  side door.
- **Claiming is administrator-driven.** A returning student is matched to their
  old record by a person who recognises them, from the name, year group and
  forum username. An automatic match on email address *is* the vulnerability,
  not a convenience feature.
- Import the addresses as a historical field or not at all, but do not put them
  in `users.email` where the authentication paths will find them.

Designing it this way makes the reissue question moot, which is the point: the
answer is unknown and does not have to be found out.

### Data protection: a recorded decision

The association considers this forum a shared record of a course community
spanning more than a decade, and treats the alumni listing as part of that.
Nobody has objected in twelve years of the old forum doing the same thing, and
anyone who does object is removed by hand on request — the administrator-side
erasure needs no email confirmation, so it works for an address that stopped
working years ago.

Noted for whoever revisits this: the retention basis is *not* the members' one.
§ 132 BAO covers people who paid, and these people did not. This rests on the
association's own interest in its history plus a standing offer to remove
anyone who asks, which is a weaker footing and worth re-examining if the
archive is ever made public outside the forum, or if photographs are shown
somewhere the subjects would not expect.

## Planned: Forum Access Control (not built)

Blocking one person from the forum, and showing different people different parts
of it — institute staff, for instance, should not necessarily see everything.
Since this portal is the only identity provider, it has to drive that.

**Half of it already exists.** `_build_group_fields()` in `forum_service.py`
turns a desired state into Discourse `add_groups` / `remove_groups`, driven by
the `forum_onboarding_group`, `forum_member_group` and `forum_inactive_group`
settings. Discourse applies category permissions per group. So the boundary is
already where it belongs:

> The portal decides which groups somebody is in. Discourse decides what each
> group can see.

Granular access therefore needs **no new mechanism here** — it needs more groups
and category permissions configured in Discourse. What is missing on this side:

- `get_desired_state()` derives purely from membership, so one person cannot be
  treated differently from another in the same state.
- Three group names, hard-coded as settings.
- No blocked state.

The shape to build is a `FORUM_PROFILES` table mapping a profile name to a set
of Discourse groups — the same shape as `ROLE_PERMISSIONS` — plus a nullable
per-account override, where the membership-derived default applies unless
somebody sets one. Blocking is then a profile with no groups. Institute staff
need no special code: no portal role, no membership, forum profile `staff`.

**Do not put forum groups in `ROLE_PERMISSIONS`.** A portal role says what
somebody may do *here*; a forum profile says what they may see *there*. Same
shape, different meaning. Conflating them means granting forum visibility would
silently imply portal capabilities, which is the exact class of mistake the
permission table exists to prevent. Two tables.

### The portal must survive without it

The launch plan is portal and new forum together, with the fallback of going
live on the portal alone while the old forum keeps running. That fallback is the
`forum_integration_enabled = False` configuration, and
`tests/test_forum_disabled_deployment.py` exercises it against the **real**
`ForumService` rather than a stub: signing in, the account page, saving a
profile, the data export, both forum routes refusing without a 500, every admin
page rendering, the health report staying green, and no forum work piling up in
the outbox with nothing to deliver it to.

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

## How Often Admins Are Emailed

Admin emails are throttled per kind of event (its `event_type`: "forum sync
failed", "profile picture uploaded", …), not per channel and not per message —
the message names the member, so a hundred members hitting one problem would
otherwise be a hundred "new" errors.

- The **first and second** of a kind go out at once; the second is what shows
  it is coming back.
- After that they are collected into summaries: **errors** after 1 hour, then
  4 hours, then daily; **review items** every 30 minutes. Each summary lists
  everything collected, with counts.
- A kind that has been quiet for 24 hours starts afresh.
- **Review items wait one minute** before any email, so photos uploaded
  together arrive as one email. Errors do not wait.
- However many kinds fire at once, at most **10 admin emails an hour**; what
  does not fit goes into the next.
- A photo or change request undecided for **more than a day** brings a daily
  reminder.

Emails to members are never throttled. The values are in
`aeronautics_members/notification_service.py`; the admin settings page shows
which kinds are currently held back and until when.

## When Queued Emails Stop Moving

A failed welcome email is retried after 15 minutes and again after 24 hours,
then marked `exhausted` and reported as a problem. The retries are driven by the
`jaeronautics-notifications.timer`, which fires every 2 minutes.

The health panel used to show only how many emails were queued, and to warn
only above twenty. That cannot distinguish three emails backing off normally
from three emails nobody is delivering — which is the failure that actually
happens, because it looks like nothing at all. It now also counts emails whose
`next_attempt_at` passed more than an hour ago (many missed runs) and reports
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

Emails addressed to domains that do not exist — a mistyped address at signup, or
the invented ones used while testing — will never send. They exhaust after a day
and then appear under **Emails that could not be delivered** on the Maintenance
tab, with the recipient, the error and two buttons:

- **Retry** puts the job back at the front of the queue. Delivery re-reads the
  address from the member's profile rather than the one recorded on the job, so
  correct the typo on the profile first and the retry goes to the new address.
- **Dismiss** stops reporting it. The row is cancelled, not deleted: what was
  attempted for a member is part of that account's record and the data export
  reads it.

Nothing prunes these rows — `cleanup-logs` covers audit and notification history
only — so without those buttons a single mistyped address would have left the
health report permanently red with no way to clear it but SQL.

## Translations in Background Jobs

The Babel locale selector reads the request's `Accept-Language`, and Babel calls
it for *every* `_()`. The notification timer, the CLI commands and the webhook
worker have an application context but no request, so a translated string in any
of them raised `Working outside of request context` and took the whole pass down
— reachable, for instance, by turning automatic emails off while a welcome-email
retry was queued. The selector now returns the default locale when there is no
request. Background code may use `_()` freely; it will render in English.

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
