# Permissions per person, roles as templates

Talked about on 2026-11-04 (then an idea in `docs/todo.md`); agreed with the
maintainer on 2026-11-19; built on `claude/permissions` the same day. How it
works for whoever maintains it: `docs/maintenance.md`, "Roles and
Permissions".

## Why

Today a person holds whole roles only -- `admin`, `superadmin`, `treasurer`,
`forum_moderator` (`permissions.py`, `ROLE_PERMISSIONS`) -- and, in a team,
lead or treasurer (`services/teams.py`, `TeamPermission`). Which permissions
somebody actually has is shown nowhere. The association restricts little
("We trust a lot of people with a lot of stuff or don't give them access at
all"), but some tasks want splitting, and other associations running the
portal may need it more.

Cases the maintainer named:

- **Writing legal texts, and publishing them**, separately (already two
  permissions: `legal.texts`, `legal.publish`).
- **Seeing the finances without changing anything** -- for the financial
  auditors from the university, who check the balances of all bank accounts
  at the end of the year; or a treasurer who only looks.
- **Answering contact messages and nothing else** -- somebody given the
  support messages without other admin rights ("if they need something, then
  they just tell the real admins").
- **A team lead who also manages cards** -- possible, not needed now.

The association's board: chairperson, vice chairperson, treasurer, vice
treasurer, secretary. No rules yet on who may do what in the portal; only
the treasurers have the bank account, and the chairperson signs contracts.

## Decided (2026-11-19)

1. **Live roles with exceptions.** A person holds roles plus exceptions --
   permissions added, permissions taken away. A permission added to a role
   later (as `legal.publish` was) reaches everyone holding it ("this is the
   right way to go"). Not a role copied into the person.
2. **Built-in, generic roles only**, as now -- no roles of one's own made in
   the portal ("Secretary"). A role is a named group of permissions; the
   exceptions do the rest.
3. **A team's leads adjust permissions within their own team** -- only team
   permissions they hold themselves ("they are actually managing the team and
   should basically be able to manage almost everything regarding the team").
4. From 2026-11-04, still standing:
   - On a person's *Access* tab every permission is listed, grouped by area;
     ticking a role ticks its permissions, and each can be unticked or added
     -- a normal member given one admin permission, say.
   - One catalogue, two scopes: a team permission granted in one team, or in
     every team (what `teams.money` and `teams.manage` do today). Lead and
     treasurer are a team's presets, with exceptions per person per team; an
     ordinary team member can be given a team permission.
   - **Safeguards:** nobody grants what they do not hold; the lockout guard
     stays (`PROTECTED_PERMISSIONS`); a permission brings what it needs
     (`admin.access` with any admin permission); every change in the log; the
     forum follows the permissions (its staff flags, as now).
   - **Granularity:** split where somebody would decide separately, not per
     button -- perhaps 50-60 permissions, not 200.

5. **Super admin is full trust, not a template** (agreed 2026-11-19, the
   maintainer: unticking "manage cards" for a super admin "wouldn't really
   make much sense, because as a super admin I can basically do everything
   with the server"). A super admin holds every permission, those added to
   the portal later too -- "all" in the code, not a list --, and takes no
   exceptions: whoever can install updates, download a backup and grant
   permissions could undo any restriction. Their *Access* tab shows all
   ticked and greyed, with one line saying why. Somebody trusted with less is
   an admin, or holds single permissions, with exceptions. Only a super admin
   makes somebody one; the last cannot go (`PROTECTED_PERMISSIONS`). "Nobody
   grants what they do not hold" needs no exception then.
7. **Granting is a permission of its own, *Grant permissions*, in the admin
   role** (2026-11-19, option B; Stripe's access-management role works so).
   Whoever holds it grants site-wide permissions and roles within what they
   hold themselves -- never super admin, which only a super admin grants;
   so not the credentials, updates or backups either. Every change logged.
   The maintainer's picture: "I, as the IT administrator of the association,
   am basically the only super admin. Maybe there is a backup account
   somewhere, but the main point of the super admin is just having one
   person who manages the server and the technical stuff, and then all of
   the other administrative things like adding users, granting permissions,
   the day-to-day work basically is handled by the actual board members."
6. **The split below and the *Auditor* role: agreed** (2026-11-19). Seeing
   is cheap to give, changing stays narrow: "there is not much that I would
   hide from anybody ... they can always ask how much money their
   association holds, and we are more or less obliged to tell them anyway".
8. **An account holding more is out of reach** (built 2026-11-19, following
   from 5 and 7): whoever grants changes only somebody who holds no more than
   they do -- their access, their address, their switch, their erasure. The
   same in a team: a member given *Change members' permissions* adjusts the
   others, not a lead beyond their own. Otherwise an admin could change a
   super admin's address, reset the password and hold the account.

## How other platforms do it (researched 2026-11-19)

- **The top role is unrestricted, not a template.** Microsoft Entra: Global
  Administrators "essentially have unrestricted access". Google Workspace:
  super admin sits above every custom role. AWS: the root user cannot be
  restricted by policies. So super admin as full trust, above.
- **Only the top role grants the top role.** Stripe: only a Super
  Administrator assigns that role; its separate access-management role
  cannot make anyone Administrator or Super Administrator. Google: a custom
  admin role can never change an administrator's account.
- **Few of them, but more than one.** Entra: fewer than five Global
  Administrators, and two emergency accounts. Google: more than three super
  admins can affect recovery. A warning on the *Access* page about it was
  turned down (`docs/todo.md`, "Turned down"): one super admin for now, more
  later, and the command line can always put it right.
- **Not for everyday work, and with a second factor.** AWS: the root user
  not for everyday tasks, MFA required. Google: the super admin apart from
  daily admin work. The portal has no second factor yet -- an idea for later
  (`docs/todo.md`, "Ideas for later"), not part of this.
- Not taken over: just-in-time elevation (Entra PIM) -- for organisations of
  thousands, not an association.

## The split

Where today one permission covers seeing and changing, and the cases above
want them apart:

- **Team money:** `teams.money` → seeing every team's figures, payouts and
  bank details / recording transfers and changing bank details.
- **Credit:** `credit.manage` → seeing balances and history / booking cash,
  corrections, refunds.
- **Admin › Money** (Stripe's book, the ledger): seeing / acting on it.
- **Contact messages:** `messages.receive` today means both being emailed
  and answering in Admin › Messages; perhaps answering alone, without the
  emails.
- **Accounts:** `accounts.view` today also opens much of an account's page;
  seeing / changing a member's details, checked page by page.

A role **"Auditor"** -- what the maintainer had in mind for the
university's auditors (finances read-only: team money, credit, Stripe's book;
nothing changed, no members' details beyond what a booking names) would be
generic -- Austrian associations must have two auditors (VerG § 5 (5)) --
and fit the university's financial auditors.

## Where it is built

- `permissions.py`: the catalogue with the split, `NEEDS` (a permission
  brings what it needs), the roles as templates (*Auditor* new; *Grant
  permissions* in the admin role; super admin = `ALL_PERMISSIONS`),
  `effective_permissions`, each permission's area and label.
- Tables `user_permissions` and `team_member_permissions` (migration
  `d8a3f1c6b2e4`): exceptions, added or taken away. Nothing to migrate: a
  role held before means the same now.
- `services/access.py`: the change and its refusals (`describe_role_change`,
  `covers`), who holds a permission (`holders`). `services/teams.py`: a
  member's team permissions (`own_team_permissions`,
  `set_team_member_permissions`, `TEAM_NEEDS`).
- Pages: a person's *Access* tab (Admin › Accounts, was *Roles*); a team
  member's page, *Permissions*, for whoever holds *Change members'
  permissions* (the leads). Whoever may only see the money or the credit
  gets the pages without the forms that change them.
- The split as built: team money and Stripe's book (Admin › Money) seen with
  `teams.money.view`, changed with `teams.money`; credit and sales seen with
  `credit.view`, booked and priced with `credit.manage`; contact messages
  answered with `messages.answer`, emailed with `messages.receive`. Member
  accounts were already split (`accounts.view`, `.billing`, `.privacy`) and
  stay so.
- **Left for the maintainer:** the privacy policy, § 38, names roles --
  "persons the association has given the respective permission" covers both.
  A legal text, changed in the portal (Admin › Legal texts).
