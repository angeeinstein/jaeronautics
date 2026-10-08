# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batches (both 2026-10-08) built everything that was on the list;
what it was is in their pull requests and in git history
(`git log -- docs/todo.md`). One thing waits for the maintainer.

## Open

### Privacy policy: publish the version that names where pictures are shown

- **Added:** 2026-10-08
- **Where:** `legal/privacy-policy/{de,en}/2026-10-08.md`, `status: "draft"`.
- **Why:** the portal shows profile pictures since that batch (to the person,
  their teams' members and leads, and admins; the old forum's picture for
  reconnected members); the policy said so only for the forum. The draft
  adds it to § 22 and "profile picture" to the list in § 27.
- **Waiting for:** the maintainer reading the wording (shown under Admin ›
  Legal texts, *Not in force yet*). Once approved: `status: "published"` in
  both files (the day stays, or the day it is published), nothing else.
- **Open:** whether the board wants to see it first.

### Credit: a balance members top up (prepared, nothing spends it yet)

- **Added:** 2026-10-08 (first an idea on 2026-10-07)
- **What, where, why, decided:** `docs/credit-plan.md` -- a switch, a Credit
  card on My Account, a Credit page with top-up and full history, Stripe
  Checkout on one "Credit" product with the amount set by the portal, an
  append-only ledger, an admin view.
- **Waiting for:** the maintainer's answers to the open questions at the end
  of that note; then it is built at once (a feature, not a small change).

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

- **The association's Odoo website** -- 2026-10-07. People confuse it with the
  portal and try to sign in there. No decision.
- **Rounded corners** -- 2026-10-07. Discussed what switching the square look
  to rounded corners would cost; nothing decided.
- **The admin entry** -- the gear at the top right was kept; a shield with an
  "Admin" label and a count of reviews waiting was suggested as an
  alternative.

## Decided against

- **A short code for staff and company members like the year group**
  (2026-10-08). The year group feeds forum usernames (`_L25`), the forum's
  cohort groups and team lists; a made-up code would make fake cohorts and
  break the scheme (three letters, two digits), and a kind of member can change
  while a username should not. If it comes up again: the forum's groups per
  kind can carry a flair and a title instead, and a company member's company
  name could be their forum title -- offered, not wanted for now.
