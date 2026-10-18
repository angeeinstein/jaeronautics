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
checked by its chip), attendance with the same cards
(`docs/attendance-plan.md`), answering contact messages from the portal
(`docs/messages-plan.md`), and the batch of 2026-10-16 (Settings › Email
with its tabs, Admin › Attendance always in the menu, a hint under every
email field when a university address looks mistyped), and, with the
security fixes of 2026-10-17, no browser autofill in the Microsoft 365 fields
and the export password; then email through Microsoft 365 with a backup
account (`docs/microsoft-mail-plan.md`, merged 2026-10-18 and in use on the
live portal). The price-list proposal below waits for the maintainer's
answer.

## Open

### Messages: the whole conversation in the email, an answer without its quote

- **Added:** 2026-10-18
- **What:** (1) the email of an answer written in Admin › Messages quotes the
  whole conversation, newest first, capped at about the last 10 steps, the
  contact form's message always at the bottom; (2) an answer that comes back
  by email is shown without the part that quotes our email, with *Show
  quoted text* to open the rest.
- **Where:** `services/messages.py` (`_send_reply`: today
  `quoted_text=message.body`), `templates/emails/contact_reply.html`,
  `services/mail_replies.py` (`_text_of`), `db_models.ContactReply` (the
  whole text kept), `frontend/src/components/MessageList.tsx`.
- **Why:** "even after some writing back and forth, the reply … is still the
  original message, and this could be a bit confusing"; the answer shown in
  the portal carried our whole email again (logo text, "Support <…> schrieb
  am …", the copyright line).
- **Decided:** whole conversation rather than only the last message; the
  first message kept "because this message was the original question or
  problem"; trimming "makes sense". The details, and where the quote is
  cut: `docs/messages-plan.md`, "Whole conversations, and team leads
  answering in the portal".
- The same code serves the team replies below; built first, or with them.

### Side menu: the groups set apart more clearly

- **Added:** 2026-10-18
- **What:** the groups of a side menu (People, Money, Messages, Teams,
  System in the admin area) read as one long list; the headings are small
  and muted (`.groupLabel` in `frontend/src/frame/Frame.module.css`). "Make
  this sidebar menu a bit more intuitive and easy to use … separate those
  categories a bit more."
- **Where:** `frame/Sidebar.tsx` and `Frame.module.css` -- one component for
  the admin area, Settings and a team's area, so all three change. My
  Account's menu has no groups and stays ("not necessary because it has no
  categories").
- **Two ways, open question which:**
  - The maintainer's: the headings in cyan.
  - Suggested instead: a 1px hairline above each group but the first, more
    room above a heading (about 1.25rem instead of 0.875rem), and the
    heading in the secondary text colour rather than the muted one, still
    small capitals. Because `docs/design.md` keeps cyan for "act here"
    ("It is not decoration") and the current page in the menu is already
    cyan -- cyan headings would compete with it.
- The maintainer was "open for" another idea; asked 2026-10-18.

### Team leads answer their team's messages in the portal

- **Added:** 2026-10-18
- **What:** a feature, its own branch. Admins choose a mail account for a
  team (its shared mailbox); the team's leads in force answer a message on
  the team's Messages page; it goes out from that account; answers come back
  into the thread when it is a Microsoft 365 mailbox, and the leads are told
  by email. A team without an account keeps *Answer by email* (a `mailto:`
  link), which goes once it has one; so does the Reply-To of the email to
  the leads.
- **Where:** `docs/messages-plan.md`, "Whole conversations, and team leads
  answering in the portal" -- what was decided, in full.
- **Why:** today the lead answers from their private mailbox, so the sender
  sees that address and the portal never sees the answer.
- **When:** "I don't want to work on this now" (2026-10-18) -- built when the
  maintainer asks for it, not with a batch.

### Microsoft 365: narrow the app's mail permissions (later)

- **Added:** 2026-10-18
- **What:** for testing, the app registration `Portal mail` was given the
  Microsoft Graph *application* permissions `Mail.Send` and `Mail.Read`
  with admin consent -- they reach **every mailbox** in the tenant. They are
  to be replaced by the mailbox-scoped Exchange roles of
  `docs/microsoft-mail-setup.md`, step 5.
- **Why:** the certificate's key is in the portal's database and backups;
  with these permissions, whoever had it could read and send from every
  mailbox of the association. The maintainer chose the narrow way
  ("the better, more locked down version"); the wide one is only so the
  connection can be tested while Exchange is not ready.
- **State:** step 5 is done except `New-ManagementRoleAssignment` (both
  lines), which Exchange refused for the Global Administrator right after
  `Enable-OrganizationCustomization` -- most likely until the tenant is
  "hydrated" (`Get-OrganizationConfig | Format-List IsDehydrated` says
  `False`; up to 24 hours).
- **How, in this order:** run the two `New-ManagementRoleAssignment` lines;
  `Test-ServicePrincipalAuthorization` shows the roles *InScope: True*
  (support: Send and Read; noreply: Send only); **then** remove `Mail.Send`
  and `Mail.Read` under API permissions and revoke the consent under
  *Enterprise applications* › `Portal mail` › *Permissions*; then *Test*
  on both accounts in Settings › Email. The two kinds add up: the roles do
  not narrow what is granted in Entra.
- **Put off (2026-10-18):** "that's a point for later on." The live
  portal sends through the Microsoft accounts since 2026-10-18 with the wide
  permissions.
- **Changed (2026-10-18):** with team mailboxes coming (the item above), not
  two mailboxes named one by one: a **mail-enabled security group**
  ("Portal mailboxes") holds every mailbox the portal may use, and the
  management scope is that group -- a new team mailbox is then added to the
  group in the admin center, no PowerShell. `docs/microsoft-mail-setup.md`,
  step 5, to be rewritten for it when this is done. Sending only for
  `noreply` stays possible with a second group, if still wanted.

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
- **Also (added 2026-10-16, with answering contact messages):** the
  privacy policy draft says an answer written in the portal is kept with the
  message, and a copy may go to the association's mailbox (§ 31, the
  contact form, and the retention table). See `docs/messages-plan.md`.
