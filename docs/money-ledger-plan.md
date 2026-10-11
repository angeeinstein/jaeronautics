# Stripe's books in the portal: fees, credit and the teams' share

**Status:** in discussion (2026-10-11). Nothing built. To-do item: "Money:
Stripe's books kept in the portal" in `docs/todo.md`.

## What the maintainer wants

- **Keep what is fetched from Stripe**, not fetch everything on every check
  and throw it away again: "load the data, check if our existing data is
  correct and maybe correct it, and then go on with this data and calculate
  things". Later changes (a refund, a dispute) are corrected as they come.
- **The check runs on its own**: every night over the recent invoices, or
  even every hour over the last day or two.
- **Stripe's fees come off what a team is owed** when the team carries them.
  - The maintainer saw that switching "who pays Stripe's fee" did not change
    the open amount. Today that is by design: the setting counts only for
    payments made after it was set (`services/money_settings.py`).
  - A fee not known yet is held back as "waiting for Stripe's fee" until the
    nightly `fill_in_fees`.
- **Credit in the same accounts.** What a team sold for credit is owed to the
  team, and the fee once paid on the top-up should be accounted for.
- "I think we could really build a good system here that accounts for all
  of the fees and all of the Stripe transactions that are related to our
  membership site."

## How it is today

- **Admin › Money, *Check against Stripe*** (`services/money_overview.py`):
  - reads every invoice, every invoice payment and every balance transaction
    from Stripe's first booking;
  - sorts them into membership, team and other;
  - checks paid invoices against the portal's records;
  - keeps nothing; it takes longer as the history grows.
- **Team payments** (`Payment`, purpose `team`):
  - each keeps `team_bears_fee` from the day it was paid;
  - `fee_cents` is fetched nightly, one payment at a time.
- **Credit:**
  - top-ups are `Payment` rows with purpose `credit`, with no fee recorded;
  - the overview counts them under *Other*, and nothing checks them;
  - a team's credit sale is owed to the team in full, less an optional share
    the association keeps ("Kept of what teams sell for credit", at most 20 %).
    That share stands in for the top-up fees.

## Proposal (to discuss)

**1. A copy of Stripe's books in the database** -- one row per balance
transaction, the bookings Stripe itself sums to the balance.
- **What each row holds:**
  - id, type, amount, fee, net, the day it was booked;
  - the charge, payment intent and invoice it belongs to;
  - what it was for: membership, team, credit or other;
  - the portal's payment it belongs to, if any.
- **What it does not hold:** names or addresses. Webshop and event bookings
  are amounts only.
- **Stripe never changes a booking.** A refund, a bounced debit or a
  chargeback is a new booking pointing at the payment it takes back. So the
  copy only grows: each run fetches what is new since the last one, with a
  few days' overlap. "Corrected retroactively" means adding the new booking,
  not rewriting old ones.
- **First run:** everything from Stripe's first booking. Bookings before the
  portal took over are marked as the old site's.
- **The balance check becomes cheap:** the copy's sum must equal what Stripe
  says it holds now, which is one request.

**2. Runs on its own.**
- Hourly, or with each Stripe webhook: new bookings, and the balance check.
- Nightly: the invoice-by-invoice check over the last 40 days, since an
  invoice's status can still change (SEPA, retries).
- A difference is emailed once, and counts as resolved itself when it goes
  away.
- Admin › Money reads the copy: totals for any period at once, no waiting.
  The button becomes "Fetch from Stripe now".

**3. Fees per payment from the copy.** Each team payment's fee is its
booking's fee, so `fill_in_fees` goes.
- **Refunds:** Stripe keeps its fee, so a refunded payment can cost the team.
- **Chargeback fees:** charged by Stripe on a lost dispute; they go to
  whoever carries the payment's fee.

**4. Credit and the top-up fee.** Each top-up's fee is known from the copy.
How it reaches a team's sale -- options:
- **a. Oldest top-up first (FIFO).**
  - Each sale is paid from the buyer's oldest credit still unspent, and takes
    that top-up's fee in proportion: €2 from a €20 top-up with a €0.55 fee
    carries €0.06.
  - Credit handed over in cash carries no fee.
  - Kept with the sale, so it never changes afterwards.
  - Exact and traceable.
- **b. An average rate:** all top-up fees ÷ all top-ups, applied to each
  sale. Simpler; not exact per person.
- **c. As today:** a fixed share kept by the association.

Either a or b could replace the "kept" percentage, or sit beside it.

**5. The fee setting over time.** Options:
- **keep "from the moment it is set"**, and say on the page which payments
  carry their fee;
- **or give it a start day** ("teams carry fees from 1 November"), which
  makes a change deliberate and visible;
- **or apply it to everything not yet transferred.** Simplest to read, but a
  team's past earnings would change after the fact.

## Open

- Hourly, or with each webhook, for the new bookings? Nightly or weekly for
  the invoice check?
- Credit fee: FIFO, an average, or the fixed share? Keep the share?
- The fee setting: from now on, from a start day, or for all money not yet
  transferred?
- Who gets the emails about differences?
- The privacy policy: the copy holds payment references without names (the
  portal already keeps those for its own payments). Probably a line in § 20.
