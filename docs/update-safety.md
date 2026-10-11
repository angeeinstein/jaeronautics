# Updates that undo a failed migration

Agreed 2026-10-11, after the update to pull request #30 failed on the live
server. To build: `docs/todo.md`, "Updates: a failed migration undoes itself"
and "CI: run the migrations on MariaDB".

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

## The change

**Only when the new code brings a migration** (the database's Alembic
revision differs from the new code's head, checked after step 5 with the new
code's `flask db current`/`heads`). Otherwise the update runs exactly as
today.

1. Stop the app and its timers (the maintenance page shows), then take a
   database dump *now*, so nothing written since is lost if it has to be
   restored. The dump from step 2 stays as it is.
2. Run `db-init`.
3. If it fails:
   - load that dump back into the database (drop and recreate it first:
     a half-run migration leaves tables the dump does not drop);
   - put the code back at the rollback point (`git checkout` of the recorded
     revision, its Python packages, its front end build -- the installer
     keeps earlier builds for a rollback);
   - start the app and its timers, check `/__health`;
   - say in the log and on the Updates page what failed and that the
     previous version runs again, with the failing migration's message.
4. If it succeeds: as today.

If the undo itself fails, stop with the app down, the maintenance page
showing, and the dump's path in the log -- not half-updated.

## Open

- The check for "brings a migration" needs the new code's packages installed
  (step 6 comes before it), or a lighter look at `migrations/versions`.
- Whether the test server should get a branch with a migration that fails on
  purpose, for the maintainer to watch the undo once (proposed: yes, not
  merged).
- `tests/test_install_*.sh` style shell test for the undo, or only the run on
  the test server.
