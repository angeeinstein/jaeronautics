# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batches (2026-10-08) built everything that was on the list, and
credit (`docs/credit-plan.md`); what it was is in their pull requests and in
git history (`git log -- docs/todo.md`).

## Open

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

### My Account overview: Credit and Teams side by side

- **Added:** 2026-10-09
- **Where:** My Account › Overview, `frontend/src/pages/account/Account.tsx`
  (`Body`, `TeamsTile`, the `wide` prop of `Tile`).
- **What:** with credit on, the tiles are Membership and Forum in the first
  row, Credit alone in the second (half width, a hole beside it), Teams
  across the full width in the third. That "breaks up the existing
  overview". Make it a 2 × 2 grid: Membership, Forum / Credit, Teams. Teams
  stays full width only when it would otherwise stand alone, i.e. with credit
  off.
- **Decided:** the Credit tile keeps its size, the same as Membership and
  Forum; that is prominent enough. A credit-card-shaped card in the header
  was suggested and not wanted for now: credit "is not meant to be the main
  feature of the association", and it is not yet clear whether or how it
  will be used. If credit becomes important, it may be suggested again. A
  slim line at the bottom was also offered and not wanted.
- **Open:** nothing.

### Credit settings: menus show the change without a reload

- **Added:** 2026-10-09
- **Where:** Admin › Settings › Credit,
  `frontend/src/pages/admin/settings/CreditSettings.tsx`.
- **What:** after switching credit on, the maintainer had to reload the page
  before *Credit* appeared in the menus. The menus come from `GET
  /api/v1/me` (`credit_area`, `credit_admin`), which is not asked again
  after the save; the admin frame asks every 30 s, My Account never. After a
  successful save, invalidate `['me']`, as `pages/admin/teams/shared.ts`
  does for the teams switch. Add a test.
- **Open:** nothing.

### Membership tile: "Ended" beside "paid until" and "Renews"

- **Added:** 2026-10-09
- **Where:** My Account › Overview, Membership tile
  (`frontend/src/pages/account/Account.tsx`, `MembershipTile`; the data
  from `_membership` in `aeronautics_members/api/account.py`).
- **What:** on the test server the maintainer's own tile showed the pill
  *Ended* together with "31.12.2026 paid until", the progress bar and
  "Renews 01.01.2027". The pill is taken from `payment_status`
  (`canceled` → "Ended"); the dates and "Renews" from the membership dates
  and `cancel_at_period_end` (false). Most likely the test subscription was
  cancelled at once in Stripe's test mode while the paid period stayed.
- **To do:** find how the account got there (Admin › Accounts › the
  account's Activity and Membership tabs on the test server). Then make the
  tile consistent: never "Ended" while access is active, and no "Renews" for
  a subscription that is cancelled. Fix the state handling if it is wrong
  there, not only the display.
- **Open:** whether a test-mode subscription cancelled by hand explains it
  completely.

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

- **A short code for staff and company members like the year group**
  (2026-10-08). The year group feeds forum usernames (`_L25`), the forum's
  cohort groups and team lists; a made-up code would make fake cohorts and
  break the scheme (three letters, two digits), and a kind of member can change
  while a username should not. If it comes up again: the forum's groups per
  kind can carry a flair and a title instead, and a company member's company
  name could be their forum title -- offered, not wanted for now.
