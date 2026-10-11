# Updates that undo a failed migration

Agreed 2026-10-11, after the update to pull request #30 failed on the live
server. Built: the undo (below), and CI runs every migration on MariaDB (job `migrations-mariadb`,
`docs/maintenance.md`, "Continuous Integration"), which would have stopped
that migration before `main`.

## What happened

Migration `e2b7c4f9a1d3` stopped at its last step on MariaDB (raw SQL naming
the column `key`, a reserved word there; the tests run on SQLite). MariaDB
commits each table change at once, so the steps before it stayed done --
among them `payments.team_bears_fee` dropped -- while the database still
counted the migration as not run. The installer stopped there: new code on
disk, the old app still running and reading the dropped column, until the fix
(#31) was merged and the update run again.

## How an update runs today (`install.sh`, `install_or_update`)

1. `prepare_frontend` -- waits for CI's build; nothing touched yet.
2. `backup_runtime_state` -- environment, state, a database dump
   (`LAST_DB_BACKUP_FILE`, taken while the app still runs), uploads.
3. `record_rollback_point` -- the revision being replaced, the schema revision
   and the dump (`rollback.conf`).
4. `pause_background_jobs`.
5. `ensure_repo_present` -- the code is replaced in place; the running app
   keeps the old code in memory.
6. Python packages, front end, `initialize_database_schema` (`flask db-init`:
   migrations, then seeding) -- still with the old app serving.
7. `reload_services` -- the app restarts on the new code.

`on_error` resumes the background jobs and collects diagnostics; nothing is
undone. `--rollback` restores the code only and leaves the schema.

## Decided

- **Never change a running system** (the maintainer): the in-place update
  stays. Only the case that failed gets a safety net. Version folders built
  beside the running one (as study-helper does) are an idea for later.
- **About a minute of downtime is fine** for an update with a migration,
  "because I'm not doing updates every 10 minutes", as long as visitors see a
  message. They do: nginx already shows `static/maintenance.html` with a 503
  whenever the app does not answer, and Stripe resends its webhooks.

## How it works (built 2026-10-11)

`install.sh`, `migrate_database_for_update` (called by `install_or_update`
for an update; a fresh install and a database move migrate as before):

- `flask db-pending` -- the new code's migration heads against the
  database's -- says yes or no. **No:** `db-init` as before, no stop.
  **Yes, or it cannot tell:** the careful way.
- Stop the app (the timers are paused already), dump the database
  (`dump_database_before_migration`: verified like any dump here, row counts
  kept), run `db-init`.
- **It fails:** drop every table, load the dump, compare the row counts
  (`restore_database_before_migration`); then `roll_back_installation` -- the
  rollback point's revision checked out, its packages, its front end (kept in
  `/var/cache/jaeronautics/frontend`), units, restart -- and the update ends
  failed, saying it was undone. If the previous version is this same code (an
  update run again after a failure), it is only started again.
- **The dump fails:** nothing migrated; the previous code put back the same way.
- **Putting back fails:** `on_error` says so and leaves the portal stopped
  behind the maintenance page, naming the dump.
- **A later step fails after a migration that went through:** the portal is
  started again on the new code, which fits the migrated database.

The decision how to undo lives in the installer of the version being
installed (the update re-runs the new `install.sh`), so it takes effect from
the first update *after* the one that brings it.

Tested by `tests/test_install_update_undo.sh` against a real MariaDB, with a
migration that stops part way as the live one did: a table made, a column
dropped, a row changed, then an error. The database comes back exactly
(every table's definition and rows). In CI: `installer-mariadb`.

## Trying it on the test server

Branch `claude/update-undo-trial` adds a migration that fails on purpose --
only on a server that has the file `/etc/jaeronautics/undo-trial`; anywhere
else it does nothing, so its CI passes. On the test server:

1. Update to `claude/update-undo` first (the undo has to be in the installed
   version, see above).
2. `sudo touch /etc/jaeronautics/undo-trial`, then update to
   `claude/update-undo-trial`. Expected: the maintenance page for about a
   minute, the update failing with "The update was undone", the portal back
   on `claude/update-undo` with its data as before.
3. `sudo rm /etc/jaeronautics/undo-trial`, and update to the branch to be
   used next. The trial branch is not merged.

**Tried 2026-10-11** on the test server, twice. The migration failed after
making its table; the database came back with identical row counts; the
previous version was put back and verified; the portal was down for about 8
seconds. The second time also showed the two changes that came of the
first: an open page said "Update in progress" and loaded again by itself, and
the update ended with its own message only.
