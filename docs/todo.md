# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batches built everything that was on the list -- most recently
(2026-10-11) Stripe's book kept in the portal, with every team figure shown
line by line and the credit's top-up fees followed to each sale
(`docs/money-ledger-plan.md`), and forum changes started at once. What it
was is in their pull requests and in git history (`git log -- docs/todo.md`).
The legal drafts and the card readers below were put off by the maintainer.

## Open

### Legal texts: publish the 2026-10-08 drafts (pictures, credit, messages)

- **Added:** 2026-10-08
- **Where:** `legal/privacy-policy/{de,en}/2026-10-08.md`, `status: "draft"`.
- **Why:** the portal shows profile pictures since that batch (to the person,
  their teams' members and leads, and admins; the old forum's picture for
  reconnected members); the policy said so only for the forum. The draft
  adds it to § 22 and "profile picture" to the list in § 27.
- **Also (added 2026-10-08, with credit):** the privacy policy draft names
  the credit balance (§ 20, § 41), and a new draft of the membership terms,
  `legal/membership-terms/{de,en}/2026-10-08.md`, adds § 31 "Credit" (the
  later sections move up by one). See `docs/credit-plan.md`.
- **Also (added 2026-10-09, with messages):** the privacy policy draft names
  the contact form, messages to team leads, announcements and team mailings
  (§ 27, § 31, § 40, § 41). See `docs/messages-plan.md`.
- **Also (added 2026-10-11, with Stripe's book):** the privacy policy draft
  says that the portal keeps a copy of the Stripe account's bookings without
  names, and that a difference with Stripe names the payer's address and is
  kept a year after it is resolved (§ 20, § 40). See
  `docs/money-ledger-plan.md`.
- **Waiting for (2026-10-11):** the maintainer discusses them with the
  association's board, the credit feature above all ("I haven't really
  discussed with them if they like it"). Shown under Admin › Legal texts,
  *Not in force yet*.
- **Once approved:** `status: "published"` in all four files, with the day
  of publishing as `version`/`effective_from` (and the file names); check
  that nothing refers to the old § 31-36 of the membership terms.

### Card readers for credit (later)

- **Added:** 2026-10-09
- **Put off by the maintainer:** "this is not something we need to build now".
- **What, how, open questions:** `docs/credit-plan.md`, "Card readers". The
  base it builds on is done: price lists, `credit_sales.sell(...)`, the
  accounting for teams and the association.

### Updates: a failed migration undoes itself

- **Added:** 2026-10-11
- **What, where, decided:** `docs/update-safety.md`. In short: in
  `install.sh`, only when the new code brings a migration -- stop the app
  (the existing maintenance page shows), dump the database, migrate; if that
  fails, load the dump back, return to the rollback point's code and start
  it again. Without a migration the update runs exactly as today.
- **Why:** the update to pull request #30 left the live server half-updated
  (new code on disk, old app running on a half-migrated database) until the
  fix was merged. Since then CI runs every migration on MariaDB
  (`migrations-mariadb`), which would have caught that one before `main`;
  this is the safety net behind it.
- **Decided:** the in-place update stays ("never change a running system");
  about a minute of downtime with a message is fine for an update with a
  migration. No new maintenance page: nginx already shows
  `static/maintenance.html` whenever the app does not answer.
- **Open:** see the plan's "Open".

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

- **Each version in its own folder, built beside the running one** --
  2026-10-11. As `angeeinstein/study-helper` does: a folder per version with
  its own Python environment, switched to only once healthy, the previous one
  kept for an instant rollback. Put off: "never change a running system"; the
  in-place update works, and the case that failed is covered by the item
  above. Worth it if an update ever fails in a way that does not (a package
  install, an app that does not start). `docs/update-safety.md`.
- **An export of Stripe's bookings for the yearly accounts** -- 2026-10-11.
  A period's bookings by membership, teams, credit and other, with fees.
  "Could be useful at some time, but I don't need this now." Builds on the
  copy of Stripe's books (`docs/money-ledger-plan.md`).
- **Stripe's transfers to the bank, matched to their bookings** --
  2026-10-11. Not needed while nothing has been transferred to the bank.
- **Answering contact messages from inside the portal** -- 2026-10-09. A
  reply written in Admin › Messages and sent as a formatted HTML email, with
  the conversation kept together. "Would be nice … but I think we can stay at
  the normal email reply for now." See `docs/messages-plan.md`.
- **The association's Odoo website** -- 2026-10-07. People confuse it with the
  portal and try to sign in there. No decision.
- **Rounded corners** -- 2026-10-07. Discussed what switching the square look
  to rounded corners would cost; nothing decided.
- **Credit in the person menu at the top right** -- 2026-10-08. The
  maintainer was unsure it is needed; left out to keep the top bar minimal
  (the overview's tile and the side menu show it). A line in
  `frame/UserMenu.tsx` reading `GET /api/v1/account/credit` if wanted.
- **The admin entry** -- the gear at the top right was kept; a shield with an
  "Admin" label and a count of reviews waiting was suggested as an
  alternative.

## Turned down

What the maintainer did not want, and why -- a record, not a ban. Any of it
may be suggested again, saying what has changed since. Only what the
maintainer explicitly says must never change is marked so.

- **Handling the forum's read-only mode in the portal** (2026-10-09).
  - The proposal had two parts:
    - while Discourse is read-only, *Forum* would show a short portal page
      ("read-only for maintenance, you can read but not sign in") instead
      of Discourse's dead end;
    - syncs Discourse refuses for read-only would count as "try again
      later", not as errors.
  - Discourse marks its answers in read-only mode, so this can be detected.
  - The maintainer: not worth the complexity for now, since read-only only
    happens when they switch it on by hand for a short time during a backup
    or update.
  - The spurious emails it causes are handled by the to-do item above
    ("Admin error emails").

- **A short code for staff and company members like the year group**
  (2026-10-08). The year group feeds forum usernames (`_L25`), the forum's
  cohort groups and team lists; a made-up code would make fake cohorts and
  break the scheme (three letters, two digits), and a kind of member can change
  while a username should not. If it comes up again: the forum's groups per
  kind can carry a flair and a title instead, and a company member's company
  name could be their forum title -- offered, not wanted for now.
