# To do

What is agreed but not built yet. How this list works -- when it is worked
through, how an item is written -- is in `CLAUDE.md` at the repository root.

The next batch is due by **2026-10-15** (the oldest item's date plus 7 days),
or sooner by the rules there.

## Open

### Thank-you page: as good-looking as the rest

- **Added:** 2026-10-08
- **Where:** `/thank-you` (and `/cancel`, the same frame) --
  `frontend/src/pages/public/Payment.tsx`, `ThankYou` and `Cancel`.
- **Why:** the maintainer, after a test signup: "I don't quite like this
  thank you page. It could look a bit prettier." The page is a heading and one
  panel of text: "Thank you!", "Your membership is active now and free until
  31 December.", "Your payment method is saved. …", a "Go to My Account"
  button, a divider, a small paragraph about teams and a small grey "See the
  teams" button.
- **What, as discussed:**
  - a proper hero, in the style of the start page and the joining steps --
    for example a check that draws itself, as at the end of the joining
    mock-up (still when the device asks for less motion);
  - "Go to My Account" in the same style as the other main buttons; the
    maintainer: it does not "match the style of the rest of the page";
  - the teams part as a visible card with a clear button, not the faint
    "See the teams" ("easy to miss");
  - the texts stay as they are in meaning: they depend on `method` (checkout or
    invoice) and `phase` (`free_period` or `prorated`) in the address.
- **Open:** none.

### Updates page: offer an update only once its CI has passed

- **Added:** 2026-10-08
- **Where:** Admin › Settings › Updates --
  `aeronautics_members/services/system_update.py` (`get_remote_version`,
  `describe_update_state`), `aeronautics_members/api/admin_system.py`,
  `frontend/src/pages/admin/settings/Updates.tsx`.
- **Why:** after merging, the page offers the new version at once, but the
  update then waits for CI to build the front end (`wait_for_ci` in
  `install.sh`, up to 25 minutes). The maintainer: "I'd rather have a quick
  update in one process than starting an update and then having to wait for
  CI."
- **What, as agreed:** the page asks GitHub the same way the installer does
  (`ci_state` in `install.sh`: `GET
  https://api.github.com/repos/<owner>/<repo>/actions/runs?head_sha=<sha>&event=push`,
  the newest run of `.github/workflows/ci.yml`; unauthenticated, the
  repository is public -- 60 requests an hour, so cache the answer: a couple
  of minutes while CI runs, longer otherwise) and says one of:
  - **Update available** (with the button) -- CI passed;
  - **A new version is being checked** (no button) while CI runs, with about
    how long that usually takes; the page refreshes itself until it is ready;
  - **The newest version did not pass its checks** -- nothing offered;
  - GitHub out of reach: as today.
- **Open:** none.

### Read the whole new front end through for bugs

- **Added:** 2026-10-08
- **Why:** offered before merging the new front end (PR #21); it was merged
  first, so the read-through is owed.
- **What:** a careful reading of `frontend/src` and the API it uses, for
  correctness: state that can go stale, errors not shown, a page that breaks
  for an account in an unusual state (no membership, erased, disabled, a team
  without a lead), anything that only works on the sample data. Fixes go in
  the same batch; anything bigger becomes its own item here.

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
