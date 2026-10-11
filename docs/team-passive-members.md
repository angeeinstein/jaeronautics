# Passive team members, rejoining, and staying informed

**Status:** decided for now (2026-10-11): only the rejoin setting is built
(`docs/todo.md`). The rest of passive membership waits for the maintainer's
conversation with TDT's leads; the proposal and open points below are kept
for it.

## The request

Team Drone Tech (TDT) asked for **active and passive members**:
- passive members pay no team fee;
- they are still part of the team and hear its news, e.g. during a
  semester abroad;
- somebody who does not pay, or whose renewal fails, becomes passive for a
  semester.

The maintainer wants it **optional and as general as possible**: "if I use
this membership portal software for another association at some point, I'm
not sure if this feature wouldn't cause more confusion than help."

## What TDT's own rules say

`legal/teams/tdt/team-rules/de/2026-03-19.md`, § 3, § 4, § 5, § 11. The rules
do not say *why* there is a passive status; this is what follows from them:

1. **The place in the team is kept without a new admission.**
   - Joining needs the lead's approval, which may be refused without reasons
     (§ 4 iii).
   - A passive member becomes active again just by paying (§ 11 ii (iii)).
2. **Not paying is a grace period, not an exit.**
   - A missed deadline makes somebody passive for the semester (§ 11 iii).
   - Only after two semesters passive may the lead exclude them (§ 5 iv,
     § 11 iv).
3. **Communication continues**, through their WhatsApp community and channel
   (outside the portal).
4. **Passive means no active rights:** no room (SimLab card), no drones, no
   events, no votes (§ 6, § 11). Their votes are held in Microsoft Forms
   (§ 12).
5. **Manual procedures the rules assume:**
   - declaring oneself passive in a WhatsApp poll before the payment
     deadline;
   - after not paying, no return within the same semester (§ 11 iii (iii)).

## What the portal already does

- **A subscription whose payment fails:** Stripe retries for weeks, and the
  person sees "update payment method". Only when all retries failed (at the
  latest 35 days after the paid period) does the team membership end; the
  person and the leads are told.
- **One payment per period:** a reminder 14 days before the end. Unpaid at the
  end, the team membership ends (14 more days for a debit on its way).
- **Somebody not in the team:** not on the room access list, pays nothing.
  The leads see who was in the team and when, on "Former members".

## The maintainer's view

> "they gain rather little by having a member listed as passive compared to
> not being listed as an active member at all."

A failed payment in an automated system is rarely deliberate:
> "an expired payment method or similar"

So a penalty such as "no return this semester" makes little sense here.

> "they also spend a lot of time creating this rule set … So I don't want to
> tell them that they need to redo everything … but … if there are some
> points that just don't make sense for an automated system … it doesn't
> really help anyone if we just make weird workarounds."

## Decided (2026-10-11)

- **Team members are always association members.** It is a general rule:
  without the association membership there is no account. Already so today.
- **Rejoining without approval**, as a team setting, off by default:
  "Former members can rejoin without approval".
  - It has a time limit in **months** (12 for TDT), not semesters: teams
    without payment periods (free, or a Stripe subscription) have no
    semesters.
  - It counts from the day the team membership ended.
  - It needs an active association membership, as joining does.
  - It covers the semester abroad and a missed or failed payment: the team
    membership ends as today; the person comes back by paying, without
    applying.
  - The maintainer: "This is definitely something that feels right and that
    we could build."

## Keeping it general (2026-10-11)

The maintainer may offer the portal to other associations, e.g. Aerospace Team
Graz, whose "teams" are departments of one association (propulsion, avionics),
not separate teams with their own fees:
> "I don't want to add lots of very specific options that only our
> association and teams can use. And that would just confuse other people."

**Decided:**
- Only the rejoin setting (above) is built. Other associations can use it
  too: any team that approves applications may want people it already
  accepted back without a second review. It shows only for teams that
  approve applications.
- Not now: "become passive", a passive label, mailings to former members
  (the proposal below). The maintainer: "if they want to see people who just
  left, they can always look in the former members page and see the time
  when they left."
- TDT can still use the word: in its rules, "passive" can mean "left within
  the last 12 months, may rejoin without approval".
- The balance, in the maintainer's words: "I don't want to make any features
  or things we implement worse for our association just because of this
  slight possibility that other associations might use this. But I still
  want to keep this possibility in mind."

## Leading proposal (2026-10-11, the maintainer's idea) -- pending TDT

**"Become passive" on the leave page,** beside "Leave" (wording later), when
the team has the setting on.
- **Both choices** end the subscription at the end of the paid period, as
  leaving does today: nothing is paused at Stripe.
  - With one payment per period there is nothing to cancel. Not paying
    already ends the team membership with the period.
- **When the paid period ends:** the team membership ends, marked passive
  (no new active-like status).
- **While passive**, until the rejoin limit (N months):
  - shown on "Former members" as "Passive until <date>";
  - gets the team's mailings when the team includes passive members;
  - rejoins by paying, without applying;
  - not on the access list, no team role in force, no fee.
- **After the limit:** an ordinary former member -- no more team mailings, a
  normal application to come back.
- **Also possible:**
  - a lead sets somebody passive from their page (TDT's rules allow it);
  - the email saying a team membership ended (not paid, payment failed)
    offers "become passive" as a link -- the person's choice, not automatic.
- **This replaces "Keep me informed".** Becoming passive is the same thing
  plus the right to come back.

## Open -- to be discussed with TDT first

- **"Keep me informed":** still to be scoped, and its name too.
  - The idea: offered when somebody leaves, when a lead removes them, and in
    the email saying a team membership ended.
  - It is shown on "Former members".
  - A team mailing can include those people (association members only).
  - They can switch it off from the team page or a link in each team email.
- **Naming, and fitting TDT's word "passive":** a proposal for that
  conversation.
  - The rejoin window *is* a passive phase: somebody whose team membership
    ended within the last N months may come back without a new admission.
  - The team could name that phase itself (a label setting, e.g. "Passive"),
    shown on "Former members" with the date until which they can rejoin.
    Another association leaves the label empty.
  - TDT's rules would keep the concept and most of the wording. Only the
    manual parts would go: the WhatsApp poll before the deadline, and "no
    return within the same semester" after not paying.
- **A separate passive status in the portal** (listed among members, fee
  paused, a Stripe subscription paused) and **becoming passive automatically
  when a payment fails**: not wanted for now. The maintainer doubts they add
  anything over the above, and pausing subscriptions at Stripe is complex.
  Not built unless the conversation with TDT shows a need.
