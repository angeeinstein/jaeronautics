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

| Capability | Admin | Super Admin |
|---|---|---|
| `admin.access` — open the admin workspace | yes | yes |
| `accounts.view`, `accounts.billing`, `accounts.privacy` | yes | yes |
| `approvals.review`, `forum.moderate`, `logs.view` | yes | yes |
| `notifications.manage` — test email, undelivered queue | yes | yes |
| `settings.general` | yes | yes |
| `settings.credentials` — Stripe/Discourse keys, mail accounts | no | yes |
| `system.update` — install a version, roll one back | no | yes |
| `roles.manage` — grant or revoke access | no | yes |

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
reaches `/admin/forum`, is bounced from settings, logs and updates, counts
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
- **`cleanup-pending-signups`** deletes stale `pending_checkout` members after
  14 days. An import that sets that status by accident would quietly delete the
  archive a fortnight later. Give archival rows a status of their own.

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
