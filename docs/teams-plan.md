# Teams: What Has Been Decided

A record of the discussion about the association's teams, so that nothing
settled in conversation is lost before the work starts. **Nothing here is built
yet.** Each point is marked as **decided**, **proposed** (suggested, not yet
confirmed) or **open**.

Discussed October 2026, after the membership portal went live.

## What it is for

The association has teams with their own membership and their own fee. People
join them on top of their association membership. Team leads look after their
members, and the university needs a list of current team members to give them
access to the office.

## Principles

- **Modular** — *decided.* Teams are a feature that can be switched off, and are
  off by default. When off there are no team pages, no buttons on the account
  page, no jobs and no team groups on the forum: the portal works exactly as it
  does without them. Teams live in their own module, pages and tables; the
  membership logic gets only a few well-defined hook points ("the membership
  ended").
- **Configured in the portal, not in code** — *decided.* Teams are created and
  set up by site admins, so different teams can work differently, and the
  software can serve another association.
- **Called "Teams"** — *decided.* A fixed term, not configurable.
- **Stripe keeps them apart** — *decided.* Each team has its own Stripe product,
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

- **Requires an active association membership** — *decided.*
- **Open teams:** a member clicks Join, pays, and is in.
- **Teams by approval** — *decided.* Register, get invited to a meeting (online
  or in person), be approved or not, and only then pay. The states:
  1. *Application received* — the member can withdraw it.
  2. *Invited* — the leads enter the meeting details, which the applicant gets
     by email.
  3. *Approved* — an email with "Pay now".
  4. *Not accepted* — shown neutrally, with no reason.
  5. *Active*, later *Ended*.
- **Applicants see their status on the account page** — *decided*, with the
  states above.
- **A checkbox at association signup** — *decided* in principle. For a team by
  approval it creates the application; for an open team, the team payment
  follows straight after the membership payment, on the thank-you page. It is a
  second Checkout, because the two fees are billed on different dates and so
  must be two subscriptions — which is also what lets someone leave a team and
  stay in the association.
- **No manual adding by leads** — *decided.* The point of the portal is that
  teams no longer handle cash. Consequence: someone with neither a card nor a
  SEPA account cannot join a team through the portal.
- **An approval that is never paid** — *open.* Should it lapse, e.g. after 14
  days?

## Payment

- **Recurring** — the first team: €10 every 6 months, periods from 1 October
  (*decided*). At signup the full fee is charged once for the current period,
  and the subscription starts with a "trial" up to the next period start, from
  where Stripe charges every 6 months. The same pattern the membership uses.
- **No rule for late joiners** — *decided.* Whoever joins mid-period pays the
  full fee for it; teams ask people to join at the start of the semester.
- **One-time per period** — pay once for a semester or a year, without
  automatic renewal.
- **None** — free.
- **The second team** — *open.* What it needs, and whether it pays per semester,
  per year or once, is not known yet. The three payment options are meant to
  cover whatever it decides without new code.

## When a team membership ends

- **Not paid, recurring:** it ends when the payment finally fails.
- **One-time payment, period over** — *decided.* On the last day of the period
  the team membership becomes inactive: off the access list, out of the forum
  group, onto the former members list. The leads are told — *proposed:* one
  summary per team ("these 7 ended on 30 September"), not an email per person,
  since every period ends on the same day.
- **An email to the member when it ends** — *proposed.* One short email with a
  renewal link, no reminder beforehand, so that nobody first finds out at a
  locked office door.
- **Leaving on one's own** — *proposed:* at the end of the paid period, no
  refund.
- **The association membership ends** — follows from the requirement: the team
  membership ends with it and the leads are told.
- **Removal by a lead** — *open*, leaning towards **immediately**: it will only
  happen for misconduct, or for somebody who has vanished and cannot be reached.
  With a reason that goes into the audit log, and no refund.
- **Rejoining after not paying** — *decided.* Whoever's team membership ended
  because they did not pay may rejoin by simply paying, but only within a set
  time — about one period, not two years later. After that, or after being
  removed by a lead, it is a new application. If the leads do not want somebody
  back, they can remove them again.

## Former members

- **Visible to the leads** — *decided*, in their own list: name, from–to, and
  why it ended (did not renew, left, removed, association membership ended).
- **Contact details only while the person still has a portal account**;
  whoever erases their account disappears from the list as from everywhere
  else — *proposed.*
- **How long they stay** — *open.* Data protection expects a limit; a few years,
  then removed automatically, is the likely answer.

## Team leads

- **Appointed by site admins** — *decided.* "Lead" belongs to one team, not to
  the whole portal: a lead sees nothing outside their team. A team can have
  several leads, and a person can lead several teams. This is deliberately not
  one of the global roles in `permissions.py`, which apply portal-wide.
- **Must be active association members** — *decided.* When their membership is
  not active, their access to the team page pauses; the assignment stays, and
  comes back with the payment. The 21-day renewal grace applies as for anybody.
- **Lead and team member are separate** — *proposed.* Lead is a function and
  needs only the association membership. A lead who also works in the team is a
  team member too and pays like everybody else. Exempting leads from the fee is
  a site admin's decision, never a lead's own.
- **The last lead** — *proposed.* Never removed automatically, only paused.
  Site admins can open every team page at any time, so a team without an active
  lead is never unmanageable. When a team has no active lead, the site admins
  are notified and the team page says so. Removing the last lead by hand asks
  for confirmation. The office access list keeps going without a lead.

### The team page

Like the admin member pages, limited to the lead's own team — *decided*:

- **Members:** list, search, detail (name, contact, study programme and cohort,
  status, paid until).
- **Applications:** queue, internal notes, invite with meeting details,
  approve, reject.
- **Remove from a team** (see *Removal by a lead* above).
- **Export** the current members.
- **Their own team settings:** description, applications open or closed, the
  meeting text, the access list recipients. Price, payment model and leads stay
  with the site admins.

Not on it — *decided*: forum resync and other technical functions, billing
details, and anything about the association membership itself.

## The office access list

- **Content** — *decided:* name and university email. Both are in the portal
  already; no new fields.
- **On demand:** a preview, then "send now" to the stored recipients, the leads
  in copy.
- **Automatically:** on dates set per team, the list of current team members is
  emailed to the contact at the university.
- **Telling the members** — *decided:* one sentence when joining: "Your name and
  university email are passed to FH JOANNEUM for access to the office." That is
  both the information they are owed and the basis for passing it on.

## Forum

One group per team, optional, kept in step like `members` — *decided.* Nothing
more for now. Team categories, if wanted, are made in Discourse and granted to
that group by hand.

## For the overhaul of the bylaws

The bylaws are being rewritten anyway. What teams need in them:

- team fees per semester, collected separately from the membership fee — today
  they say all fees are added up and collected once a year;
- passing name and university email to the university for office access;
- team membership requires association membership;
- the leads' access to their team members' data;
- the insurance clause: team members are insured by the association "if they do
  not have sufficient cover, which must be stated explicitly". Whether the
  portal should ask that when someone joins a team is *open*.

## Later ideas

- **Paying for drinks by NFC.** A terminal where members tap their university
  card and pay, e.g., €1 per drink — replacing a tally sheet, so it does not
  need to be more secure than one. The tap logic exists from a separate project
  and can be reused. Things to settle when it comes to it:
  - **How it is charged.** €1 cannot be charged per tap: Stripe takes a fixed
    fee of about €0.25 per payment, plus a minimum amount. So either a
    prepaid balance topped up through Stripe, or a tab collected in one payment
    per month or semester.
  - **Linking a card to a member:** each member registers their card's ID once
    in the portal.
  - **The terminal's connection** to the portal, and what happens while it is
    offline (store the taps, send them later).
  - **Who sees the consumption** — the member themselves certainly; anybody
    else is a data protection question.
  - **A limit** on how far a balance or tab may go.
- **A confirmation of team membership** as a PDF ("member of team X from 2025
  to 2027"), for CVs.
