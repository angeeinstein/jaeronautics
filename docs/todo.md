# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The last batch (2026-10-08) built everything that was on the list; what it
was is in its pull request and in git history (`git log -- docs/todo.md`).
One thing waits for the maintainer; the rest came from testing that batch.

## Open

### Privacy policy: publish the version that names where pictures are shown

- **Added:** 2026-10-08
- **Where:** `legal/privacy-policy/{de,en}/2026-10-08.md`, `status: "draft"`.
- **Why:** the portal shows profile pictures since that batch (to the person,
  their teams' members and leads, and admins; the old forum's picture for
  reconnected members); the policy said so only for the forum. The draft
  adds it to § 22 and "profile picture" to the list in § 27.
- **Waiting for:** the maintainer reading the wording (shown under Admin ›
  Legal texts, *Not in force yet*). Once approved: `status: "published"` in
  both files (the day stays, or the day it is published), nothing else.
- **Open:** whether the board wants to see it first.

### Team cards: a tall logo must not run into the team's name

- **Added:** 2026-10-08
- **Where:** Teams overview cards -- `.logo` and `.logo img` in
  `frontend/src/pages/teams/Overview.module.css` (also check `.logoTile` in
  `About.module.css` and `TeamMark.module.css`).
- **Why:** the maintainer's screenshot of the test server: "Team Drone Tech"
  has a portrait logo (taller than wide); it runs down out of its place over
  the card's title, so the name reads "Te… Drone Tech". The Ravens' square
  logo is fine. The same logo already stuck out a little in the earlier
  screenshot, inside its square.
- **What:** every logo fits whole in its 4.5rem square, whatever its shape --
  width and height 100% with `object-fit: contain` (the image's `max-height:
  100%` does not hold inside a grid cell without a fixed track). Checked with
  a tall, a wide and a square logo in the screenshot checker's sample data.
- **Open:** none.

### Team cards: a long description and a short one look uneven

- **Added:** 2026-10-08
- **Where:** Teams overview cards (`.line` in
  `frontend/src/pages/teams/Overview.module.css`, two lines at most); the
  team's short description (`PageOut.description`, up to 500 characters,
  asked for as "one or two sentences").
- **Why:** the maintainer, with the screenshot: the Ravens wrote two or
  three sentences, cut off after two lines with "…"; Drone Tech wrote two
  words ("Building Drones"). "There is a difference between those two
  teams" -- the sentence was not finished.
- **What:** to be decided -- see the open question.
- **Open:** what bothers you most: (a) the Ravens' text cut off with "…"
  (show it whole, or allow three lines); (b) the cards not looking alike
  (facts at the same height, the fee on one line); or (c) that leads may
  write a long text where a short line is meant (a lower limit, say 160
  characters, with a counter, and the longer text belongs on the About
  page)? My suggestion: (c) and (b), and the card shows the whole line.

### Team fee: the three fields do not line up

- **Added:** 2026-10-08
- **Where:** a team's Admin › Fee page -- `FeeFieldsEditor` in
  `frontend/src/pages/admin/teams/TeamFields.tsx` (the `SimpleGrid` of
  Payment, Stripe price ID, Periods start on).
- **Why:** the maintainer's screenshot: "Payment" has no help text and the
  other two have two lines each, so the Payment box sits higher than the
  other two.
- **What:** the help text under each field instead of between label and
  field (Mantine `inputWrapperOrder`), so the three boxes line up; the same
  in the team Details form (`DetailsFields`), which has the same mix.
- **Open:** none.

### Team roles: from the member list, and a compact Roles page

- **Added:** 2026-10-08
- **Where:** a team's Members list (`Members` in
  `frontend/src/pages/teams/manage/People.tsx`) and its Roles page
  (`RolesBody`, `LeadsPanel`, `LeadPicker` in
  `frontend/src/pages/teams/manage/Settings.tsx`); the endpoints exist
  (`POST|DELETE /api/v1/teams/<slug>/manage/leads`, `.../treasurer`).
- **Why:** the maintainer, about the Roles page built in the last batch:
  "not have those big cards for leads and treasurer … it would be nice to be
  able to just go in the members list and then have a three dot menu …
  on each member where I can just do a few basic actions like make them a
  team lead or make them a treasurer … still … keep the roles menu in the
  sidebar where we then show a overview, but you can also appoint roles …
  with a different look … the cards are so big and there's text around that
  the actual name of the team lead just vanishes."
- **What, as proposed:**
  - **Members list:** a ⋯ menu at the end of each row, for whoever may
    appoint: *Make lead* / *No longer lead*, *Make treasurer* / *No longer
    treasurer* (the last lead still with a second click). Roles shown as
    pills beside the name (Lead exists; Treasurer added).
  - **Roles page:** one compact table instead of two cards -- a row per
    role holder: picture and name large, the role, whether it is in force,
    a remove button; under it one line "Appoint [person ▾] as [lead ▾] [Add]".
    The explanations shrink to one short line under the heading. Admins
    keep the search across the association in the same picker.
- **Open:** none, unless the maintainer wants it otherwise when he sees it.

## Ideas for later (not scheduled)

Talked about, not agreed as work. Not built unless the maintainer brings them up.

- **A credit balance (coffee and more)** -- 2026-10-07. Members could top up a
  credit, see it in the menu at the top right, and spend it on the
  association's or a team's things (coffee first). My Account's side menu was
  chosen partly so a page like this can be added.
- **The association's Odoo website** -- 2026-10-07. People confuse it with the
  portal and try to sign in there. No decision.
- **Rounded corners** -- 2026-10-07. Discussed what switching the square look
  to rounded corners would cost; nothing decided.
- **The admin entry** -- the gear at the top right was kept; a shield with an
  "Admin" label and a count of reviews waiting was suggested as an
  alternative.

## Decided against

- **A short code for staff and company members like the year group**
  (2026-10-08). The year group feeds forum usernames (`_L25`), the forum's
  cohort groups and team lists; a made-up code would make fake cohorts and
  break the scheme (three letters, two digits), and a kind of member can change
  while a username should not. If it comes up again: the forum's groups per
  kind can carry a flair and a title instead, and a company member's company
  name could be their forum title -- offered, not wanted for now.
