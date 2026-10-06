# New Front End: Plan

How the portal moves from server-rendered pages (Jinja, Bootstrap) to a React
front end on a JSON API. The *what* -- areas, sidebars, page patterns, the
look -- is in `docs/frontend-structure.md` (version A of the mockups) and
`docs/design.md`; this is the *how* and the order.

## Principle: no compromises for the old code's sake

The new front end is not fitted around how things are done today. Where the
code, a library, a policy or the installer is in the way of a better, more
modern or simpler result, it is changed -- the security policy, the session
setup, the way settings are stored, the installer, the tests -- not worked
around. Bigger changes that affect how the server is run or what members see
are asked first; technical ones are decided and written down here.

What must stay compatible:
- **The live database and its data.** Every change to it is an Alembic
  migration that carries the live data over, and the update from `main` to
  this branch is tested on a copy of real data before the merge.
- **Links already sent out:** password reset, email confirmation and welcome
  links in emails, and the Stripe and forum return addresses, keep working.
- **What the portal does:** the business rules (payments, memberships, teams,
  forum) are kept, not reimplemented. Changing them is a decision of its own.


| | Decided |
|---|---|
| Branch | `claude/new-frontend`. The test server runs it; the live site stays on `main` and gets fixes from `claude/membership-site-review-12nfxx` as before. Merged to `main` only when the new front end is complete. |
| Old pages | Deleted as soon as their new page exists: one version of each page, never two. Pages may be missing in between -- only the test server runs this branch. |
| Order | API foundation, then the front end's frame, then **admin area → teams area → My Account → public pages and signup**. |
| Build | On the server, by `install.sh`, on every install and update. |
| Components | Mantine, themed with our design; our own components where the portal needs its own; TanStack Table for data tables. |
| Look | Dark only (`docs/frontend-structure.md` §15). |
| Language | English only, as today. Text in the front end, no translation layer until a second language is wanted. |

## What stays on the server

Not everything becomes a React page. These stay Flask, unchanged:

- **Emails** (Jinja templates, `emails/_layout.html`) and the **legal PDFs**.
- **Redirects to other services:** Stripe Checkout and the billing portal,
  the forum's single sign-on (DiscourseConnect), the Stripe webhook.
- **File downloads:** data export, backups, legal PDFs, the legal text
  template, CSV exports. The front end links to them.
- **Background jobs, CLI commands, `install.sh`** -- untouched.
- **Business logic:** everything in `aeronautics_members/services/`. The API
  calls the same functions the pages call today; nothing is written twice.

## 1. The API

**Where:** a new package `aeronautics_members/api/`, one module per area
(`me.py`, `admin_accounts.py`, `teams.py`, ...), all under `/api/v1/`.

**Signed in:** the same session cookie as today (Flask-Login). No tokens: the
front end and the API share one domain. Not signed in, the API answers `401`
with JSON -- never the redirect to the login page the pages get.

**CSRF:** Flask-WTF's protection stays on. The front end reads the token from
`GET /api/v1/session` and sends it as `X-CSRFToken` on every change.

**Permissions:** checked on the server for every endpoint, with the
`requires(Permission...)` decorators the pages use today. The front end only
uses them to decide what to show: `GET /api/v1/me` returns the person, their
roles and permissions, and the counts the sidebar shows (reviews waiting,
applications).

**Answers:**
- Success: the data, as JSON. Lists: `{"items": [...], "total": n}`, with
  `page`, `per_page`, `sort` and filters as query parameters where a list
  needs them.
- Errors, always the same shape:
  `{"error": {"code": "team_not_joinable", "message": "...", "fields": {...}}}`.
  The services' errors (`ValidationError` 400, `PermissionError_` 403,
  `NotFoundError` 404, `ConflictError` 409, `ExternalServiceError` 502)
  already carry a code, a message and a status; one error handler turns them
  into this. `fields` holds per-field messages for forms.

**Schemas:** every request and answer is a Pydantic model. That gives
validation of what comes in, one place that says what goes out, and an
OpenAPI description of the whole API. From that description the front end's
TypeScript types are generated (`openapi-typescript`), so a field renamed on
the server is a type error in the front end, not a blank on a page.

**Names and formats:** fields keep the names the database and services use,
snake_case in the JSON too, so one thing has one name everywhere. Times are
UTC with a `Z` (`2026-10-06T07:38:11Z`); days are `YYYY-MM-DD`. A request
with a field the endpoint does not know is refused, so a misspelt field is an
error, not silently ignored.

**Leaving for another site** (Stripe Checkout or billing, the forum): the
endpoint answers `{"redirect_url": ...}` and the front end goes there.

