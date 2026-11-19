# Permissions per person, roles as templates

Talked about on 2026-11-04 (then an idea in `docs/todo.md`); agreed with the
maintainer on 2026-11-19. Not built yet.

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

5. **Super admin is full trust, not a template** (proposed 2026-11-19, the
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
6. **The split below and the *Auditor* role: agreed** (2026-11-19). Seeing
   is cheap to give, changing stays narrow: "there is not much that I would
   hide from anybody ... they can always ask how much money their
   association holds, and we are more or less obliged to tell them anyway".

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
  admins can affect recovery. Proposed here: the *Access* page says so when
  there is only one super admin (a lost password then locks the portal's
  top role away -- today only the server's command line helps), and when
  there are more than four.
- **Not for everyday work, and with a second factor.** AWS: the root user
  not for everyday tasks, MFA required. Google: the super admin apart from
  daily admin work. The portal has no second factor yet -- an item of its
  own (`docs/todo.md`), not part of this one.
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

## Where it would be built

- `permissions.py`: the catalogue (with the split above), the roles as
  templates, each permission's area and label for the page.
- Tables: a person's exceptions (permission, added or taken away), and per
  team the same for team permissions; migrations from today's role rows.
- `current_user.can(...)`: roles' permissions, plus added, minus taken away.
- Pages: a person's *Access* tab (Admin › Accounts); a team's *Members* page
  for its leads; the audit log.
- The privacy policy, § 38: it names roles -- "persons the association has
  given the respective permission" covers both.
