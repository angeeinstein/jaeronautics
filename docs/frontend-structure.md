# Frontend Structure

How the portal is laid out and navigated: the blueprint for reworking the
current pages and, later, for a new front end (React or similar).

Chosen in October 2026 from three clickable mockups: **version A, "Admin
sidebar"**. The mockup is kept in [`docs/design/navigation-mockup.html`](design/navigation-mockup.html)
(open it in a browser; switch to version A at the top), with screenshots in
[`docs/design/navigation-a/`](design/navigation-a/).

**What is decided and what is a guide.** The *structure* is the decision:
the areas, the sidebars and what is in them, breadcrumbs, where tabs are used,
where each page lives, how phones behave (sections 2-6, 9-11). The *look* --
colours, fonts, sizes, spacing, button styles (sections 7, 8 and 14) -- is
what the mockup used and what felt right; it is a reference for the overall
impression, a starting point, not values to reproduce exactly. Improve on it
freely as long as the result stays calm, dark and clear. Where this document
and the mockup disagree, this document wins; the mockup has example data and
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
2. **The top bar says which area; the sidebar says where in it.** Areas:
   Forum, Teams, Admin -- My Account is the user menu and the logo, the
   admin area the gear beside it. Each sidebar belongs to one area and replaces the
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

- Far left, where a page has a sidebar (the admin area, a team's
  management): the **☰ button**, right above it. It folds the sidebar away
  and back; on a phone it opens it as a drawer.
