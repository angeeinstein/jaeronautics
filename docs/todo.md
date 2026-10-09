# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batches (2026-10-08 and 2026-10-09) built everything that was on
the list, credit and selling for credit (`docs/credit-plan.md`); what it was
is in their pull requests and in git history (`git log -- docs/todo.md`).

## Open

### Admin error emails: drop what has resolved itself before it is sent

- **Added:** 2026-10-09
- **What happened:** the maintainer put the forum (Discourse) in read-only mode
  for a backup and an update. Meanwhile, clicking *Forum* in the portal made
  the account's forum sync fail ("forum_sync_failed" on the error channel).
  The maintainer switched read-only off and resynced, and the errors were
  gone. About an hour later an admin email still reported them.
  - The first two of a kind go out at once; the rest are held back and sent
    as a summary later (`KIND_SUMMARY_MINUTES`: 60, 240, 1440 minutes,
    `notification_service.py`, `_deliver_admin_digest`).
  - Nothing checks whether a held-back error is still a problem.
- **Maintainer:** "I think it would make sense to delete those events out of
  the queue if the underlying problem has solved itself."
- **What to build:**
  - Before a held-back admin error goes out, check whether it still holds.
    One check per kind (event_type), in a small registry. A kind without a
    check is sent as now.
  - A resolved event is not emailed. It is marked (e.g. status `resolved`,
    with when) rather than deleted, so the record stays.
  - Perhaps one line in the email: "N more resolved themselves before this
    email".
  - If every held-back event is resolved, no email.
- **First kind:** `forum_sync_failed`. It is resolved when that member's
  forum account has no `last_error` any more (cleared by a successful sync,
  `forum_service.py`, `sync_member`). The event carries `object_type`
  "forum_account" and `object_id`.
- **While building:** go through the other error kinds and add a check where
  "still a problem" can be read from the data, e.g.:
  - an email job that was sent after all;
  - a webhook event processed on a retry.
- **Open:** whether the resolved ones should also show somewhere, e.g. on
  System health or in the logs (they are in the event table either way).

### Legal texts: publish the 2026-10-08 drafts (pictures, credit)

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
- **Waiting for:** the maintainer reading the wording (shown under Admin ›
  Legal texts, *Not in force yet*). Once approved: `status: "published"` in
  all four files (the day stays, or the day it is published), nothing else.
- **Open:** whether the board wants to see it first.

### Card readers for credit (later)

- **Added:** 2026-10-09
- **Put off by the maintainer:** "this is not something we need to build now".
- **What, how, open questions:** `docs/credit-plan.md`, "Card readers". The
  base it builds on is done: price lists, `credit_sales.sell(...)`, the
  accounting for teams and the association.

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

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
