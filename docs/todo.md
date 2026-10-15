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
`claude/device-pairing`, with the batch of 2026-10-15 (price lists with their
figures and delete, a device's page slimmer, card or SEPA asked before
Stripe's page), and the batch after it (a sale switching a reader's
outputs, tabs on long pages and an icon in every panel, the firmware file
checked by its chip). Attendance with the
same cards comes next, needed around January (not to be started before the
maintainer says so).

## Open

### Price lists: deleted items folded into one row

- **Added:** 2026-10-15
- **What:** an item deleted after it was sold now stays in its price list for
  good, greyed, "No longer on the list" -- in *30 days* too, with 0. "Over
  the time, there are multiple or dozens of products that are deleted, but
  are still shown in the list here. This would be annoying." Instead, **one
  row**, folded: "▸ 3 no longer on the list · 9 sold · €18.40"; a click
  unfolds the items, greyed as now. In *30 days* it counts only those sold in
  the last 30 days, and is not there when there are none.
- **Where:** `frontend/src/components/PriceList.tsx` (`GoneRow`, the `gone`
  list), on Admin › Sales and a team's Sales page.
- **Why folded, not hidden:** *Together* still adds up -- what they made does
  not vanish from the totals -- and the detail stays one click away. A
  deleted item never sold stays out of the list, as now.
- **Open question:** the proposal above, offered 2026-10-15 (the maintainer
  named hiding, removing, a collapsed section or another list as options).

### Contact form: a hint when a university address looks mistyped

- **Added:** 2026-10-15
- **What:** on the contact form, when the address typed looks like a
  university or institute address but does not match one exactly, a small
  hint under the field that it may be wrong -- best with the likely right
  one: "Did you mean anna.muster@edu.fh-joanneum.at?". A hint only: the
  message can still be sent as typed.
- **Where:** `frontend/src/pages/public/Contact.tsx`, the *Email* field
  (visitors not signed in; a signed-in person's address is their account's).
  The domains to compare with are the portal's own, not written into the
  code: Admin › Settings › General, *University email domains*
  (`services/institutional_email.py`, `get_student_domains` /
  `get_staff_domains`; the front end has them from `GET /api/v1/forms/options`,
  `student_domains`, `staff_domains`).
- **Why:** "A common mistake that people make is that the email address is
  edu.fh-joanneum.at but people sometimes switch the point that's meant to
  separate the subdomain with the hyphen between fh and the joanneum" --
  `edu-fh.joanneum.at`, `edu.fh.joanneum.at` and the like, and the answer
  then never arrives.
- **Decided:** a hint, not a refusal, and **only for what looks like an
  accident** (asked 2026-10-15): the domain typed, with its dots and hyphens
  removed, equals one of the portal's domains with theirs removed, but the
  domain itself differs (`edu-fh.joanneum.at`, `edu.fh.joanneum.at`,
  `edufh-joanneum.at`); or it is one letter off one of them
  (`edu.fh-joaneum.at`). Any other address gets no hint at all: `gmail.com`,
  another university, and the portal's own domains spelled right -- staff's
  `fh-joanneum.at` is not taken for a mistyped student address.
- **Everywhere an address is typed** (2026-10-15: "if it's a very small
  fix, I think this makes sense" -- it is): one check and one hint shown
  under each field, the same wording everywhere --
  - the contact form (`pages/public/Contact.tsx`);
  - signing in (`pages/public/SignIn.tsx`) and *Forgot password*
    (`pages/public/ForgotPassword.tsx`), where a mistyped address otherwise
    only reads as "wrong address or password" / no email arriving;
  - joining (`pages/public/MembershipForm.tsx`): the sign-in address, and
    the university/institute address, whose refusal ("Please use your
    student address") then also says which one was likely meant;
  - My Account › Contact details (`pages/account/Contact.tsx`) and an
    admin correcting someone's address (`pages/admin/account/ProfileTab.tsx`).

  Not the test email in Settings. The domains come from
  `GET /api/v1/forms/options`, which the joining form already loads.

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

- **Undoing your own sale** on My Account for a few minutes (2026-10-15;
  listed under "Later" in `docs/cards-plan.md`). "I don't think that's
  necessary if somebody is too stupid to only pay for something once, then
  that's their problem" -- and a sale can still be taken back on the
  person's credit page.

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
