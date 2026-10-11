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
2. **A lost dispute about an older payment cancels the current subscription** (#13/13).
3. **After a lost dispute the member cannot rejoin** (#13/14).
4. **"Cannot tell" taken for "the membership" in the subscription lookup** (#8/10).
5. **Asynchronous credit top-ups release their reservation too early** (#24/27).
6. **A top-up arriving after the account was erased is credited to it** (#24/28).
7. **Cash credit at erasure: no payout details for the admins** (#24/29).
8. **A team debit that bounced and was collected again reads as a difference** (#30/41).
9. **A request for work already under way is lost** (#30/43).

## Accounts and mail

10. **A disabled administrator counts as another one** (#2/2).
11. **Queued news reaches somebody who unsubscribed meanwhile** (#28/35).
12. **Unsubscribe links are not bound to the address** (#28/36).
13. **The contact form's limit is per network; its bot check can be skipped** (#28/38, 39).
14. **A new account's confirmation link breaks if Stripe fails during signup** (#2/3).

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