- **Also (added 2026-10-15, with attendance):** the privacy policy draft
  names the stays recorded at attendance readers -- who, which team, in and
  out with the reader, corrections and by whom; who sees them; kept with the
  account, in the export, deleted with it (§ 20, after the cards). See
  `docs/attendance-plan.md`.
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

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

- **The server log in the portal** -- 2026-10-16. The journal of the portal's
  services readable on a phone, for when SSH is not at hand ("if some issue
  occurs and I don't have access to a computer"). Put off: not urgent, the
  command line has it. What was talked through:
  - **Where:** a *Server log* tab beside System health's checks, with
    `system.update`. Lines with time, service, level (errors red, warnings
    amber); a traceback folded into its error; filters for errors only, one
    service, a search; *Follow* (asks every few seconds for what came after
    the last line -- no open stream holding a gunicorn worker); *Load
    earlier*; *Copy*.
  - **Reading the journal:** not the `systemd-journal` group (the whole
    server's journal: SSH logins, `sudo` commands). A small root-owned script
    (`/usr/local/bin/jaeronautics-log`, installed by `install.sh`), the only
    thing the portal may run with `sudo`, takes a service from a fixed list, a
    line count and a cursor, checks them, and calls `journalctl -o json`. It
    reads nothing the portal can write. The command line stays unrestricted
    ("rather more information than less").
  - **Services:** the portal's own, nginx, MariaDB, Redis, and systemd's
    messages about them (failed, restarted, out of memory). Not SSH, `sudo`,
    the kernel. Open: Discourse, if on the same server.
  - **Secrets out before a line leaves the server**, done by the portal (it
    holds the secrets anyway, so doing it as root protects nothing more and
    would make a root process read portal-controlled data): every exact value
    it knows (`.env` secrets, SMTP passwords, Discourse key and secret,
    device key secrets) replaced with `[hidden: …]`, then shapes (`sk_`,
    `rk_`, `whsec_`, `xkeysib-`, `Bearer`, `Api-Key:`, `password=`, a
    password in a database address). At the source, `hide_parameters` so
    database errors stop printing their values. Personal data stays: useful
    to identify an error, and those with `system.update` are trusted with it.
  - Opening it not recorded in Admin › Logs (noise for one or two people).
- **Faster CI** -- 2026-10-16. A run takes about 7½ min; the slowest job by
  far is the Python tests (`test` in `.github/workflows/ci.yml`: pytest
  about 6½ min on GitHub's 4-core runner, already with `-n auto`). Front
  end about 3 min; e2e 2½-3 min per part (3 parts, one Playwright worker
  each: the tests share one seeded database on port 8777); MariaDB jobs
  under 1 min. The maintainer wants to think about which way to go. Two
  ways talked through:
  - **Split the Python tests into 3-4 parts on GitHub's runners**, like
    e2e: each test in exactly one part (by a stable split, or by measured
    durations so the parts are even), a check that the parts' counts add up
    to the whole, `passed` waiting for all parts. Expected: a run of about
    3 min. Low risk: the tests already pass spread over processes; each
    part repeats about 25 s of setup (free minutes: the repository is
    public). About an hour's work, CI only.
  - **A self-hosted runner on the maintainer's PC** (i9-14900K, 32 threads,
    64 GB, NVMe; on whenever they work). pytest perhaps 1½-2 min there; e2e
    hardly faster (serial). Whole run perhaps 2-2½ min with several isolated
    runner instances -- little more than the split gives. Needs: Linux (WSL2
    with Docker: the MariaDB job is a service container), Pango/HarfBuzz,
    Python, Node, Chromium; the runner as its own user, without access to
    personal files. Fallback when the PC is off: a first job on GitHub's
    runner asks the API (a token with *Administration: read* as a secret)
    whether the runner is online and idle and picks `runs-on` from that.
    Safety (public repository): never a fork's pull request on the PC --
    only pushes and pull requests from this repository's branches -- plus
    "Require approval for all external contributors" in Settings › Actions;
    pull requests cannot be switched off on a public repository
    (interaction limits last at most 6 months). The GPU does not help.
  - Suggested order: the split first; measure `time python -m pytest -q -n
    24` on the PC before deciding on a runner.
- **Connected services beside the forum (Telegram and others)** --
  2026-10-17. Asked only how it would look: "I am not planning on
  implementing anything of those services just yet." What was sketched:
  - Today the forum is the only outside service that follows a membership
    (queued sync at the change, started at once, retried; every night all
    members again -- `services/outbox.py`, `forum_service.sync_member`,
    "desired state" then the difference). Teams end with the association
    (`team_payments.follow_association_end`); the rest is checked live.
  - Plug-ins like the device types: each service declares its settings,
    what a member should have there, how to make it so, how an account is
    linked. One "membership changed" trigger queues a job per enabled
    service; one nightly check for all; one table of linked accounts
    (service, external ID, state, last error) shown on My Account, the
    admin's account page and System health. The forum the first plug-in.
  - Telegram: the member links their account once through the bot; the
    group approves join requests, the bot approves only active members;
    at the end the bot removes them (ban, then unban). Fits Discord, Slack,
    Microsoft 365 groups through Graph, a GitHub organisation, Nextcloud.
  - Open: which service first; teams too (a group per team); removal on the
    last day or a grace period.
- **CI passes a push that changes only `docs/` in under a minute** --
  2026-10-17. Every push runs the whole of CI, a to-do item too ("this
  obviously doesn't make any sense when it's just a to-do list item"). Put
  off: "Let's do this some point later." Talked through:
  - Not `[skip ci]` or `paths-ignore`: the commit is then left without a
    CI result -- a pull request whose last commit it is cannot be merged
    (the required check waits forever), and `install.sh` waits 3 minutes
    for a run that never comes, then installs "without its result"; the
    Updates page shows no run.
  - Instead, in job `tested` of `.github/workflows/ci.yml`: when the commit
    before passed (its tree's `ci-passed-<tree>` mark is there) and every
    file changed since is under `docs/`, count it as passed -- skip the
    tests, leave the mark for the new tree. About 30-40 seconds; the check
    green, the installer and the Updates page see "passed".
  - Only `docs/` (not `legal/`: rendered and made into PDFs, tested).
    `docs/device-protocol.md` is offered as a download on the Firmware page
    but no test reads it.
  - The pull-request run (on GitHub's merge commit) needs the same rule,
    compared with the pull request's previous head.
  - Not holding to-do commits back: the list is pushed the moment it changes.
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

- **The credit switch hiding everything that belongs to credit** (2026-10-15).
  Asked: with credit off, Admin › Credit and Admin › Sales still showed (they
  stay while anybody holds a balance, so it can be refunded). Proposed: the
  switch as *Credit and sales* -- Admin › Sales hidden, Admin › Credit only
  with a note while balances remain, *Sales point* not offered as a type. The
  maintainer, once they knew a sales point already refuses every tap with
  "Paying with credit is switched off" and a team's Sales page and members'
  credit are hidden: "we can leave it as is. This is fine."

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