**Rate limits:** the same Flask-Limiter limits as the pages they replace.

**Tests:** every endpoint gets pytest tests like the pages have now -- signed
out, without the permission, with bad input, and the real thing. The tests of
a page are moved to its endpoint when the page is replaced.

## 2. The front end

**Where:** `frontend/` at the top of the repository.

**Stack:**
- React and TypeScript (strict), built with Vite.
- Mantine for components, forms (`@mantine/form`), notifications and hooks.
- TanStack Query to load and cache the API's data and refresh it after a
  change; TanStack Table (version 9) for the admin lists, added with the first
  list that needs it (step 4.2); React Router for the pages, each page's code
  loaded when it is first opened.
- Tabler Icons, the line icons in the sidebar.
- Vitest for component tests, Playwright for end-to-end tests through the
  real Flask app (the browser is already set up in CI and here).
- ESLint and Prettier, in CI like ruff is for Python.

**Theme:** `docs/design.md` becomes one Mantine theme: our colours, Inter,
Archivo and IBM Plex Mono (self-hosted, as now), zero rounded corners,
spacing, forced dark. Pages never set colours or sizes of their own.

**Our own components** (`frontend/src/components/`), built from Mantine pieces
where useful: `AppShell` with `TopBar`, `UserMenu` and the area `Sidebar`
(groups, entries with counts, folding sections, back link), `Breadcrumbs`,
`PageHeader`, `Pill` (status), `ConfirmButton` (two-step confirm), `StatTile`,
and the empty, loading and error states. `DataTable` (clickable rows, filter
chips, search) is built with the accounts list in step 4.2, shaped by a real
list rather than guessed.

**Addresses:** the URLs stay as they are (`/admin/accounts/12`, `/teams/rocket`,
...). For every page that has moved, Flask answers its URL with the front
end's one HTML file, which loads the app; the app shows the page. Paths that
have not moved are still Flask's pages until they do. A page's URL is
therefore never broken, whichever side draws it.

**Files:** the build writes to `aeronautics_members/static/app/` (not in git),
which nginx already serves under `/static/`. File names carry a hash, so they
can be cached for a long time and an update never shows a stale script.

**Security policy** *(settled in step 3)*: as strict as before in effect --
scripts and styles from the portal only, no third-party code -- but built for
the new front end. Mantine writes `<style>` tags as part of normal operation
(its colour variables, its global classes, every responsive size), so turning
them off would have meant giving up features. Instead the policy moved from
nginx's fixed header into Flask (`aeronautics_members/content_security.py`),
which sends it with every answer and a fresh nonce each time: a `<style>` tag
carrying the nonce is allowed, `style=""` attributes and everything else
inline stay blocked. The nonce reaches the app in `<meta name="csp-nonce">`;
Mantine and the scroll lock of dialogs (`get-nonce`) put it on their tags.
nginx sets no policy any more: a second, fixed one would apply as well and
block what the first allows. Checked in Chromium: no violations, desktop and
phone, every test of `e2e/`.

## 3. Build and deployment

