# Credit: a balance members top up and spend

**Status:** proposed, 2026-10-08. The maintainer asked for it. This note is
the proposal; the decisions still open are at the end. Nothing is built yet.

## What the maintainer asked for

> "users can buy credits or have money loaded onto their account for things
> like beverages or coffee … I want to be able to switch on or off the entire
> feature … everyone [sees] in my account a card with my credit and a way to
> load up my credit … a complete history of what … I loaded money-wise and
> what I paid with … the connection to Stripe … I don't plan to implement any
> features with this yet. I just want to prepare everything."

Nothing spends credit yet (no coffee machine, no shop). This step builds the
balance, topping it up, its history, and the switch. The way to spend it is
ready inside the code, but nothing calls it yet.

## Proposal

### The switch

- **Where:** Admin › Settings › Credit. It holds:
  - on or off;
  - the Stripe product;
  - the smallest and largest top-up;
  - the suggested amounts.
- **When off:**
  - the card, the page and the admin view are gone;
  - balances and history are kept;
  - a top-up that is already paid is still credited, because the money arrived.
- **When on again:** everything is back as it was.

### Stripe: one product, the amount set by the portal

- The admin creates one product in Stripe, "Credit", with **no price**, and
  pastes its id (`prod_…`) into the setting.
- The portal opens Checkout with `price_data` on that product and the amount
  the member picked on the portal's own page (suggested amounts, or any amount
  between the minimum and maximum).
- Every top-up shows under the one product in Stripe's reports.
- Ruled out:
  - **a fixed price per amount** (10, 20, 50 €): a price for every amount, and
    no free amount;
  - **Stripe's "customer chooses the price" price**: the amount is typed on
    Stripe's page instead of the portal's, with no suggested amounts in the
    portal.
- **Marker:** `purpose: credit` on the Checkout session and the payment, routed
  by `services/payments.py` (`register_purpose`), as the teams' fees are.
- **Receipt:** from Stripe (`invoice_creation`), as for a team's one-time fee.

### Payment methods

- Card, which includes Apple Pay and Google Pay, and **EPS**, the Austrian
  instant bank transfer.
- **No SEPA direct debit** for credit:
  - it takes days to arrive;
  - it can be taken back for 8 weeks, or 13 months if not authorised;
  - the coffee would already be drunk.
- Credit is added only when Stripe says the money is there:
  `checkout.session.completed` with `payment_status: paid`, or
  `async_payment_succeeded`.
- **Minimum top-up: 10 €.** A card payment costs roughly 1.5 % + 25 cents, which
  is 5 % of a 5 € top-up. The association absorbs the fee; members are not
  charged extra.

### The ledger

- **Table `credit_entries`, append-only.** Each entry has:
  - the person;
  - an amount in cents, positive or negative;
  - a kind;
  - a description;
  - the `Payment` row it came from, if any;
  - the team it was spent for, if any (so a team's sales can later count
    towards what it is owed, as fees do);
  - who booked it;
  - when;
  - the balance afterwards.
- **Kinds:**
  - `top_up`: from Stripe;
  - `cash`: money handed to the treasurer, booked by them;
  - `purchase`: spending, nothing yet;
  - `refund`: money given back in Stripe;
  - `reversal`: a payment taken back by a dispute;
  - `correction`: by an admin, with a reason.
- **Entries are never changed or deleted.** A mistake is put right by a
  correction entry.
- **Balance:** kept on the account as well and changed under a row lock in the
  same transaction as the entry. Checked against the sum of the entries; a
  mismatch is a system-health warning.
- **Below zero:** possible only through a refund or a dispute after the money
  was spent. Shown in red; spending needs the balance back above zero.
- **The spending call:** `credit.spend(user, cents, what, team=None, by=None)`
  refuses when there is not enough. Not used by any page yet.

### What members see

- **My Account › Overview:** a card titled "Credit" with:
  - the balance, large;
  - a "Top up" button;
  - the last 3 entries;
  - a link to "Credit" in the side menu.
- **My Account › Credit** (new page):
  - the balance;
  - top-up: chips for the suggested amounts plus "Other amount", leading to
    Stripe Checkout;
  - the full history: date, what, amount (+ green, − plain), balance after;
  - the Stripe receipt linked on top-ups;
  - a CSV download of the history.
- **Back from Stripe:** "Payment received, adding it…", then the balance counts
  up once the webhook has arrived (live, through polling). No animation when the
  device asks for less motion.
- **Top bar:** optionally the balance in the person menu.

### What admins and the association treasurer see

Permission: `teams.money` (admins and the association treasurer) or a new
`credit.manage`; to be decided.

- **Admin › Money › Credit:**
  - the total of all balances (what the association owes its members);
  - top-ups this month;
  - each person's balance;
  - every entry, filterable;
  - CSV export.
- **On a person's admin page:**
  - their balance and history;
  - "Book cash top-up";
  - "Correction" with a required reason.
  - Both are audited.

### Refunds and leaving

- **A refund in Stripe** (`charge.refunded`) becomes a `refund` entry.
- **A dispute lost** becomes a `reversal`.
- **Deleting an account that still holds credit:**
  - the deletion page shows the balance;
  - the remaining credit is refunded automatically to the top-ups it came
    from, newest first, through Stripe;
  - only what Stripe can no longer refund (after about 180 days for cards) is
    left for the treasurer to transfer by hand.
- **Credit does not expire.** Austrian courts have struck down short expiry
  dates on prepaid vouchers.
- The ledger is kept as bookkeeping after an account is erased, like
  `Payment`, without the person's name.

### Kept simple on purpose

- **No sending credit to another member, and no paying out except a refund.**
  Spent only with the association and its teams, the credit stays a closed
  prepaid balance ("limited network"). It is not e-money, which would need a
  licence.
- **One currency, EUR.**

## Legal texts

- **Terms:** a short section on credit:
  - what it can be spent on;
  - no interest;
  - no expiry;
  - refund on leaving or on request;
  - no transfers.
- **Privacy policy:** the top-ups and purchases recorded and why (bookkeeping
  duty, 7 years).
- **Tax:** VAT on prepaid credit is due when it is spent, not when it is
  topped up (a multi-purpose voucher). Worth asking the association's tax
  adviser before spending is built; the ledger keeps what is needed.

## Open questions for the maintainer

1. **Who may top up:** only members with a current membership, or every
   account?
2. **Suggested amounts and limits:** 10 / 20 / 50 €, and a maximum of 100 € per
   top-up? A maximum balance?
3. **Cash top-ups** booked by the treasurer: wanted?
4. **The balance in the top bar's person menu:** wanted?
5. **Leftover credit at deletion:** refunded automatically (proposed), or the
   member chooses between a refund and a donation?
