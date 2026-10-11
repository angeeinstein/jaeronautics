# Codex's review comments on earlier pull requests

On 2026-10-11 the maintainer asked for Codex's automatic review comments on
pull requests #2 to #30 to be checked against today's code (44 comments), and
for those that are still real to be fixed -- each checked first for whether it
is necessary, for side effects, and for clashes with the others ("only if you
are really sure that this is something that's first of all necessary and a
real problem in our current code"). Branch `claude/review-fixes`.

Each point: what Codex said (pull request, comment number in the triage),
whether it still holds, and what was done.

## Payments and memberships

1. **A late webhook for an old subscription overwrites the current one** (#2/0).
   Held: after a rejoin, Stripe's "deleted" for the old subscription (Stripe
   ends it after failed payments) marked the member failed and inactive.
   Done: an event about a subscription other than the one held is checked --
   the one Stripe made later is the current one (`is_earlier_subscription`).
   Events about an earlier one are left (a payment on it is still recorded,
   with its own subscription); a later one is followed as before. When Stripe
   cannot be asked, the event fails and Stripe delivers it again.
2. **A lost dispute about an older payment cancels the current subscription** (#13/13).
   Held. Done: the disputed invoice's subscription (from the year it paid
   for, else Stripe) is the one revoked and cancelled; the current one only
   when it is that one or it cannot be told. Works with 1: Stripe's
   "deleted" for the old one is then left alone.
3. **After a lost dispute the member cannot rejoin** (#13/14).
   Held: the code says "coming back is a decision to rejoin", but
   "dispute_lost" was not among the rejoinable states. Done: it is; rejoining
   is a new subscription, paid again. (Say if that should rather go through
   an admin.)
4. **"Cannot tell" taken for "the membership" in the subscription lookup** (#8/10).
   Held: when Stripe could not say what a running subscription was for, the
   lookup before a rejoin (and the admin's refresh) took it for the
   membership -- a team's fee would have been written onto the member. Done:
   both lookups raise instead ("try again later"); ended subscriptions are
   not asked about.
5. **Asynchronous credit top-ups release their reservation too early** (#24/27).
   Not done: it cannot happen with what is offered. Top-ups are paid by card
   or EPS, and Stripe confirms both at once; SEPA, which arrives later, was
   left out on purpose (`docs/credit-plan.md`). Worth doing if a delayed
   method is ever added.
6. **A top-up arriving after the account was erased is credited to it** (#24/28).
   Held: a Checkout page open in a tab stays payable for a day. Done: the
   erasure closes an open top-up page, and a top-up paid on one anyway is
   refunded at once (admins told if Stripe refuses).
7. **Cash credit at erasure: no payout details for the admins** (#24/29).
   Held: the notice carried only the account's number, and the erasure
   blanks everything that would say whom to pay -- while the privacy policy
   promises that cash credit is paid out. Done: the notice names the person
   and their address, taken before the erasure; it is not tied to the
   account, so it stays. (A privacy choice: say if it should rather be
   e.g. the person being told to contact the treasurer.)
8. **A team debit that bounced and was collected again reads as a difference** (#30/41).
   Held (reproduced in a test): team fees can be paid by SEPA, and the
   bounced first debit showed as "taken back in Stripe". Done: as for
   memberships, a charge taken back is no difference when another charge
   paid the same invoice.
9. **A request for work already under way is lost** (#30/43).
   Held, and likely: forum work starts at once, and Stripe sends a payment's
   events milliseconds apart, so the second change could fold into a sync
   already reading the old state and be marked done with it. Done: such a
   request marks the item (`asked_again`, a new column), and finishing puts
   it back in the queue instead; the lookup locks the row, so a run ending
   in the same moment cannot miss it.

## Accounts and mail

10. **A disabled administrator counts as another one** (#2/2).
    Held: erasing the last working administrator was allowed while a
    switched-off one existed. Done: the erasure uses the same count as role
    changes, which leaves switched-off accounts out.
11. **Queued news reaches somebody who unsubscribed meanwhile** (#28/35).
    Held: news goes out within the hour's and day's limits (100 and 300 by
    default), so it can take days. Done: whether they still want it is
    checked again as each one is sent; somebody who unsubscribed is left out
    and counted with those who had before.
12. **Unsubscribe links are not bound to the address** (#28/36).
    Held, in a way that matters: the link's page shows the account's address
    -- so a mail sent to an old address revealed the new one. Done: links
    carry the address they were sent to and stop working when it changes
    (the switch stays in My Account). Links in news already sent, which do
    not carry one, go on working.
13. **The contact form's limit is per network; its bot check can be skipped** (#28/38, 39).
    The limit held: five messages an hour was shared by everybody behind one
    address, a whole campus. Done: counted per account, or per network and
    typed address (the loose per-network limit stays). The bot check: left.
    The page is a React app, and a bot writing to the API directly can send
    any timing it likes; requiring the field would stop nothing.
14. **A new account's confirmation link breaks if Stripe fails during signup** (#2/3).
    Held, for somebody without a university address: the link's nonce is
    made when it is sent, and Stripe failing rolled it back. Done: saved
    before Stripe is asked.

## Installer

15. **A job can start on the new code before the jobs are paused** (#18/20).
16. **A failed update starts the jobs again on a half-installed checkout** (#18/21).
17. **Smaller:** a larger conntrack limit is lowered; uninstall leaves the
    conntrack files; `.env.example` has the old rate limits (#6/6, 7; #7/8).

## Not done, and why

Fixed since, or no longer applicable: the money overview comments (#15, #25:
rewritten in #30), the forum import comments (#5: the import has run), the
dropped team-rules columns (#19: migrated everywhere), Content Security Policy
and React's style attributes (#21: React sets styles through the DOM, which the
policy does not block; the end-to-end tests fail on any violation), the lost
dispute's revocation (#2/1: now by the disputed invoice). Theoretical or minor,
left: nginx files of another site with the portal's name (#22), concurrent
first notifications of a new kind and concurrent reconnects (#11), the admin's
preview of a published text (#17), presence rows and limits (#26), partial
delivery of contact messages (#28/37), the forum-cleanup edge (#18/22), a lead
removing their own role on the members page (#23), the rejoin months field
(#29/40), the per-item fee share (#30/42), historical refunds in "owed on"
(#15/15), payment links paid by SEPA (#8/9). The TDT rules' exclusion vote
(#16) is for the team to settle.
