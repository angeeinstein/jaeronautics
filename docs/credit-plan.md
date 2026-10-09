# Credit: a balance members top up and spend

**Status:** built 2026-10-08, prepared: nothing spends credit yet. Setting it
up and running it: `docs/maintenance.md`, "Credit". Code:
`services/credit.py`, `api/credit.py`, `frontend/src/pages/account/Credit.tsx`,
`frontend/src/pages/admin/credit/`, `frontend/src/pages/admin/settings/CreditSettings.tsx`.

## What the maintainer asked for

> "users can buy credits or have money loaded onto their account for things
> like beverages or coffee … I want to be able to switch on or off the entire
> feature … everyone [sees] in my account a card with my credit and a way to
> load up my credit … a complete history of what … I loaded money-wise and
> what I paid with … the connection to Stripe … I don't plan to implement any
> features with this yet. I just want to prepare everything."

## Decided (2026-10-08)

- **Only active members top up.** Somebody whose membership ended keeps seeing
  their credit and history.
- **Limits:** top-ups of at least 10 €; **at most 20 € on one account**, so that
  no large sums sit in the system ("so that I don't have to worry that maybe
  there are 10,000 euros parked"). Buttons for 10, 15 and 20 €, plus "Other
  amount". All configurable under Admin › Settings › Credit.
- **Cash top-ups** booked by the treasurer: built.
- **The balance in the top bar's person menu:** not built. The overview's tile
  and the side menu entry are enough, and the top bar stays minimal. Easy to
  add later if wanted.
- **Leftover credit at account deletion** is refunded automatically.
- **Tax:** not looked at for now, the maintainer's decision. The association
  has been tax-exempt for most of its activities. If the sums grow, ask a tax
  adviser.

## How it works

### The switch

Admin › Settings › Credit holds:

- on or off;
- the Stripe product;
- the smallest top-up, and the most on one account;
- the suggested amounts;
- EPS beside cards.

Off: members see nothing and cannot top up. Balances and history stay,
Admin › Credit stays while anybody still has some, and a top-up already paid
is still added.

### Stripe

- **One product, "Credit".** Its price does not matter; Checkout is opened
  with `price_data` on that product and the amount the member picked in the
  portal. Every top-up shows under that product in Stripe's reports.
- **Ruled out:**
  - a price per amount: a price for every amount, and no free amount;
  - Stripe's "customer chooses the price": the amount is typed on Stripe's
    page rather than the portal's.
- **Marked `purpose: credit`** on the Checkout session, payment and invoice;
  routed by `services/payments.py`.
- **Receipts** from Stripe (`invoice_creation`), linked in the history.
- **Payment methods:** card (Apple Pay and Google Pay with it), and EPS when
  switched on. No SEPA direct debit: it arrives days later and can be taken
  back for weeks.
- **Money counts once Stripe says it is there.**
- **One top-up open at a time** per person, so the limit holds.

### The ledger

- **`credit_entries`, append-only.** Kinds:
  - `top_up`;
  - `cash_in` (cash handed over);
  - `cash_out` (paid out by hand);
  - `purchase`;
  - `refund`;
  - `reversal` (a lost chargeback);
  - `correction` (needs a reason, never below zero).
- **The balance** in `credit_accounts` changes under its row lock in the same
  transaction. System health reports a balance that is not the sum of its
  entries.
- **Purchases may name a team**, so a team's sales can later count towards
  what it is owed.
- **`credit.spend(user, cents, what, team=None)`** refuses when there is not
  enough. Nothing calls it yet.

### Refunds

- **Against the payment a top-up came from, newest first.** Stripe refuses
  more than is left of a payment and sends money nowhere but back to whoever
  paid it. So even a misbehaving portal could only give payers their own
  money back.
- **Booked as made.** The webhook reporting the refund afterwards books
  nothing more.
- **Refunds made in Stripe's dashboard, and lost chargebacks,** come off the
  credit through the webhook.
- **Cash, and payments too old for Stripe,** are paid out by hand and booked
  as *Paid out*.

### What people see

- **Members:**
  - a *Credit* tile on My Account's overview;
  - My Account › Credit: the balance counting up when a top-up arrives (not
    with reduced motion), topping up, the history with receipts, and a CSV
    download.
- **Admins and the treasurer (`credit.manage`):**
  - Admin › Credit: what is held, what came in and was spent in 30 days,
    every balance, the latest entries, and a CSV of everything (accounts by
    number);
  - per person: book cash in or out, a correction, or a refund.
- **An account's admin page** shows a *Credit* button when the person has
  some.
- **Deleting an account** says the credit will be refunded.

## Legal texts (drafts, waiting for approval)

- **Privacy policy 2026-10-08** (already a draft for the pictures): credit
  in § 20 and § 41.
- **Membership terms 2026-10-08:** new § 31 "Credit". It covers:
  - who may top up;
  - confirmation;
  - only with the association and its teams;
  - no transfers or interest;
  - no expiry;
  - refunds on request and at deletion;
  - chargebacks.

  The later sections move up by one.

## Kept simple on purpose

- **No sending credit to another member, and no paying out except a refund.**
  Spent only with the association and its teams, it stays a closed prepaid
  balance ("limited network"), not e-money.
- **One currency, EUR.**
