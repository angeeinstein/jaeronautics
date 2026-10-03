# Teams: Where the Discussion Stands

A record of the discussion about the association's teams, so that nothing said
in conversation is lost before the work starts. **Nothing here is built yet.**

**None of this is fixed.** These are notes from an early discussion with a lot
still to think about and talk through, not a specification. Any point may change
once the teams have been asked, once something is tried, or simply because a
better idea comes up. Whoever picks this up — a person or an assistant — should
treat it as a starting point to discuss again, not as a list of requirements to
carry out because it is written down here. When in doubt, ask.

Each point is marked as **agreed so far** (the current thinking in the
discussion), **suggested** (raised but not really discussed) or **open**.

Discussed October 2026, after the membership portal went live.

## What it is for

The association has teams with their own membership and their own fee. People
join them on top of their association membership. Team leads look after their
members, and the university needs a list of current team members to give them
access to the office.

## Principles

- **Modular** — *agreed so far.* Teams are a feature that can be switched off, and are
  off by default. When off there are no team pages, no buttons on the account
  page, no jobs and no team groups on the forum: the portal works exactly as it
  does without them. Teams live in their own module, pages and tables; the
  membership logic gets only a few well-defined hook points ("the membership
  ended").
- **Configured in the portal, not in code** — *agreed so far.* Teams are created and
  set up by site admins, so different teams can work differently, and the
  software can serve another association.
- **Called "Teams"** — *agreed so far.* A fixed term, not configurable.
- **Stripe keeps them apart** — *agreed so far.* Each team has its own Stripe product,
  and its Checkout sessions and subscriptions carry `purpose: team:<name>` in
  their metadata. With teams switched off, the portal treats those as foreign
  and ignores them; `services/stripe_scope.py` already does this for anything
  not marked as the membership.

## Configuring a team (site admins)

| Setting | Options |
|---|---|
| Name, description | free text |
| Admission | **open** (join directly) or **by approval** (see below) |
| Payment | **recurring**, **one-time per period**, or **none** |
| Periods | a start date and a length — e.g. 1 October, 6 months |
| Stripe price | when a payment option is chosen |
| Forum group | optional group name |
| Applications | open or closed; optionally a maximum team size |
| Leads | one or more people |
| Office access list | recipients, dates |

## Joining

- **Requires an active association membership** — *agreed so far.*
- **Open teams:** a member clicks Join, pays, and is in.
- **Teams by approval** — *agreed so far.* Register, get invited to a meeting (online
  or in person), be approved or not, and only then pay. The states:
  1. *Application received* — the member can withdraw it.
  2. *Invited* — the leads enter the meeting details, which the applicant gets
     by email.
  3. *Approved* — an email with "Pay now".
  4. *Not accepted* — shown neutrally, with no reason.
  5. *Active*, later *Ended*.
- **In practice the teams are short of members** — *agreed so far.* Applying
  mostly means telling the leads one wants to join; nearly everybody gets
  invited and approved. So the application text is optional, and the invite
  step can be skipped.
- **A standing invitation** — *not now.* A text per team ("we meet every
  Tuesday at 18:00 in room …") that would move applicants straight to
  *Invited*. The application meetings are personal chats with a lead, so this
  is not needed for now; it stays a possibility.
- **No restrictions on applying again** after a rejection — *agreed so far.*
- **Every member type may join** — *agreed so far:* students, alumni, staff,
  company members and honorary members alike; a team does not mind who is in
  it. Leads who want a say choose "by approval", and can remove anybody. Known
  side effect: the access list's "University email" shows whatever work
  address somebody gave, so a company address for a company member.
- **Several teams at once** — *agreed so far.*
- **Applicants see their status in the Teams area** (see *Where it lives*) —
  *agreed so far*, with the states above.
- **A checkbox at association signup** — *agreed so far* in principle. For a team by
  approval it creates the application; for an open team, the team payment
  follows straight after the membership payment, on the thank-you page. It is a
  second Checkout, because the two fees are billed on different dates and so
  must be two subscriptions — which is also what lets someone leave a team and
  stay in the association.
- **No manual adding by leads** — *agreed so far.* The point of the portal is that
  teams no longer handle cash. Consequence: someone with neither a card nor a
  SEPA account cannot join a team through the portal.
- **An approval that is never paid** — *open.* Should it lapse, e.g. after 14
  days?

## Payment

- **Recurring** — the first team: €10 every 6 months, periods from 1 October
  (*agreed so far*). At signup the full fee is charged once for the current period,
  and the subscription starts with a "trial" up to the next period start, from
  where Stripe charges every 6 months. The same pattern the membership uses.
- **No rule for late joiners** — *agreed so far.* Whoever joins mid-period pays the
  full fee for it; teams ask people to join at the start of the semester.
- **One-time per period** — pay once for a semester or a year, without
  automatic renewal.
- **None** — free.
- **The second team** — *open.* What it needs, and whether it pays per semester,
  per year or once, is not known yet. The three payment options are meant to
  cover whatever it decides without new code.

## When a team membership ends

- **Not paid, recurring:** it ends when the payment finally fails.
- **One-time payment, period over** — *agreed so far.* On the last day of the period
  the team membership becomes inactive: off the access list, out of the forum
  group, onto the former members list. The leads are told — *suggested:* one
  summary per team ("these 7 ended on 30 September"), not an email per person,
  since every period ends on the same day.
- **An email to the member when it ends** — *suggested.* One short email with a
  renewal link, no reminder beforehand, so that nobody first finds out at a
  locked office door.
- **Leaving on one's own** — *suggested:* at the end of the paid period, no
  refund.
- **The association membership ends** — follows from the requirement: the team
  membership ends with it and the leads are told.
- **Removal by a lead** — *agreed so far:* **immediately**. It will only
  happen for misconduct, or for somebody who has vanished and cannot be reached.
  With a reason that goes into the record (not sent to the person), and no
  refund.
- **Rejoining after not paying** — *agreed so far.* Whoever's team membership ended
  because they did not pay may rejoin by simply paying, but only within a set
  time — about one period, not two years later. After that, or after being
  removed by a lead, it is a new application. If the leads do not want somebody
  back, they can remove them again.

## Former members

- **Visible to the leads** — *agreed so far*, in their own list: name, from–to, and
  why it ended (did not renew, left, removed, association membership ended).
- **Contact details only while the person still has a portal account**;
  whoever erases their account disappears from the list as from everywhere
  else — *suggested.*
- **How long they stay** — *agreed so far:* for good. Who was in the team in
  which year is worth knowing long after, for questions about that time. The
  list shows each person once, with every period they were in the team and
  when they were last active, most recently active first; whoever is back in
  the team is not on it.
- **The leads' notes when someone erases their account** — *agreed so far:* not
  deleted automatically, since they may hold something that needs keeping for
  longer. How they are handled instead — reviewed by an admin, kept for a set
  time, or anonymised — is *open*. Note that a person who asks for erasure has a
  right to it unless there is a reason to keep the data, so "kept forever" is
  not an option either.

## Team leads

- **Appointed by site admins** — *agreed so far.* "Lead" belongs to one team, not to
  the whole portal: a lead sees nothing outside their team. A team can have
  several leads, and a person can lead several teams. This is deliberately not
  one of the global roles in `permissions.py`, which apply portal-wide.
- **Must be active association members** — *agreed so far.* When their membership is
  not active, their access to the team page pauses; the assignment stays, and
  comes back with the payment. The 21-day renewal grace applies as for anybody.
- **A lead is a team member with a role** — *agreed so far.* The teams are small
  (one has about ten members) and short of money, so leads are ordinary team
  members who pay like everybody else and carry a role on top. A role only
  counts while its holder is an active team member. The first lead of a new
  team joins like anybody, and a site admin approves them.
- **More roles later** — *agreed so far*, not built now: a treasurer, say, with
  slightly different permissions. The database keeps roles as text and allows
  several per person, so adding one changes no table.
- **The last lead** — *suggested.* Never removed automatically, only paused.
  Site admins can open every team page at any time, so a team without an active
  lead is never unmanageable. When a team has no active lead, the site admins
  are notified and the team page says so. Removing the last lead by hand asks
  for confirmation. The office access list keeps going without a lead.

### The team page

Like the admin member pages, limited to the lead's own team — *agreed so far*:

- **Members:** list, search, detail — name, university email, private email,
  phone, cohort, status, paid until (*agreed so far*). No address, no payment
  details.
- **Applications:** queue, internal notes, invite with meeting details,
  approve, reject.
- **Remove from a team** (see *Removal by a lead* above).
- **Export** the current members.
- **Their own team settings:** description, applications open or closed, the
  meeting text, the access list recipients. Price, payment model and leads stay
  with the site admins.

Not on it — *agreed so far*: forum resync and other technical functions, billing
details, and anything about the association membership itself.

## Where it lives for members

- **A Teams area of its own, separate from My Account** — *agreed so far*, so
  that the association membership and the teams are not mixed up on one page.
  It shows the teams one can join or apply to, and one's own team memberships
  with their status.
- **A page per team for all its members** — *agreed so far* as a place to grow
  into: `/teams/<name>` for every active member of that team, beside the
  leads' `/teams/<name>/manage`. To begin with, the description, who the leads
  are, one's own status, and **the whole team** — name, picture and university
  email of every active member (*agreed so far*: the teams already share these
  among themselves on SharePoint and Microsoft Teams). Later perhaps a prepaid
  balance for the drinks terminal, documents, dates. It should be built so that
  sections can be added without reworking the page.

## The foundation: data model

*Suggested*, as discussed in October 2026. Built without regard to payment, and
kept open where another association might need something different: values that
could take more states later are text, not yes/no.

**`teams`** — one row per team: `slug` (unique; for URLs, the Stripe marker and
the forum group), name, description, `status` (`active`, `archived`),
`admission_mode` (`open`, `approval`), applications open, the application
question, the standing invitation, an optional maximum size, an optional forum
group, and `payment_mode` (`none` for now).

**`team_roles`** — who holds which role in which team: team, person, `role` as
text (`lead` now, others later), when and by whom it was given. Several roles
per person are possible. What each role may do is one table in the code, like
the global roles in `permissions.py`, so a new role needs no database change.

**`team_memberships`** — one row per attempt, not per person: team, person,
`status` as text (`applied`, `invited`, `approved`, `active`, `ended`,
`rejected`, `withdrawn`), when each step happened, the application text, the
meeting details, who decided, and why it ended. Whoever applies again gets a new
row, so the history stays and the former members list follows from the ended
rows. At most one ongoing attempt per person and team, kept under row locks.

**`team_membership_notes`** — the leads' internal notes: author, text, time.
Seen by the team's leads and by site admins, never by the person.

**Settings** — teams on or off (off by default), and the label ("Team" /
"Teams").

**Access** — a lead's permissions count only for their own team, and only while
they are an active member of the association and of the team. Site admins reach
every team through a new global permission to manage teams.

**Built so far.** Step one: the switch and the label, the four tables, teams
and their roles in the admin (`/admin/teams`), and the access rules, in
`services/teams.py`. Step two, all only while teams are switched on:

- the Teams area (`/teams`): join an open team, apply to one by approval (with
  the team's question, if it has one), withdraw, leave;
- the team's page (`/teams/<name>`) for its members: everybody's picture, name
  and university email, and who leads it;
- the leads' page (`/teams/<name>/manage`): applications, members, former
  members, the team's own settings, and an export of the current members as
  CSV; per person, their details, history and the leads' notes, and invite
  (with the meeting details), approve, not accept, remove (with a reason that
  stays in the record and is not sent to the person);
- emails: to the person when invited, approved, not accepted or removed; to the
  leads when somebody applies, joins, leaves, or leaves with their association
  membership. A team with no lead in force sends those to the site admins;
- the nightly membership job ends team memberships and applications of people
  no longer in the association, and tells the leads in one message per team;
- erasing an account ends its teams, removes its roles and blanks what it wrote
  in applications; the leads' notes stay, as agreed.

Then, also built:

- **an optional logo** per team (*agreed so far*), uploaded by site admins or
  the team's leads, stored as a PNG the portal makes from the upload. It shows
  on the team's card for people choosing a team, on its pages, and in its
  emails below the association's own header, which stays, so the emails
  clearly come from the association;
- **the payment step** (*agreed so far*): every approval, and joining an open
  team, passes through it, even for a free team, so the flow is the same with
  or without payment and payment can be added without changing it. A free team
  settles it on the spot -- recorded, with no email of its own; a team that
  charges would stay *approved* there until paid.

**One identity.** The association account is the person: one login, and the
name, picture and addresses from the association profile. A team membership is
only added to that account and never asks for any of it again -- much like
signing in everywhere with one account.

Not yet: payment itself.

**The flow** — open team: join → active. Team by approval: applied → invited →
approved → active, with rejected or withdrawn possible on the way. Without
payment, approval leads straight to active; with payment, *approved* is where
the member pays. Every step goes into the audit log.

## The office access list

*Built*, as discussed:

- **Content** — name and university email of every current member, as a table
  in the email, by surname. Both are in the portal already; no new fields.
- **Configured per team**, by site admins or the team's leads: who receives it
  (one address or several), on which days of the year (`15.10, 15.03`), and
  whether it goes out by itself on those days — off until switched on.
- **The leads are copied in, visibly**, as they would be if they wrote it
  themselves. The team's logo, if it has one, sits below the association's
  header, as in the other team emails.
- **On demand:** the leads' page shows who receives it, when it goes out next
  and when it last did; "Preview and send" shows the email as it would go out,
  with a warning for members without a university email, and sends it.
- **By itself:** the nightly membership job sends the lists due that day, after
  ending the memberships of people who left the association, so the list is
  current — never twice on one day. A list that cannot be sent is reported to
  the admins and can then be sent from the team's page.
- **Compared with our last list** — *agreed so far*, deliberately simple, since
  the list is a help for whoever gives access and not the only source of truth:
  people get access in other ways too. The main list says who should have
  access now, with a NEW label for anybody not on our last list; below it,
  small and grey, who was on the last list and is no longer in the team; and
  one line naming the date compared with. Compared by account, so a changed
  name or address is not mistaken for somebody new. Only the last list sent
  is kept; somebody who erased their account shows up once as no longer in
  the team, and is gone after the next list.
- **No notice when joining** — *agreed so far:* that a team's members are listed
  for access to its rooms is obvious, and the university email goes back to the
  university. People here are open about who is in which team.

## Forum

One group per team, optional, kept in step like `members` — *built.* A team
names its group; its active members are in it and everybody else is not, sent
with every forum sync in both directions. Becoming or stopping being a member
-- joining, approval, leaving, removal, leaving the association, an archived
team -- queues a sync. Nothing about team groups is sent while teams are off.
The group has to exist on the forum. Renaming it later leaves people in the
old one, which then has to be deleted on the forum. Team categories, if wanted,
are made in Discourse and granted to that group by hand.

## For the overhaul of the bylaws

The bylaws are being rewritten anyway. What teams need in them:

- team fees per semester, collected separately from the membership fee — today
  they say all fees are added up and collected once a year;
- team membership requires association membership;
- the leads' access to their team members' data;
- the insurance clause: team members are insured by the association "if they do
  not have sufficient cover, which must be stated explicitly". *Left out for
  now:* the board first decides whether the association can provide that
  insurance at all, or whether the clause is a leftover.

## Later ideas

- **Paying for drinks by NFC.** A terminal where members tap their university
  card and pay, e.g., €1 per drink — replacing a tally sheet, so it does not
  need to be more secure than one: everybody using it is an association member.
  The logic exists from a separate project, a coffee payment system, and can be
  reused. Things to settle when it comes to it:
  - **How it is charged.** That system works with a **prepaid balance**, and
    each tap is taken off it. That suits Stripe, which could not charge €1 per
    tap anyway — a fixed fee of about €0.25 per payment, plus a minimum amount —
    but can take a top-up of the balance.
  - **A local action** on a tap, such as switching on a coffee machine, is
    possible with that system but not needed at first.
  - **Linking a card to a member:** each member registers their card's ID once
    in the portal.
  - **The terminal's connection** to the portal, and what happens while it is
    offline (store the taps, send them later).
  - **Who sees the consumption** — the member themselves certainly; anybody
    else is a data protection question.
  - **A limit** on how far a balance or tab may go.
- **A confirmation of team membership** as a PDF ("member of team X from 2025
  to 2027"), for CVs.
