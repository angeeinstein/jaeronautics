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

### Installer: no second nginx configuration for the portal

- **Added:** 2026-10-08
- **Where:** `install.sh` -- `render_nginx_config` and where `NGINX_CONF_PATH`
  is chosen (`sites-available/` when it and `sites-enabled/` exist, else
  `conf.d/`).
- **Why:** after the update to the new front end the live site looked broken
  (Mantine's default blue, rounded buttons; content under the top bar; both
  logos at once). Cause: an old `/etc/nginx/conf.d/jaeronautics.conf` from an
  earlier installation still set a fixed `Content-Security-Policy` with
  `style-src 'self'`. nginx reads `conf.d/` first, so that file served the
  domain, and every update wrote the real configuration to
  `sites-available/jaeronautics.conf`, where nginx ignored it. Two policies
  arrived, and the stricter one blocked the app's nonce'd `<style>` tags.
  Fixed on the live server by hand (the old file moved to
  `/root/jaeronautics.conf.old-nginx`).
- **What:** when writing the configuration, a file for the same service in the
  other place (`conf.d/<service>.conf` when writing to `sites-available/`, and
  the reverse) is moved into the update's backup folder, with a warning in the
  update's output; then `nginx -t` before the reload.
- **Open:** none.

### System health: warn when a page has two security policies

- **Added:** 2026-10-08
- **Where:** `aeronautics_members/services/diagnostics.py` (System health),
  Admin › Settings › System health.
- **Why:** the problem above showed only as a broken-looking site; nothing in
  the portal said why.
