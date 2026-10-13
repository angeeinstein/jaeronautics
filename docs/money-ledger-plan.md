# Stripe's books in the portal: fees, credit and the teams' share

**Status:** built (2026-10-11, `claude/money-check`); see "How it is built".
Running it: `docs/maintenance.md`, "Stripe's fees" and "The association's
money".

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

## Decided (2026-10-11)

- **The fee setting applies to all team money, past and future**, not per
  payment from the day it was set.
  - The maintainer: "apply it to all transactions that ever happened on this
    server and then going forward as well".
  - Nothing has been transferred to a team yet; the server has run for about
    a week.
  - `Payment.team_bears_fee` stops deciding it; the setting does.
- **Credit fee: oldest top-up first (FIFO)**, as proposed.
  - The maintainer would accept an average "as long as it is somehow
    transparent where the money numbers came from. So it shouldn't be just a
    random amount that is deducted from the team without anybody knowing how
    this happened."
  - So each sale shows which top-up paid it and its share of that fee.
- **How often:** "as often as possible and useful without overloading our
  server or Stripe API". A general rule now (`CLAUDE.md`, standing
  preferences). For these bookings:
  - fetched right after each Stripe webhook (several webhooks in a row make
    one fetch);
  - an hourly run as a safety net;
  - the invoice check over the last 40 days every night.
- **Emails about differences:** to the admins and the treasurer, with a
  switch to turn them off.

- **Every figure shows how it came about** (the maintainer, 2026-10-11,
  extending idea 6):
  - What Stripe keeps -- a fee on a refunded payment, a top-up fee on credit
    refunded unused, a chargeback fee -- always appears as its own line in
    the calculation, never folded silently into a total.
  - A team's **earned** and **open** amounts each get a detailed breakdown
    ending in the total:
    - payments, with Stripe's fee where the team carries it;
    - refunds and chargebacks;
    - credit sales, with their share of the top-up fee and which top-up
      paid them;
    - transfers.

    "So that it's really easy to understand how this number came into
    existence."
  - Transfers themselves stay plain: the treasurer may transfer any amount,
    which counts against the open amount. The breakdown belongs to the
    amounts, not to a transfer.
- **No share kept of team credit sales** (the maintainer, 2026-10-11, after
  testing): the "Kept of what teams sell for credit" percentage goes. It
  stood in for the top-up fees, which each sale now carries exactly, and
  "the association doesn't want to make money from the teams". A sale that
  kept a share before keeps it (`kept_cents`) and shows it as a line.
- **No preview** before changing the fee setting. Nobody but the maintainer
  has used the money pages yet. On the live server so far there are only
  association and team signups, and leads' texts, logos and pictures, so
  nothing to protect retroactively.

- **Not now** (2026-10-11):
  - matching Stripe's transfers to the bank: none has happened yet in over a
    year, everything is still in Stripe;
  - an export for the yearly accounts: "could be useful at some time" (ideas
    for later in `docs/todo.md`);
  - splitting *Other*: it stays "Other", no explaining sentence.

## How it is built

- **The copy:** `services/stripe_books.py`, table `stripe_bookings` (one row
  per balance transaction; no names), `stripe_books_state` (how far fetched,
  Stripe's balance then). Fetched after webhooks through the outbox (item
  `stripe_books`, started at once), hourly by the external-work worker, and
  on *Fetch from Stripe now*. A booking is sorted by the portal's own record
  (payment or invoice), then by what Stripe says (`stripe_scope`); one not
  told yet is tried again for 14 days after it was fetched.
- **Fees:** `Payment.fee_cents` is filled from the booking; the per-payment
  `team_bears_fee` is gone -- the setting decides for all money.
- **Credit:** `services/credit_fees.py` works out, from the credit's entries,
  which top-up paid for what (oldest first) and each part's fee share. Not
  stored: the entries and fees never change, so the result never does.
- **The statement:** `services/team_money.py`, `statement()`: every line and
  the sums; `summary()` and the pages read it. The open amount is the sum of
  the lines.
- **The check:** `services/money_checks.py` after every fetch, on the copy;
  `money_differences` keeps what differs; emailed once after an hour (switch
  in the money settings); resolved ones forgotten after a year. A SEPA debit
  is booked by Stripe when it is announced, but recorded by the portal only
  once it has cleared: such a booking is marked `pending`, asked about again
  on each fetch, and no difference while pending or when the debit failed
  (added 2026-10-13, after a false "missing in the portal" on the live site).
- **The overview:** `services/money_overview.py` reads the copy: totals per
  product for any period, the balance on a day, of it the teams' and the
  members' credit, the association's own.
- **Privacy:** the copy holds payment references and amounts, no names; a
  difference names who paid (an email address), cleared when the account is
  erased. The privacy policy draft says so (§ 20, § 40).