- **`install.sh`** installs Node.js (current LTS, from NodeSource's package
  repository -- Ubuntu's own is too old for Vite) with the other packages,
  and on every install and update runs `npm ci` and `npm run build` in
  `frontend/` before the portal restarts. A build that fails stops the update
  like a failed migration does, and the rollback applies.
  The build goes into a folder beside the live one and is swapped in whole;
  the previous build's files stay one more update, so a page opened before
  the update still loads its scripts.
- **CI** gets a front-end job: install, the API's types current, type check,
  lint, formatting, Vitest, build; and an end-to-end job running Playwright
  against the built front end and Flask. CI runs Python 3.12 and Node.js 24,
  what the servers run.

## 4. Steps

Each step ends with tests, a full test run, a commit and a push; the test
server can update to it.

1. **This plan** -- for review.
2. **API foundation** *(done: `aeronautics_members/api/`, `tests/test_api_foundation.py`,
   `docs/frontend-routes.md`)*: the `api` package, JSON errors, `401` instead of
   redirects, CSRF header, `GET /api/v1/session` and `GET /api/v1/me`,
   Pydantic schemas, the OpenAPI document and the type generation, test
   helpers. A **route map** (`docs/frontend-routes.md`): every one of today's
   122 routes, marked as page (→ which API endpoints, which React page),
   action (→ which endpoint), download or redirect (stays), and its step.
3. **Front-end foundation** *(done: `frontend/`, `blueprints/app_shell.py`,
   `content_security.py`, `install.sh`, CI)*: `frontend/` with Vite, React, TypeScript, Mantine
   and the theme; the security-policy check; the API client with types and
   TanStack Query; the router; `AppShell` with top bar, user menu and the
   admin sidebar; our components; Flask serving the app for moved paths;
   `install.sh` and CI. Ends with an empty admin area in the new frame.
4. **Admin area**, one section at a time, each: its endpoints and tests, its
   React page, then the old route, template and page tests removed.
   1. Dashboard *(done: `api/admin_dashboard.py`, `services/dashboard.py`,
      `frontend/src/pages/admin/AdminDashboard.tsx`)*
   2. Accounts: list, detail with its tabs, and every action on an account
      *(list done: `api/admin_accounts.py`, `services/account_directory.py`,
      `frontend/src/pages/admin/Accounts.tsx`, `components/DataTable.tsx`.
      Its two overlapping membership filters became one, by state --
      active, ending, payment pending, payment failed, ended, none -- worked
      out by the database, so a row's label, the filter and the sort agree;
      the dashboard's "Ending" figure counts the same people. Links to the
      old page's filters still work. The account page done too: `api/admin_account.py`,
      `services/account_admin.py` -- every action on an account as one service function,
      checks, audit entry, forum and Stripe follow-ups included -- and
      `frontend/src/pages/admin/account/`, a tab per subject plus Activity.
      What erasing would do asks Stripe, so it is read only when the Danger zone
      tab opens. The page offers an action only where the server would not refuse it.)*
   3. Reviews (photo approvals) *(done: `api/admin_reviews.py`; the four decisions moved
      into `services/reviews.py` beside the queue they act on;
      `frontend/src/pages/admin/reviews/`. A decision on something decided meanwhile
      is a conflict, said as such.)*
   4. Teams (the admins' part) *(done: `api/admin_teams.py`;
      `frontend/src/pages/admin/teams/` -- the list with the switch and names,
      a new team, and a team's details, fee, roles and archiving, each saved on its
      own. A fee change asks again and then says what it did to the members already in.
      The short name `new` is refused, as `/admin/teams/new` is the form. What a team
      says about itself stays on its management page until step 5.)*
   5. Money *(done: `api/admin_money.py`; `frontend/src/pages/admin/money/` -- what
      each team is owed, the check against Stripe on request, and a team's money on the
      association's side at `/admin/money/<slug>`: transfers, with a GiroCode that
      follows the amount and reference typed, and bank details. Recording transfers left
      the team's own Money page, which its leads keep until step 5. The Treasurer's
      dashboard shows what is open instead of the membership figures. Days are typed and
      shown as 31.12.2026 (`components/DayInput.tsx`, Mantine's date input); amounts as
      €1,234.50, on the server too.)*
   6. Logs *(done: `api/admin_logs.py`, the reading moved into `services/audit.py`;
      `frontend/src/pages/admin/Logs.tsx` -- search, category, and new: everything about
      one person (`?user=`), which an account's Activity tab links to. Secrets stay out,
      as before. Links in text take the theme's link colour everywhere.)*
   7. Legal Texts *(done: `api/admin_legal.py`; `frontend/src/pages/admin/LegalTexts.tsx`.
      The API core takes uploaded files (`uploads=`) and answers files (`produces=`), both
      in the OpenAPI description. PDF links make the PDF first, then open it
      (`components/PdfLink.tsx`, as legal-pdf-open.js does on Flask's pages).)*
   8. Settings, section by section (general, billing, mail, forum,
      notifications, maintenance with update, backup and restore)
      *(General, Notifications, Membership fee and Forum done: `api/admin_settings.py`; the one
      save for every section became a service per section (`services/settings_sections.py`), each
      changing only its own settings and logging one entry when something changed; the
      secrets are never sent to the browser, only whether one is set. Pages at
      `/admin/settings/<section>`, `frontend/src/pages/admin/settings/`; an old link to a
      section's tab opens its page. Mail accounts and Test email done: `api/admin_mail.py`,
      `services/mail_accounts.py`; the export of the accounts with their passwords now asks
      for the current password.)*
5. **Teams area:** overview, a team's page and join page with the rules
   dialog, the leads' management sections, the team's money.
6. **My Account:** the overview, membership and payment status, forum card
   with the photo upload and cropper, change requests, password, data export
   and deletion.
7. **Public pages and signing in:** the new home page (association
   information), signup on its own page with the legal texts in a dialog,
   login, password reset, email confirmation, thank-you and cancel pages,
   the legal texts' pages.
8. **Clean-up:** remove Bootstrap, `base.html`, the page templates, WTForms
   where nothing uses it any more, and their CSS and scripts; update the
   docs; a full review of the branch; then the merge to `main` and the live
   site's update.

## 5. Open, to decide on the way

- **The new home page's content** -- needed at step 7.