- Then the logo mark, "Aeronautics" and a small "Members" label (the portal is
  not the association's website).
- Links: **Forum** (for a member: `/forum`, which signs them into the forum or
  says what is still missing) and **Teams** (while teams are switched on).
  The current area is underlined in the accent colour. My Account has no link
  here since October 2026: it is the user menu, and the logo.
- Right, for whoever may administer something: a **gear** (*Admin*) into the
  admin area, beside the user menu -- as on many sites, the back office behind
  an icon by the person, so the pages everybody uses stay as they are. It is
  lit while one is in the admin area. (Before: a link among the areas, whose
  menu then opened at the far left, which felt wrong; then a menu button that
  slid the admin menu in over other pages, which did too.)
- Right: **the user menu** -- the approved forum picture (or the initials) and
  the name. The one place to change the password from. Opens a
  small menu: name and email, *My account*, *Change password*, *Download my
  data*, *Log out*. Closes on a click elsewhere or Escape. "Log out" is no
  longer a link of its own in the bar.
- Signed out (public pages: landing, join, login, legal, thank you): the same
  bar with only *Log in*; those pages keep their own simple layout.

### Areas and their sidebars

| Area | Sidebar | Content width |
|---|---|---|
| My Account | its own side menu | narrow, centred (about 860 px) |
| Teams -- overview, a team's page, About | none | narrow, centred |
| Teams -- a team's management, its Money | the team's sidebar | full |
| Admin | the admin sidebar | full |

Sidebars are for the places where people *work* -- running a team, running
the association -- and for My Account, which grows (a credit balance is
planned) and so gets one place to add a page.

### The footer

Under every page, as the column above it is wide: Impressum, Privacy,
Statutes, Legal texts, Contact and Website, then the copyright -- small and
quiet, the links wrapping on a phone. The first three lead to the portal's
own legal texts unless an address elsewhere is configured.

## 4. The admin area

Entered with the **gear** at the right of the top bar; opens on the Dashboard.

**Sidebar**, top to bottom:

- **‹ Back to the portal** (to My Account)
- *Overview* -- **Dashboard**
- *People* -- **Accounts**, **Reviews** (count of what waits)
- *Teams* -- **Teams**, **Money**
- *System* -- **Logs**, **Settings** ▸
  - Settings folds open in place (chevron) to its parts: **General**,
    **Notifications**, **Membership fee**, **Forum**, **Mail accounts**,
    **Test email**, **System health**, **Updates**, **Backup and restore**.
    Clicking *Settings* opens it on General.
    (The forum's connection test is on the Forum page, beside what it tests.)
    While one is on a settings page it stays open.

Group labels are small, upper-case and muted; the current item is highlighted
with a soft accent background and accent text. Each item has a simple line
icon. Which items appear follows the permissions: the association's
Treasurer, for example, sees only what touches the money they transfer
(section 15).

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
  What used to be one Maintenance tab is three pages: System health (the
  report and what to do about it), Updates (version, install, roll back,
  the last run) and Backup and restore (with the background jobs).

## 5. The teams area

**Overview** (Teams): **My teams**, then **Other teams**, each team a **tile**
-- the whole tile is the way in, no buttons on it (since October 2026). A tile:
the team's cover picture (or the brand's backdrop), its logo shown whole in a
square box (logos come round, shield-shaped or square, never cut), one's place
in it in the corner (*Lead*, *Member*, *Applied*), its name and line, how many
are in it, and the one thing that matters now -- applications to answer, a fee
due, *Applications open* / *Open to join* and the fee for the others. What to
do about it is on the team's own pages. A member's or lead's tile opens the
team's overview, anybody else's its About page.

**A team's own area** -- every page of one team in one frame (TeamLayout),
with **the team's side menu** for whoever is in it or runs some part of it:

- **‹ All teams**
- the team's mark and name
- *Team* -- **Overview**, **About the team**
- *People* -- **Applications** (count), **Members**, **Former members**
- *Money* -- **Money**
- *Settings* -- **Team page**, **Applying**, **Access list** (only when switched
  on), **Roles**

A plain member sees only *Team*; the team's treasurer *Team* and *Money*; a
lead everything. Somebody not in the team gets its pages without the menu --
its About page, and joining it; the association's treasurer, who has no part
in the team, opens a team's Money page without it too. The ☰ at the left of
the top bar folds the menu away, as in the admin area.

**Overview** (Teams › Rocket Team): the team's cover, lower; *Needs your
attention* for whoever runs it (applications to answer, each a way straight
there); *Your membership* (paid until, paying for the next period, *Leave the
team…* set apart, quietly); *Members*, each by their picture, asked again every
minute so somebody who joins appears.

**About** (Teams › Glider Team) for everybody, the team presenting itself: a
cover (its picture, or the brand's backdrop) with logo, name, tagline, facts
(members, applications open, fee) and *Apply* for whoever may; below, the
formatted story and the photo grid (one large when they fill rows of three;
each opens in a viewer, arrow keys for the next), with *Applying* -- the
question, the rules with the tick box, *Apply* -- beside them on a wide screen,
below them on a phone. A member reads it in the team's frame, without the
form. The leads' *Team page* settings: the text with *Write* / *Preview*, cover
and logo, the photos (add several at once, caption, order, remove).

Breadcrumb: Teams › Rocket Team (its overview) › Members. Each settings section
is one card with *Save* at the bottom right and saves only its own fields (as
built).

## 6. My Account

Its own **side menu** (since October 2026), under *My account*: **Overview**,
**Profile**, **Membership and billing**, **Forum and picture**, **Privacy and
data**. An account without a membership has only *Overview* and *Privacy and
data*. A new part of the account is a new item there. The ☰ folds it away
on a wide screen and opens it as a drawer on a phone, as elsewhere. *Change
password* and the forum's own address (/forum) are in the same frame.

**Overview** (My Account): who this is -- the picture (or initials), the name,
the kind of membership, year group and login address -- then what is to be
done first (addresses waiting to be confirmed, with the link sent again right
there), then a **tile** for each part, the whole tile the way to its page:

- **Membership** -- its state, the day it is paid until, a bar of how much of
  the paid year has run, since when and when it renews; while a payment just
  made is confirmed, it says so and the page asks again every few seconds.
- **Forum and picture** -- its state (*Ready*, *Picture needed*, *In review*,
  …), the picture, the forum username and one line on where things stand.
- **Teams** -- across the row: one's teams with one's place in each, or what
  teams are; it opens the teams overview.

An account without a membership shows its login address and *Become a
member* instead of the tiles.

**Profile** -- **Contact details** read first (both email addresses, each with
whether it is confirmed and its link sent again, the phones, the address);
*Edit* turns the card into the form, saved at once, *Cancel* back. **Name and
membership type** likewise read first; *Request a change* opens the request an
admin decides, and while one waits it is shown instead.

**Membership and billing** -- the membership card: dates, what to do about it
(*Manage billing*, *Resume payment*, *Rejoin*). **Forum and picture** -- the
forum card with the picture upload. **Privacy and data** -- the download and
deleting the account by emailed link, with cancelling offered instead while a
paid membership runs.

### Joining

**Become a member** (/join) is a few short steps (since October 2026), the
steps on top with a line that fills, each done one a way back to it:

1. **Who you are** -- a card for each kind of member who may join (Student,
   Alumni, Staff or lecturer, Company or partner; an honorary member is
   appointed, never signs up). Choosing one moves on by itself.
2. **About you** -- salutation, title, name, then what that kind is asked,
   under its own heading. A student's and an alumnus's **year group** is
   picked, not typed: the programme (Aviation · Bachelor, LAV; Aviation ·
   Master, MAV; or another one by its three-letter code), then the year
   started from a row of recent years (or *Earlier*; an alumnus may say *I
   don't remember*), shown as it will be stored: LAV25. Any programme of the
   university may join; the scheme -- three letters, two digits -- is checked.
   A student gives their university address, which must be a student's
   (@edu.fh-joanneum.at); an alumnus theirs only if it still works; staff
   their institute address (@fh-joanneum.at -- a student's never counts);
   a company member the company. The forum name it makes appears as it is
   typed.
3. **Contact** -- the sign-in address, by kind: a student's and an alumnus's
   is private (a university one stops working when they leave); staff choose
   their institute address or a private one; a company member signs in with
   the company address. Then phone and address. For somebody signed in, their
   login's address, read only. When the institute address is the sign-in
   address, one confirmation confirms both.
4. **Password** -- signup only, with how strong it is.
5. **Check and join** -- everything once more, each line back to its step;
   what it costs today and every year, read from Stripe's price (or nothing
   today in the free months); the texts to accept, each opening over the form.
   How to pay is chosen on Stripe's page.

*After joining* (pay, confirm the address or both, add a picture for the
forum) stands beside the steps on a wide screen and in the last step on a
narrow one. Each step is checked before the next; a field the server refuses
takes the form back to its step. Steps slide in, fields that become relevant
fade in -- all of it still for whoever asks their device for less motion.

## 7. Page patterns

- **Page head**: title (display face), one-line description in muted text,
  actions right-aligned on the same line, wrapping under the title on phones.
- **Breadcrumb**: above the page head, small, links in the accent colour, the
  current page plain.
- **Cards**: a surface slightly lighter than the ground, thin border, rounded corners, header row with
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

## 8. Visual language (a guide)

- **Dark only** -- *decided.* The association's website, its emails and the
  portal today are all dark; the portal stays dark rather than following the
  device. (The mockup also has a light variant; ignore it.)
- **Colours**: what worked in the mockup -- a deep blue-grey ground, slightly
  lighter cards, one accent (the association's cyan) for links, the current
  item and the main button, and green, amber and red only for state. Keep the
  association's brand colours where they differ.
- **Type**: the mockup used a sturdy, slightly technical face for headings
  (Archivo), a plain readable one for text (IBM Plex Sans) and a monospaced one
  for figures and dates in tables. Any pairing with that character works; a
  monospaced or tabular face for figures is worth keeping.
- **Shape**: modest rounding, thin borders rather than shadows, even spacing.

## 9. Phones and narrow windows (below about 760 px)

- The top bar keeps its links (the logo shrinks to the mark, the user menu to
  the avatar); it scrolls sideways if it ever has to.
- A sidebar becomes a drawer: the **☰** button at the left of the top bar
  opens it over the page with a dimmed background; choosing an item or tapping
  the background closes it.
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
| Settings → Maintenance: health, updates, backups, background jobs | Settings → System health, Updates, Backup and restore |
| Admin → Accounts → account page (long single page) | Account page with tabs: Profile, Membership, Forum, Teams, Roles, Danger zone |
| Admin → Teams → team form | Admin › Teams › team (cards) + *Team page and settings* |
| Admin → Money | Admin sidebar → Money |
| Teams overview, team page, About, Leave | Teams area, no sidebar |
| A team's management (side list) | The team's sidebar (as built), with breadcrumb |
| A team's Money page | Teams area (from the team's sidebar or the overview) |
| Change password (page) | User menu → Change password |
| Download my data (button on My Account) | User menu → Download my data (and stays on My Account) |
| `/forum` (forum status and picture upload) | My Account › Forum and picture; *Open forum* goes to the forum |

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

## 14. Measurements and colours in the mockup (a guide)

What the mockup used, for reference when rebuilding the overall impression.
None of it is a requirement: adjust sizes, colours, fonts and button styles as
the real pages, the brand or a component library suggest.

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

**Colours** (the mockup's dark palette; the portal is dark only):

| Role | In the mockup | Use |
|---|---|---|
| ground | `#0d1316` | page background |
| surface | `#141c20` | cards, top bar, sidebar |
| raised | `#1a2429` | hover, inputs, the team's sidebar |
| text | `#e4edf1` | text |
| muted | `#93a5b0` | descriptions, labels |
| line | `#283740` | borders |
| accent | `#2fd0ec` | links, current item, main button |
| accent, soft | `#10343d` | current item background, info pills |
| good | `#5fd193` on `#13301f` | Active, Paid |
| warning | `#f0b45c` on `#3a2a12` | Ending, Waiting |
| danger | `#ff8a80` | destructive actions |

The mockup's CSS is at the top of `docs/design/navigation-mockup.html`.

## 15. Decisions and open points

- **Dark only** -- *decided* (section 8).
- **Admin → Teams and a team's management stay separate** -- *decided.* They do
  different things: the admins' part (name, joining, fee, forum group, access
  list switch, leads, archiving) and the leads' part (people, team page,
  applying, access list, roles). They link to each other.
- **What the association's Treasurer sees** -- *decided (step 4.5).* Everything
  that touches the money they transfer to the teams, and nothing else of the admin
  area: Money (what each team is owed, the check against Stripe), each team's money
  with its payments, transfers and bank details, and a dashboard that shows what is
  open to transfer -- not the membership figures, which need the accounts
  permission. Who paid is shown, as it is what leads to the amounts; no other member
  data.
