# Credit: a balance members top up and spend

**Status:** built 2026-10-08; selling (price lists, sales booked by hand,
team accounting) and who carries Stripe's fees built 2026-10-09. Card
readers designed below, not built. Setting it up and running it:
`docs/maintenance.md`, "Credit". Code:
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

## Selling (built 2026-10-09: the base, not the payment step)

What the maintainer asked for (2026-10-09):

> "the association itself can sell too … a coffee in the main student
> area … just build the implementation where the money goes and how it is
> accounted for … keep open how the payment step itself works … I also want
> to be able to set prices for different things … different prices for the
> teams or for different items."

**Price lists.**
- **The association's:** Admin › Credit, kept by admins and the treasurer.
- **Each team's:** the team's *Prices* page, kept by its leads and team
  treasurer (team permission `team.edit_prices`). The page shows while
  credit is on.
- **An item** has a name and a price. It can be renamed, repriced, moved or
  switched off, but never deleted, since sales still name it.
- **A new price** counts from the next sale.

**A sale** (`services/credit_sales.py`, `sell`):
- The item's price comes off the member's credit, and the entry names the
  item, the seller (a team, or none for the association) and how it was
  made (`channel`; today only `booked`).
- It is refused when the credit does not cover it, the item is switched
  off, the team is archived, or credit is off.
- **Taking a sale back** gives the credit back and stops it counting for the
  seller. It works once per sale; the second time is refused by the
  database.
- **Today:** admins and the treasurer book a sale by hand on the person's
  credit page and take it back from its row.

**Where the money goes.**
- **A team's sales** count towards what the team is owed, like its fees.
  They appear on its Money page (by item) and are transferred with the next
  payout.
- **The association's sales** stay with it. Admin › Credit › *Sold* shows
  each seller's last 30 days and total.

## Who carries Stripe's fees (built 2026-10-09)

The board had not decided ("they were not fully convinced that … the
association would pay for the Stripe fees"), so it is a setting on Admin ›
Money, kept by the treasurer and admins:

- **Team fees.** Either the association pays (the default, as before) or
  the fee is "taken from the team". In the second case each team fee paid
  from then on counts for the team less Stripe's actual fee for that
  payment.
  - The fee is asked from Stripe overnight (`fill_in_fees` in the nightly
    jobs).
  - Until it is known, the payment shows as "waiting for Stripe's fee" and
    does not count yet.
  - Stripe keeps its fee on a refund too.
- **Credit.** A top-up's fee is spread over whatever it buys, possibly from
  several sellers, so there is no one fee per sale. Instead the association
  may keep a share of what teams sell, 0–20 % (default 0). The share is
  stored on each sale.
- **Changing either setting** counts from then on. Each payment
  (`team_bears_fee`) and each sale (`kept_cents`) keeps how it was when it
  was made. No transfer to a team had been made yet when this was built.

## Card readers (designed, not built -- "not something we need to build now")

The maintainer's idea: an NFC reader on an ESP32 beside the beer fridge (or
the coffee machine).
- A member taps their student card. The reader asks the portal whether the
  credit covers the item.
- One beep and green if it does: take your beer. Two beeps and red if not.
- Honesty-based, replacing the coin bowl or the tally sheet on paper.
- Later perhaps an action on success, such as switching on the coffee
  machine.
- A reader with a display shows the prices and lets the member choose
  (beer or Red Bull).

How it fits what is built:

- **Readers.** A table of readers, each with:
  - a name;
  - its seller (a team or the association);
  - optionally a fixed item (a coffee machine sells one thing);
  - a secret key, shown once when the reader is added and stored only as a
    hash;
  - when it was last seen.

  Added and switched off under Admin › Credit, or a team's Prices page for
  its own reader.
- **Cards.** A member links their card once:
  - either on their Credit page (tap it on any reader within a minute of
    pressing "Link a card");
  - or the treasurer types the number the reader shows.

  The portal keeps a hash of the card's ID, never the ID itself. A card can
  be unlinked; a member may have several.
- **The reader's API** (`/api/v1/readers/…`, the key as a bearer token, over
  HTTPS):
  - `GET items`: the seller's price list, for a display. Cached on the
    reader.
  - `POST sale {card, item}`: answers `ok` (with what is left) or `refused`
    (unknown card, not enough credit, item off, credit off). The ESP32 beeps
    and lights accordingly.

  Behind it is `credit_sales.sell(..., channel="reader")`, the same sale as
  today's booked one.
- **Safety.**
  - Each reader's key only sells its seller's items, at most one sale every
    few seconds per card (a double tap is one beer).
  - Small amounts only: the most a person holds is 20 €.
  - A lost card is unlinked by its owner or the treasurer.
  - Sales from a reader can be taken back like any other.
- **Offline.** The reader refuses while the portal cannot be reached. No
  sales are queued on the device, so nothing is sold twice or without
  credit.
- **Open questions:**
  - Whether student cards' IDs are readable and stable (the cards' NFC
    chip type).
  - Whether members should see their linked cards.
  - Whether a coffee machine's relay needs a separate "dispense" step after
    the sale.

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
