# Cards and sales points: paying with a card at a reader

Agreed and built 2026-10-13 (the first version), branch `claude/card-sales`.
It builds on the devices (`docs/devices-plan.md`, merged in #36) and on
credit and its price lists (`docs/credit-plan.md`). Setting it up and the
reader's API: `docs/maintenance.md`, "Cards and sales points". Code:
`services/cards.py`, `services/sales_points.py`, `api/cards.py`,
`frontend/src/pages/account/Cards.tsx`, `frontend/src/pages/admin/Cards.tsx`,
`frontend/src/pages/admin/account/CardsTab.tsx`.

## What the maintainer wants

The team agreed that the maintainer builds two uses of the card readers:
paying for beverages with credit, and attendance tracking (the flight team's
members earn ECTS and must be there for a number of hours). Beverages come
first; attendance is needed around January ("not something that we need to
build now immediately").

The students' cards are read by their ID (UID), which stays the same on every
tap -- tested by the maintainer: FH Joanneum's cards are MIFARE DESFire EV3
8k, TU Graz's are DESFire EV1 4k.

The maintainer built a coffee payment system about a year ago (never used:
the coffee machine's payment connector did not work). Taken from it as
inspiration, not as a model: cards with names that their owners can remove,
linking a card by scanning it with Web NFC (it worked with both cards), a
button per drink pressed before the tap, and an output per drink. Not taken
over: the device sending the amount, card IDs typed in by anyone, balances
and histories readable without signing in, a retried request charging twice.

## Decided

### Cards

"Similar to a normal authentication system with access cards, where the card
ID basically is its own database entry and is only linked to a user, but the
card ID itself is not the user." The same cards serve every later use (the
tool cabinet, "a sort of mild access control ... we also don't need this to
be super secure", attendance); what a card may do is decided by each device
type, not by the card.

- **A card** belongs to one person; a person may have several. It has a name
  ("Student card", "Key fob"), a state, when and by whom it was linked, and
  when and on which reader it was last used.
- **Its ID** is kept as a keyed hash (HMAC with a server secret: the ID is
  short and its first byte is the same on every NXP card, so a plain hash
  could be reversed by trying every ID), and its last few characters, to
  tell cards apart (`…A1F3`). The ID is written the same however it was read:
  no separators, upper case (a phone gives `04:a1:…`, a reader `04A1…`).
- **Two ways to switch a card off:**
  - *disabled* by its owner, who can switch it on again ("can't find it",
    "found it");
  - *blocked* by an admin; only an admin lifts it. Its owner sees that it is
    blocked.
- **Removing** a card hides it; what was paid with it still says so ("paid
  with Key fob"). Its ID is free to be linked again.
- **Who manages cards:** the owner (name, disable, enable, remove) and admins
  only, with a permission of their own, `cards.manage` (block, unblock,
  remove, link for someone). The treasurer can be given it later if needed.
- **Where:**
  - members: My Account › Cards -- its own page, not under Credit, since
    cards serve more than credit;
  - admins: a Cards section on each account's admin page, and a list of all
    cards.

### Linking a card (the first version, removed 2026-10-14)

*Removed once linking with a reader's QR code worked (below): it is the one
way now. The maintainer: "Since the card linking via the QR code works so
perfectly ... I don't want to keep features that are not really necessary
and may only confuse people", and "I don't think there is a single student
who doesn't have a smartphone". Web NFC worked only in Chrome on Android;
the admin's ways were a stand-in for whoever could not use it. Quickly
brought back from git history if a reader without a screen ever needs them.
Kept as the record of the start.*

"Enough for the first tests"; more ways later.

1. **By its owner, with Web NFC** on My Account › Cards (*Scan card*; Android
   with Chrome, shown only where the browser has it).
2. **By an admin, in person:** scanning it with their own phone the same way,
   or starting "the next tap on reader X" on the person's account page: the
   next unknown card tapped there within 60 seconds is linked to them. For
   iPhones and anyone without NFC.

There is no linking mode a member can start from the portal alone: anybody
with an account, anywhere, could start it, and a stranger's first tap would
end up in their account without the card's owner knowing or being able to
undo it (the maintainer's concern).

A card already linked to someone is refused ("this card belongs to another
account -- ask an admin"). A card scanned with Web NFC is sent by the browser
and so could be made up; accepted, since a card linked wrongly charges the
person who linked it.

### Linking a card at a reader with a QR code (agreed and built 2026-10-14)

The member links their own card at a reader with a screen, with any phone
(iPhones too), "proven by being there":

1. A card nobody has linked is tapped. The reader reports it as always
   (`card`), so the portal has the card at this moment: it keeps it (as the
   keyed hash and its end, like every card) in a **link** with a random token,
   and answers `card_unknown` with that link's address (`link_url`). The
   reader shows "Not linked yet, scan to link it" with it as a QR code.
2. **The QR code holds the token, never the card's ID** -- the ID is all
   that proves who pays, and anyone who has it could copy it onto a card
   with a writable ID; on a screen, in a browser history or a server log it
   would be out. Discussed and decided against (2026-10-14).
3. The link lasts **10 minutes** and works once, so nobody is hurried on the
   phone (signing in, a forgotten password). **The first phone to open it
   keeps it**; another phone opening it is told it is already being used.
   (2 minutes was agreed first, while the reader waited for the end; with the
   reader no longer waiting, the time on the phone is free.)
4. The phone opens the portal (signing in first if needed): "Link the card
   …A1F3, tapped at Rocket fridge just now?", with a name for it, and
   *Link*. The card is linked from what the link kept: **the reader takes no
   part any more**. The phone shows "Linked, hi Alex"; the next tap at the
   reader opens the menu.
5. **The reader moves on at once.** While the QR code is shown -- at most
   **30 seconds** -- it asks the portal about the link **every 2 seconds**
   (the maintainer: "I don't want to wait ten seconds here ... two seconds
   seems right"); those requests are not listed on the device's page. The
   answer is `waiting` (keep the QR code), `opened` (a phone has it: show
   "Continue on your phone" for a few seconds, then the normal screen) or
   `expired`. The maintainer: "if somebody scans the code and then abandons
   this, I want the device to switch back to the normal default screen
   rather quickly so that other people could use the terminal". The portal
   sends the times with the link, so they change without a firmware update.

- **The same card tapped again** gets a new link and a new QR code, and every
  earlier link of that card stops working -- **also one a phone has already
  opened**. Whoever opened a link and lost it (closed the browser, the phone
  locked) taps the card again and starts over "like it never happened
  before" (the maintainer). Only one link per card is ever open, and only
  someone holding the card can start one.
- **A card linked meanwhile** another way (another QR code): the link is
  refused with "This card is already linked", and the phone says so.
- **Who may link:** any signed-in account, as with Web NFC then -- not only
  active members. "Just linking a card isn't really any problematic thing";
  paying needs credit, and topping up needs an active membership.
- **Why it answers the concern above** (a stranger's tap ending up in
  someone's account): the link only exists on the reader's screen, just after
  that card was tapped, so nobody can start it from afar; and the first phone
  keeps it, so a photograph of the code taken a moment later is of no use.
- **Firmware:** draws the QR code from the link's `url` (ESP-IDF's `qrcode`
  component) and asks about it while it is shown: `docs/device-protocol.md`,
  section 7.4.
- **Built** (`services/card_links.py`, the page
  `frontend/src/pages/public/LinkCard.tsx` at `/link/<token>`): the page opens
  the link before asking to sign in, so the reader moves on at once; the
  phone keeps its claim in the browser, so signing in comes back to it. A
  link's row is deleted the next day, and with the card's account at erasure;
  the privacy policy draft says so.

### The sales point (the first version, replaced by the menu)

*Replaced on 2026-10-14 by a menu of up to four items with icons, the first
name and the balance (docs/devices-plan.md, "The screens";
docs/device-protocol.md, section 8.1). Kept as the record of the start.*

A device type, "Sales point", with a **fixed item**: a tap sells that one
item. "For a starter, I would only begin with a fixed item per reader, and
later on we can expand this if this works and if this feels right."

- **Settings:** the seller (the association or a team) and the item, from
  that seller's price list. Only admins add and set up readers ("rather
  technical pieces"); the price lists stay with whoever keeps them now
  (admins and the treasurer for the association, a team's leads and team
  treasurer for the team).
- **The tap:** the device reports the card's ID. The answer is one of:
  - `sold`: the item, its price and what is left;
  - `refused`, with why: unknown card, card disabled, card blocked, not
    enough credit, item switched off, credit switched off.

  The firmware shows it (light, beeps); the portal decides the price.
- **A double tap is one sale:** one sale per card per reader every few
  seconds, and a report sent again (the same event id) gets the first answer.
- **When the portal cannot be reached** the reader refuses; nothing is queued.
- **The sale** is the one already built (`credit_sales.sell`), made by the
  reader (`channel`), naming the card. It counts for its seller and can be
  taken back like any other.

## Later (agreed as directions, not scheduled)

- **More kinds of reader**, all the same tap, the prices always from the
  portal so "nobody has to touch the device ever again if it's deployed once":
  - buttons: a button per item, set on the reader's page, pressed before the
    tap (as on the old coffee reader);
  - a display: built as the menu (2026-10-14) -- the card is tapped first,
    then one of up to four items chosen on the screen;
  - perhaps only part of a seller's list on a reader. How price lists and
    readers fit together beyond this is "a bit open".
- **Linking at a reader by its user**, proven by being there: a QR code with a
  short-lived token on a reader with a display, or pressing the reader's
  button within a few seconds of pressing *Link a card* in the portal.
- **Undoing your own sale** for a few minutes, on My Account (a double tap, or
  the favourite beverage was not in the fridge after all). Not at the reader.
- **Actions at the reader** beyond light and beeps: a relay (a coffee
  machine), a fridge's magnetic lock -- an output per item, sent with the
  answer. "Especially because I don't have a concrete use case for the
  readers right now."
- **Attendance**, around January, with the same cards.

## How it was built (decided while building)

- **The key the card IDs are hashed with** is derived from the server's
  `SECRET_KEY`, so a copy of the database alone does not give the IDs away
  and a backup (which carries `SECRET_KEY`) restores working cards. A check
  value kept at the first link lets System health say when `SECRET_KEY` has
  changed since, which would make every card unknown.
- **The items are the reader's only setting**, from its owner's price list
  only. An item switched off later stays chosen but leaves the menu.
- **A card tapped gets a ticket** for one purchase on that reader, for 20
  seconds; reports on one reader are taken one at a time (a row lock on the
  device), so two copies of one report are one sale. (The first version's
  "once in 5 seconds per card" went with the ticket.)
- **The device's page keeps a tap without the card's ID** (its last four
  characters only), and says what came of it: "…E5F6 opened the menu
  (€5.00)", "Beer sold, €1.50; €3.50 left". (asked for
  after testing, 2026-10-13). A report type says what of its data is kept
  and how its event reads.
- **A known card tapped while an admin waits at that reader buys as usual**;
  the wait goes on for the person's own, unknown card.
- **Members see My Account › Cards** once a sales point exists, or when they
  have a card. At most 10 cards per person.
- **Admins see every card** under Admin › Cards (People), and manage one on
  the owner's page, tab *Cards*.
- **The privacy policy draft** of 2026-10-08 (§ 20) names the cards; it
  waits for the board with the rest of that draft.
