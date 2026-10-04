# Frontend Structure

How the portal is laid out and navigated: the blueprint for reworking the
current pages and, later, for a new front end (React or similar). It describes
**where things are and how one moves between them**, not pixel styling.

Chosen in October 2026 from three clickable mockups: **version A, "Admin
sidebar"**. The mockup is kept in [`docs/design/navigation-mockup.html`](design/navigation-mockup.html)
(open it in a browser; switch to version A at the top). Where this document and
the mockup disagree, this document wins; the mockup has example data and
leaves things out.

---

## 1. The problem it solves

The portal used to stack three navigation levels on one screen: the top menu
(My Account, Teams, Admin), a row of admin tabs under it (Dashboard, Accounts,
Reviews, …) and, in Settings, a vertical list inside the tab. Each was fine on
its own; together they made it unclear which level one was on.

What others do (looked at: Stripe, the Microsoft Entra admin center, Discourse,
Wild Apricot, Shopify's and other design guidelines) converges on one pattern:
a slim top bar for the whole application, **one sidebar for the area one is
in**, breadcrumbs for where one is inside it, and never more than two levels of
navigation visible at once. Discourse went through exactly our change: its admin
area had tabs at the top of the page and replaced them with an admin sidebar
that appears when entering the admin area.

## 2. Principles

1. **At most two levels of navigation on screen**: the top bar, and either a
   sidebar *or* tabs on a detail page -- never a sidebar, tabs and a list.
2. **The top bar says which area; the sidebar says where in it.** Areas: My
   Account, Teams, Admin. Each sidebar belongs to one area and replaces the
   previous one rather than nesting inside it.
3. **Every page in a sidebar area has a breadcrumb** whose first entry is the
   area (Admin › Accounts › Anna Berger). It replaces "Back" buttons.
4. **Tabs only on a detail page** -- one person, one team -- to split what
   belongs to that one thing (Profile, Membership, Forum, …).
5. **One main action per page and per box.** One highlighted button; the rest
   plain. Destructive actions are outlined in red and set apart (Danger zone).
6. **State is visible at a glance**: status pills (Active, Ending, Payment on its
   way, Ended), counts on sidebar items that need attention (Reviews 2,
   Applications 1).
7. **Whoever may not use something does not see it.** Menu entries follow the
   permissions; the server checks regardless.
8. **Names from the member's side of the screen**: "Membership fee", not
   "Billing configuration"; "Back to the portal", "All teams".

## 3. The frame

### Top bar (every page while signed in)

- Left: logo mark, "Aeronautics" and a small "Members" label (the portal is
  not the association's website).
- Links: **My Account**, **Teams** (while teams are switched on), **Admin**
  (only with access to the admin area). The current area is underlined in the
  accent colour.
- Right: **the user menu** -- a round avatar with initials and the name. Opens a
  small menu: name and email, *My account*, *Change password*, *Download my
  data*, *Log out*. Closes on a click elsewhere or Escape. "Log out" is no
  longer a link of its own in the bar.
- Signed out (public pages: landing, join, login, legal, thank you): the same
  bar with only *Log in*; those pages keep their own simple layout.

### Areas and their sidebars

| Area | Sidebar | Content width |
|---|---|---|
| My Account | none | narrow, centred (about 860 px) |
| Teams -- overview, a team's page, About, Money | none | narrow, centred |
| Teams -- a team's management | the team's sidebar | full |
| Admin | the admin sidebar | full |

The member side needs no sidebar: it is a handful of pages, reached from the
top bar and from links on the pages themselves. Sidebars are for the places
where people *work*: running a team, running the association.

## 4. The admin area

Entered with **Admin** in the top bar; opens on the Dashboard.

**Sidebar**, top to bottom:

- **‹ Back to the portal** (to My Account)
- *Overview* -- **Dashboard**
- *People* -- **Accounts**, **Reviews** (count of what waits)
- *Teams* -- **Teams**, **Money**
- *System* -- **Logs**, **Settings** ▸
  - Settings folds open in place (chevron) to its parts: **General**,
    **Notifications**, **Membership fee**, **Forum**, **Mail accounts**,
    **Test tools**, **Maintenance**. Clicking *Settings* opens it on General.
    While one is on a settings page it stays open.

Group labels are small, upper-case and muted; the current item is highlighted
with a soft accent background and accent text. Each item has a simple line
icon. Which items appear follows the permissions: the association's
Treasurer, for example, sees Dashboard and Money only.

**Pages:**

- **Dashboard** -- three to four figure tiles that are links (Active members →
  Accounts, Waiting for review → Reviews, Open to transfer to teams → Money),
  then *Recent activity*.
- **Accounts** -- page title, one-line description, *Export* at the top right; a
  search field and filter chips (All, Active, Ending, Ended, …); a table whose
  rows are clickable (avatar + name, kind, status pill, paid until).
- **An account** (Admin › Accounts › Name) -- the name with its status pill,
  the email under it, then **tabs**: *Profile*, *Membership*, *Forum*, *Teams*,
  *Roles*, *Danger zone* (deactivate, erase -- outlined red, behind the
  two-step confirm or the typed email). Each tab one card.
- **Reviews** -- one card per item (picture or name change) with *Approve*
  (main) and *Reject* (outlined red, two-step).
- **Teams** -- table of teams (mark, name, members, fee, lead), *New team* as
  the main action. **A team** (Admin › Teams › Rocket Team): the admins' part in
  cards -- Details (name, joining, access list switch), Fee, Roles, Archive --
  and *Team page and settings* at the top right, which leads into the team's
  management.
- **Money** -- table of teams (paid, transferred, open); a row opens that
  team's Money page.
- **Logs** -- table (when, who, what) with filters.
- **Settings pages** -- each one card of fields with *Save* at the bottom right.
  Maintenance keeps its own parts (updates, backups, background jobs) as
  separate cards on that one page.

## 5. The teams area

**Overview** (Teams): page title; **My teams** as rows (mark, name, status pill,
paid until; *Open* as the main button, *Manage* and *Money* when one has those
rights, *Leave* set apart as a quiet button at the far right); **Other teams**
as cards (mark, name, short description, fee, *About & apply*).

**A team's page** (Teams › Rocket Team) for its members: title and short
description, *About the team* and *Manage* at the top right; *Your membership*
(paid until, Leave); *Members*.

**About** (Teams › Glider Team) for everybody: the longer text and picture,
then *Applying* -- fee, the question, the rules with the tick box, *Apply*.

**Management** -- entering it swaps in **the team's sidebar**:

- **‹ All teams**
- the team's mark and name
- *People* -- **Applications** (count), **Members**, **Former members**
- *Settings* -- **Team page**, **Applying**, **Access list** (only when switched
  on), **Roles**
- *Elsewhere* -- **Team page**, **Money**

Breadcrumb: Teams › Rocket Team › Members. Each settings section is one card
with *Save* at the bottom right and saves only its own fields (as built).

## 6. My Account

One narrow column of cards, two side by side on wide screens:
**Membership** (status pill; paid until, next renewal and amount, paid by;
*Manage billing*), **Forum** (forum name and picture state; *Open forum*),
**Profile** (name, university email, cohort; *Edit*), **Teams** (one's teams;
*All teams*). The flows that exist today -- verifying emails, paying,
uploading the picture, profile change requests, deleting the account -- stay
on this page as cards that appear when they apply, at the top when something
is to be done.

## 7. Page patterns

- **Page head**: title (display face), one-line description in muted text,
  actions right-aligned on the same line, wrapping under the title on phones.
- **Breadcrumb**: above the page head, small, links in the accent colour, the
  current page plain.
- **Cards**: white (or dark) surface, thin border, 10 px radius, header row with
  a title and optional right-aligned note or pill; body with even spacing.
  Borders rather than shadows separate things; the shadow is kept for menus
  and the app frame.
- **Tables**: upper-case small column headings, rows separated by a line, the
  whole row clickable when it leads somewhere (hover tint). Numbers in a
  tabular face. On phones the table scrolls sideways inside its card.
- **Figure tiles**: big number, label under it; a tile is a link.
- **Pills**: green *Active/Paid*, amber *Ending/Payment on its way/Waiting*,
  accent *Info/Invited*, grey *Ended/Withdrawn*.
- **Buttons**: one primary (filled accent) per box; secondary (outlined
  neutral); quiet (text only, e.g. Leave); danger (outlined red). Irreversible
  actions keep the two-step confirm button.
- **Feedback**: a short toast at the bottom ("Saved", "Approved") instead of a
  banner across the page; errors stay next to the field they concern.
- **Empty states**: one muted sentence in the card ("Nobody yet.").

## 8. Visual language (as in the mockup)

- **Colours**: a cool, slightly blue-grey ground; white (light) or deep
  blue-grey (dark) cards; one accent -- the association's cyan, darker in
  light mode for contrast; semantic green, amber, red for state only.
- **Light and dark**: the mockup follows the device's setting. The current
  portal is dark only; whether to offer both is a decision for the redesign
  (recommended: both, following the device).
- **Type**: Archivo for headings (a sturdy, slightly technical grotesque),
  IBM Plex Sans for text, IBM Plex Mono for figures and dates in tables.
- **Shape**: 6--12 px radii, 1 px borders, generous but even spacing (gaps of
  8 / 12 / 16 / 24 px).

## 9. Phones and narrow windows (below about 760 px)

- The top bar keeps its links (the logo shrinks to the mark, the user menu to
  the avatar); it scrolls sideways if it ever has to.
- A sidebar becomes a drawer: a **☰** button at the left of the top bar opens
  it over the page with a dimmed background; choosing an item or tapping the
  background closes it.
- Two-column card grids become one column; label/value lists stack.

## 10. Accessibility

The current page and area are marked (`aria-current`); the breadcrumb is a
`nav` named "Breadcrumb"; tabs are proper tab controls; the user menu and the
drawer close with Escape; focus is always visible; the sidebar sections are
headed. Motion is minimal and off when the device asks for reduced motion.

## 11. Where today's pages go

| Today | In the new structure |
|---|---|
| Top menu: My Account, Teams, Admin, Logout | Top bar: My Account, Teams, Admin; Logout moves into the user menu |
| Admin tab row (Dashboard … Settings) | Admin sidebar |
| Admin → Settings with its vertical list | Admin sidebar → Settings, folded open |
| Settings → Backup & Restore, updates, background jobs | Settings → Maintenance |
| Admin → Accounts → account page (long single page) | Account page with tabs: Profile, Membership, Forum, Teams, Roles, Danger zone |
| Admin → Teams → team form | Admin › Teams › team (cards) + *Team page and settings* |
| Admin → Money | Admin sidebar → Money |
| Teams overview, team page, About, Leave | Teams area, no sidebar |
| A team's management (side list) | The team's sidebar (as built), with breadcrumb |
| A team's Money page | Teams area (from the team's sidebar or the overview) |
| Change password (page) | User menu → Change password |
| Download my data (button on My Account) | User menu → Download my data (and stays on My Account) |
| `/forum` (forum status and picture upload) | My Account → Forum card; *Open forum* goes to the forum |

## 12. For the new front end

Components the structure needs (any component library with an "app shell"
provides most of them):

- `AppShell` (top bar + optional sidebar + main), `TopBar`, `UserMenu`
- `Sidebar` with `SidebarGroup` (label) and `SidebarItem` (icon, label, count,
  current, optional children that fold open), a back link at the top
- `Breadcrumbs`, `PageHeader` (title, description, actions)
- `Tabs`, `Card`, `DataTable` (clickable rows, filter chips, search),
  `StatTile`, `Pill`, `Toast`, `ConfirmButton` (the two-step confirm),
  `Drawer` for phones

What it needs from the server: who the user is and what they may do (to build
the menus), the counts shown in the sidebar (reviews, applications), and the
data of each page -- today rendered on the server, then from a JSON API. The
URLs stay as they are; the new front end routes the same paths.

## 13. Screenshots of version A

In [`docs/design/navigation-a/`](design/navigation-a/), taken from the mockup
(example data):

| File | Shows |
|---|---|
| `01-admin-dashboard.png` | Admin area: sidebar with groups, Settings folded open; figure tiles; recent activity |
| `02-admin-accounts.png` | Accounts list: search, filter chips, clickable rows with status pills |
| `03-admin-account-profile.png` | One account: breadcrumb, name with pill, tabs, Profile tab |
| `04-admin-account-danger-zone.png` | The Danger zone tab: deactivate, erase (outlined red) |
| `05-admin-reviews.png` | Reviews: one card per item, Approve / Reject |
| `06-admin-teams.png` | Teams list with *New team* as the main action |
| `07-admin-team.png` | One team, the admins' part, *Team page and settings* top right |
| `08-admin-money.png` | Money per team |
| `09-admin-settings-general.png` | Settings → General, current sub-item highlighted in the sidebar |
| `10-admin-settings-maintenance.png` | Settings → Maintenance |
| `11-user-menu.png` | The user menu opened from the top bar |
| `12-my-account.png` | My Account: no sidebar, narrow centred cards |
| `13-teams-overview.png` | Teams: My teams as rows, Other teams as cards |
| `14-team-page.png` | A team's page for its members |
| `15-team-about.png` | A team's About page with applying |
| `16-team-manage-applications.png` | A team's management: the team's sidebar, breadcrumb, count |
| `17-team-manage-roles.png` | Management → Roles |
| `20-phone-my-account.png` | Phone: top bar shrunk to the mark and avatar |
| `21-phone-admin-dashboard.png` | Phone: admin area with the ☰ button, sidebar hidden |
| `22-phone-admin-menu-open.png` | Phone: the sidebar as a drawer over the dimmed page |

## 14. Measurements and colours (from the mockup)

**Frame**: top bar 54 px high, 1 px bottom border; sidebar 236 px wide (drawer
260 px on phones), 1 px right border, items 7 px × 10 px padding, 6 px radius,
2 px between items; group labels 0.7 rem, upper-case, 0.08 em letter-spacing,
12 px above; the folded-open Settings children indented 14 px with a 1 px line
on their left. Main area padding 20 px top, 24 px sides (16 px on phones); the
narrow column 860 px wide at most. Breakpoint 760 px.

**Type**: base 15 px, line height 1.5; page title 1.4 rem Archivo bold; card
titles Archivo semi-bold; table headings 0.78 rem upper-case 0.06 em; hints
0.8–0.85 rem muted; figures in IBM Plex Mono with tabular digits; figure tiles
1.6 rem Archivo.

**Shapes**: cards 10 px radius, 1 px border; buttons 7 px radius, 7 × 14 px
padding; pills 4 px radius, 0.72 rem upper-case; the app frame and menus carry
the only shadows (`0 1px 2px` + `0 8px 24px`, low opacity).

**Colours** (light / dark):

| Token | Light | Dark | Use |
|---|---|---|---|
| bg | `#eef3f6` | `#0d1316` | page ground |
| surface | `#ffffff` | `#141c20` | cards, top bar, sidebar |
| surface-2 | `#f4f8fa` | `#1a2429` | hover, inputs, team sidebar |
| fg | `#13222c` | `#e4edf1` | text |
| muted | `#5a6c78` | `#93a5b0` | descriptions, labels |
| border | `#d4dfe6` | `#283740` | lines |
| accent | `#0089a8` | `#2fd0ec` | links, current item, primary button |
| accent-soft | `#dcf2f8` | `#10343d` | current item background, info pills |
| ok / ok-soft | `#1d7a4a` / `#e1f3e8` | `#5fd193` / `#13301f` | Active, Paid |
| warn / warn-soft | `#a5610a` / `#fbefdc` | `#f0b45c` / `#3a2a12` | Ending, Waiting |
| danger | `#b3261e` | `#ff8a80` | destructive actions |

The exact CSS is at the top of `docs/design/navigation-mockup.html`.

## 15. Open points

- Light and dark, or dark only (section 8).
- Whether the association's Treasurer gets a trimmed admin area (Dashboard and
  Money only) or a page of their own.
- Whether Admin → Teams and the team's management should merge for site
  admins (today: the admins' part and the leads' part are separate, linked by
  *Team page and settings*).