- **What:** System health fetches the portal's own start page through its
  public address and looks at the answer's headers: more than one
  `Content-Security-Policy`, or one without the page's nonce in `style-src`,
  is a warning that says what it means ("something between the portal and the
  browser -- an old nginx file, a Cloudflare rule -- adds a second policy;
  pages then look broken") and where to look.
- **Open:** none.

### Pictures: the old forum's too, and in the Accounts list

- **Added:** 2026-10-08
- **Where:** `avatar_token_for` in `aeronautics_members/services/teams.py`
  (used by the top bar's `/me` in `api/session.py`, My Account and the team
  lists), `aeronautics_members/api/admin_accounts.py` and
  `frontend/src/pages/admin/Accounts.tsx`.
- **Why:** on the live site the maintainer saw no pictures: not their own in
  the top bar, none in Admin › Accounts. "Is that supposed to be like that?"
  -- No. (1) The portal only uses a picture uploaded and approved in the
  portal (`ForumAvatarSubmission`); a member who reconnected their old forum
  account has their picture on the `ImportedForumProfile` (`avatar_path`,
  served by `forum.forum_imported_avatar_public_file`, see
  `services/forum_profiles.py`), which the forum gets but the portal ignores.
  Most live members came from the old forum. (2) The Accounts list draws
  initials only; its API sends no picture.
- **What:** one helper for "this person's picture as an address": the approved
  portal picture, else the reconnected old forum profile's; used by every place
  that shows a person. The Accounts list rows get it too (load the pictures
  with the page's rows, not one query per row).
- **Privacy policy, decided 2026-10-08:** the maintainer wants the pictures in
  the portal too ("everyone sees their own picture … helpful for teams and
  team leaders and new team members so that they can get to know each other …
  if they are looking for someone, they could find them more easily"). The
  policy (`legal/privacy-policy/{en,de}/2026-10-04.md`) describes the picture
  only under § 22, the forum: "Name and profile picture may be visible to
  other forum members within the members' forum." It does not say "only on
  the forum", but it does not mention the portal either -- and the portal
  already shows approved pictures to a team's members in its list
  (`RosterPersonOut` in `api/teams.py`) and to leads (`api/team_manage.py`).
  So, in the same batch, a new version of the policy (a new dated file, both
  languages) that says where the portal shows the picture: to the member
  themselves, to the other members of their teams and to those teams' leads,
  and to administrators; for reconnected old-forum members, the old forum's
  picture as well. In § 22 (and "profile picture" in the list in § 27). Shown
  to the maintainer before it is published. No re-acceptance: the policy is
  information, not a contract (§ 46).
- **Not part of this:** a list where any member can look anyone up. The
  Accounts list is for admins only; "finding someone" here means the team
  lists. A member directory would be its own feature, with its own policy
  text.
- **Open:** none.

### Accounts list: old forum accounts hidden by default

- **Added:** 2026-10-08
- **Where:** Admin › Accounts -- the "Portal or old forum" filter
  (`KIND_CHOICES` in `frontend/src/pages/admin/Accounts.tsx`, `DEFAULT_FILTERS`
  and `filtersFromParams`/`paramsFromFilters` in
  `frontend/src/pages/admin/accountFilters.ts`; `AccountListQuery.kind` in
  `aeronautics_members/api/admin_accounts.py`).
- **Why:** the maintainer: "most of what I see are just non-active old forum
  accounts, and this doesn't really help anyone." The list shows "Portal and
  old forum" by default, and most rows on the live site are old forum people
  who have not come back.
- **What, as decided:** the default is **Portal accounts**, in the front end
  and in the API's default. "Portal and old forum" stays as a choice, kept in
  the address (`kind=all`) since the default is no longer `all`; "Clear
  filters" goes back to Portal accounts. Links from elsewhere (the dashboard)
  get the new default with them, which is right for all of them today.
- **Also decided:** showing old forum accounts is always a deliberate choice.
  Every other filter -- the membership chips at the top (with their counts),
  role, account state, search -- works on portal accounts only, unless old
  forum accounts were picked in the "Portal or old forum" filter (or with the
  search button below). The maintainer: "for all the other filters and those
  small batches that I can quickly select … the old forum accounts would not
  be included … But I can show them if I want to." (The counts already follow
  the other filters, `membership_counts` in `admin_accounts.py`; check that
  no chip, like "No membership", counts them anyway.)
- **My addition, unless the maintainer objects:** a search that finds nothing
  among portal accounts but would find old forum ones says so, with a button
  -- "2 old forum accounts match. Show them" -- so looking someone up by an
  old forum username still works in one step.
- **Open:** none.

### Team logos: no square behind them

- **Added:** 2026-10-08
- **Where:** the Teams overview cards (`.logo` in
  `frontend/src/pages/teams/Overview.module.css`), the team's About page hero
  (`.logoTile` in `frontend/src/pages/teams/About.module.css`), and anywhere
  else a team logo sits on a tile.
- **Why:** the maintainer, with a screenshot of the Teams overview: two cards,
  each logo in a dark square with a border, half over the cover and half over
  the card ("Joanneum Flight Team 'Ravens'" -- a raven on black;
  "Team Drone Tech" -- a shield-shaped logo, whose transparent corners show
  the dark square). "I think this isn't really nice … just show the logo …
  if the logo is transparent then just show it like it is."
- **What, as decided:** the logo alone, no background, no border, no padding:
  a transparent logo shows the cover and card behind it, a logo with its own
  background shows that. Same size and place as now.
- **My addition, unless the maintainer objects:** a soft shadow that follows
  the logo's own outline (`filter: drop-shadow`, not a box), so a dark logo
  still stands out over a dark cover photo.
- **Open:** none.

### Teams: run from the team's own pages -- leads appoint leads, admins' settings in the side menu

- **Added:** 2026-10-08
- **Where:** the team's side menu (`teamSidebar` in
  `frontend/src/frame/TeamLayout.tsx`), its Roles page (`RolesPage` in
  `frontend/src/pages/teams/manage/Settings.tsx`, `team_roles` and the
  treasurer endpoints in `aeronautics_members/api/team_manage.py`), the admin
  team page (`frontend/src/pages/admin/teams/Team.tsx`, `api/admin_teams.py`),
  the team permissions (`TEAM_ROLE_PERMISSIONS` in `services/teams.py`).
- **Why:** the maintainer: making someone a team lead or treasurer "should be
  done a bit more similar to the normal way I give someone roles … at the
  moment it's a bit annoying to have to go into the admin settings and teams
  and there give someone the team leader role … directly from the team page
  … the roles tab … only for appointing a treasurer … could be changed to also
  appoint a team leader … only shown to an admin or a team lead … a team lead
  should also be possible and allowed to appoint another team lead … selection
  based, like with the drop down … not … type in their exact email address."
  And: "if I am an admin and I want to do some admin stuff about the team …
  I just go to the team page and I just see more settings in the sidebar that
  I only see because I am an admin … keep the teams section in the admin
  settings because here new teams are created and set up for the first time
  … the team roles and the team's fee and team's details … in the normal
  teams settings."
- **What, as decided:**
  - **Roles page** (team › Settings › Roles): leads and treasurer, each
    appointed from a drop-down, never by typed address. Leads and site admins
    may appoint and remove leads (a change from `docs/teams-plan.md`, "Team
    leads", which said only site admins); removing the last lead still asks
    for confirmation. Leads still appoint the treasurer.
  - **Who can be chosen:** a lead chooses from the team's members. A site
    admin also from all of the association's members, searched by name --
    the first lead of a new team is not in it yet. A role still only counts
    while its holder is a member of the team (unchanged).
  - **Admin settings in the team's side menu**, seen only by site admins
    (`TEAMS_MANAGE`): the team's details (name, joining, places, forum group,
    access list switch), its fee, and archiving it -- the cards of today's
    admin team page, moved.
  - **Admin › Teams stays** for the list of all teams and creating one; a row
    leads to the team's own pages, and a new team opens on its admin details
    there. The separate admin team page goes; its address forwards.
- **Open:** none.

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
