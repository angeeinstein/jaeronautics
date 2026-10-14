# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batches built everything that was on the list -- most recently
(2026-10-11) Stripe's book kept in the portal, with every team figure shown
line by line and the credit's top-up fees followed to each sale
(`docs/money-ledger-plan.md`), and forum changes started at once; then
Codex's review comments that still held (`docs/review-fixes.md`, branch
`claude/review-fixes`). What it
was is in their pull requests and in git history (`git log -- docs/todo.md`).
The legal drafts below wait for the board. Cards and sales points
(`docs/cards-plan.md`) are built; the sales point's menu with icons, linking a
card with a reader's QR code, device pairing and firmware updates on
`claude/device-pairing`. Attendance with the
same cards comes next, needed around January (not to be started before the
maintainer says so).

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
- **Also (added 2026-10-13, with cards):** the privacy policy draft names
  the cards linked to accounts: a name, a keyed hash of the ID and its end,
  when linked and last used, and that they go with the account (§ 20). See
  `docs/cards-plan.md`.
- **Also (added 2026-10-14, with linking a card by QR code):** a card not
  linked yet, tapped on a reader with a screen, is kept as its hash for a day
  (§ 20, the cards paragraph). See `docs/cards-plan.md`.
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

### Attendance with the same cards (later)

- **Added:** 2026-10-13
- **Put off by the maintainer:** needed "in January, maybe a bit before ...
  not something that we need to build now immediately".
- **What:** the flight team's members earn ECTS and must be there for a
  number of hours; members tap their card on a reader there. The cards and
  the readers exist (`docs/cards-plan.md`, `docs/devices-plan.md`); this is
  a device type of its own.
- **Open questions:** what counts (tapping in and out, a forgotten tap-out,
  a least time), who may see it (team leads, whoever handles ECTS), and the
  privacy policy for it.

### Devices: the marks of "our" firmware

- **Added:** 2026-10-14
- **What:** the check of an uploaded firmware file (and the device's own
  check before installing) for the few bytes that mark it as this
  association's firmware, once they are settled with the firmware.
- **Where:** `services/device_firmware.py`, `_marks` (it checks the project
  name now); the device's firmware.
- **Why, decided, open questions:** `docs/devices-plan.md`, "One device for
  everything", "Open". Pairing, the device saying what it can be, the
  settings by card and backup code, and firmware updates are built (branch
  `claude/device-pairing`); this is the part left for when the firmware
  side is settled. Signing the firmware: not planned.

### Credit, sales and devices: each in its own place

- **Added:** 2026-10-14
- **What:** the admin Credit page holds too much -- the summary tiles, *Sold*,
  the association's price list, the association's devices, *Balances*,
  *Latest* -- "this is all a bit mixed up. I think we should separate all of
  those things and make it clear what is about a device, what is about some
  history or some logs, and where do they belong. Maybe even split it and
  not put everything in the same credit submenu." The team pages to be
  looked at the same way.
- **And the device page:** an admin changing a device's owner from the
  association to a team without a price list could not save: the form sent
  the old owner's items with it (the server clears them itself on an owner
  change). The maintainer: "Maybe we should remove this items input in the
  devices page completely, because this should be done in another menu where
  we manage the price list ... this devices page should only be about
  technical stuff and things like the ownership and the name and place and
  description".
- **Open:** the new layout, proposed in the conversation of 2026-10-14 and
  to be agreed before it is built.

### Ask how somebody pays before Stripe's page: card now, SEPA in a few days

- **Added:** 2026-10-12
- **What:** before Stripe's payment page opens, a choice the person has to
  click: **Card, Apple Pay or Google Pay** ("your membership starts right
  away") or **SEPA Direct Debit** ("your membership starts once the debit
  clears, in a few days", the wording `Payment.tsx` already uses). For a team
  fee: "your team membership". Both answers open **the same** Stripe page,
  with both methods as now.
- **Where:**
  - joining (`frontend/src/pages/public/MembershipForm.tsx`): its last step,
    "How do you want to pay?";
  - starting a membership from an existing account, rejoining, "Resume
    payment" (`pages/account/`), and paying a team fee (`pages/teams/`): a
    small dialog when the pay button is pressed.

  One shared component, the same wording everywhere.
- **Why:** SEPA takes days, which is "most of all annoying for them, because
  they have to wait some time before they can use their account, or before
  they can become a team member". The maintainer wants people who do not care
  to choose card, and SEPA kept for those who have no card or prefer it. Text
  above or below a button is not read ("nobody reads such fine prints"); a
  choice they have to click is.
- **Decided:**
  - Both answers lead to the same Stripe page with card and SEPA. Somebody who
    picked card and did not find their method there might abandon and "plan to
    complete it later"; better they pick SEPA there. They have read the notice,
    so changing their mind is fine.
  - SEPA stays switched on in Stripe. The portal names the methods itself
    (`services/billing.py`, `services/payments.py`: card and `sepa_debit`),
    and members renewing by SEPA keep their mandate.
- **Ruled out:**
  - switching SEPA off in Stripe: it would probably break the portal's
    checkouts, and existing SEPA renewals;
  - a line of text near the pay button or on Stripe's page alone;
  - offering SEPA only on the cancel page;
  - opening Stripe's page with only the chosen method.

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

- **Each version in its own folder, built beside the running one** --
  2026-10-11. As `angeeinstein/study-helper` does: a folder per version with
  its own Python environment, switched to only once healthy, the previous one
  kept for an instant rollback. Put off: "never change a running system"; the
  in-place update works, and the case that failed -- a migration -- now
  undoes itself. Worth it if an update ever fails in another way (a package
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
