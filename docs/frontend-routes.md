# Route Map: from Flask Pages to the New Front End

Every route the portal has today (123, from `app.url_map`), and what becomes
of it -- the working list for `docs/frontend-plan.md`. A route is ticked off
here when its replacement is in and the old one is gone.

**Kinds of route, and what each becomes:**

- **page** -- a GET that renders a template. Becomes a page of the front end
  at the **same URL** (Flask hands that URL the app), with the data from
  `GET /api/v1/...`. The template is deleted.
- **action** -- a POST from a form that redirects back with a message.
  Becomes an API call (`POST`/`PATCH`/`PUT`/`DELETE /api/v1/...`) answering
  JSON; the page shows the result in place.
- **out** -- an action that sends the browser to another site (Stripe
  Checkout or billing, the forum). Becomes an API call answering
  `{"redirect_url": ...}`; the front end then goes there. The return addresses
  Stripe and the forum use stay as they are.
- **download** -- a file (PDF, CSV, JSON, picture). **Stays a Flask route**;
  the front end links to it.
- **link in an email** -- an address already sent out in emails. Must keep
  working forever: either the page at that URL, or Flask doing the work and
  redirecting to a page.
- **stays** -- not part of the front end at all.

Status: `[ ]` to do, `[x]` done.

## Stays as it is

| Route | Why |
|---|---|
| `POST /stripe-webhook` | Stripe's webhook. |
| `GET /__health` | Health check for monitoring. |
| `GET /static/...` | Static files; the built front end is served from here too. |
| `GET /forum/discourse/connect` | The forum's single sign-on handshake (DiscourseConnect). |
| `GET /forum/logout` | The forum's logout return. |
| `GET /forum/avatar/public/<token>`, `/forum/avatar/imported/<token>` | Pictures the forum fetches. |
| `GET /teams/logo/<token>`, `/teams/picture/<token>` | Team pictures (download). |
| `GET /legal/<slug>/pdf[/<version>]`, `/teams/<slug>/rules/pdf[/<version>]` | Legal PDFs (download; the "make it first" step becomes an API call). |

## Step 4 -- Admin area

### 4.1 Dashboard
- [x] `GET /admin` (page; also the endpoint alias `admin`) → dashboard (`GET /api/v1/admin/dashboard`)

### 4.2 Accounts
- [x] `GET /admin/accounts` (page) → list with filters and search (`GET /api/v1/admin/accounts`)
- [x] `GET /admin/accounts/<id>` (page) → detail with tabs (`GET /api/v1/admin/accounts/<id>`; what erasing would do: `.../erasure`; old forum search: `.../old-forum-candidates`)
- [x] `POST /admin/accounts/<id>/billing-sync` → `POST /api/v1/admin/accounts/<id>/billing-sync`
- [x] `POST /admin/accounts/<id>/delete` (cancels the Stripe subscription) → `POST /api/v1/admin/accounts/<id>/erase`
- [x] `POST /admin/accounts/<id>/disabled` → `PUT /api/v1/admin/accounts/<id>/disabled`
- [x] `POST /admin/accounts/<id>/email` (correct the private email) → `PUT /api/v1/admin/accounts/<id>/email`
- [x] `POST /admin/accounts/<id>/forum-resync` → `POST /api/v1/admin/accounts/<id>/forum-resync`
- [x] `POST /admin/accounts/<id>/picture-replacement` → `PUT /api/v1/admin/accounts/<id>/picture-replacement`
- [x] `POST /admin/accounts/<id>/reconnect` (old forum account) → `POST /api/v1/admin/accounts/<id>/reconnect`
- [x] `POST /admin/accounts/<id>/roles` → `PUT /api/v1/admin/accounts/<id>/roles`
- `GET /admin/accounts/<id>/archived-avatar`, `/admin/accounts/<id>/data-export` (download) -- stay

### 4.3 Reviews
- [ ] `GET /admin/reviews` (page)
- [ ] `POST /admin/reviews/name-changes/<id>/approve|reject` (action)
- [ ] `POST /admin/reviews/pictures/<id>/approve|reject` (action)

### 4.4 Teams (the admins' part)
- [ ] `GET|POST /admin/teams` (page + action: the list, creating)
- [ ] `GET|POST /admin/teams/new` (page + action)
- [ ] `GET|POST /admin/teams/<slug>` (page + action: the admins' settings)
- [ ] `POST /admin/teams/<slug>/archive` (action)
- [ ] `POST /admin/teams/<slug>/roles`, `/roles/revoke` (action)

### 4.5 Money
- [ ] `GET /admin/money` (page)

### 4.6 Logs
- [ ] `GET /admin/logs` (page)

### 4.7 Legal Texts
- [ ] `GET /admin/legal` (page)
- [ ] `POST /admin/legal` (upload a draft, answers its PDF) → API upload answering the PDF
- [ ] `POST /admin/legal/pdfs/remake` (already JSON) → API
- `GET /admin/legal/template`, `/admin/legal/waiting.pdf` (download) -- stay

### 4.8 Settings
- [ ] `GET|POST /admin/settings` (page + action: every section's save)
- [ ] `POST /admin/settings/mail-accounts` (action: add or change)
- [ ] `POST /admin/settings/mail-accounts/<id>/delete` (action)
- [ ] `POST /admin/settings/mail-accounts/<id>/test-connection` (action)
- [ ] `POST /admin/settings/mail-accounts/import` (action, upload)
- [ ] `POST /admin/settings/send-test-email` (action)
- [ ] `POST /admin/settings/test-forum-connection` (action)
- [ ] `POST /admin/system-update`, `GET /admin/system-update/status` (action, already JSON) → API
- [ ] `POST /admin/backup`, `GET /admin/backup/status` (action, already JSON) → API
- [ ] `POST /admin/backup/files/<name>/delete` (action)
- [ ] `POST /admin/forum-tasks/retry` (action; from the health report)
- [ ] `POST /admin/undelivered-emails/<job>/<retry|dismiss>` (action; from the health report)
- [ ] `GET /admin/background-jobs/checklist`, `POST .../again`, `.../dismiss`, `POST /admin/background-jobs/resume` (already JSON / action) → API
- `POST /admin/settings/mail-accounts/export` (download) → becomes a GET download
- `GET /admin/backup/files/<name>` (download) -- stays

## Step 5 -- Teams area

- [ ] `GET /teams` (page) → overview
- [ ] `GET /teams/<slug>` (page) → the team's page for its members
- [ ] `GET /teams/<slug>/about` (page) → join page, with the rules dialog
- [ ] `GET /teams/<slug>/rules[/<language>[/<version>]]` (page; link in emails) → rules page, also shown in the dialog
- [ ] `POST /teams/<slug>/join` (action)
- [ ] `POST /teams/<slug>/pay` (out: Stripe Checkout)
- [ ] `GET|POST /teams/<slug>/leave` (page + action)
- [ ] `POST /teams/<slug>/stay`, `/withdraw` (action)
- [ ] `GET /teams/<slug>/manage` (page) → management, its sections
- [ ] `POST /teams/<slug>/manage/settings` (action: each section's save)
- [ ] `POST /teams/<slug>/manage/memberships/<id>/approve|invite|reject|remove` (action)
- [ ] `GET /teams/<slug>/manage/people/<user>` (page) → a person, with notes
- [ ] `POST /teams/<slug>/manage/people/<user>/notes` (action)
- [ ] `POST /teams/<slug>/manage/treasurer`, `/treasurer/remove` (action)
- [ ] `GET /teams/<slug>/manage/access-list` (page), `POST .../send` (action)
- [ ] `GET /teams/<slug>/money` (page)
- [ ] `POST /teams/<slug>/money/bank`, `/money/payouts` (action)
- `GET /teams/<slug>/manage/export.csv`, `/teams/<slug>/money.csv` (download) -- stay

## Step 6 -- My Account

- [ ] `GET /account` (page today by redirect; link in emails) → My Account
- [ ] `POST /account/profile` (action)
- [ ] `POST /account/identity-request`, `/identity-request/<id>/cancel` (action)
- [ ] `POST /account/resend-verification`, `/resend-work-verification` (action)
- [ ] `POST /account/billing` (out: Stripe billing portal)
- [ ] `POST /account/resume-payment`, `/account/rejoin` (out: Stripe Checkout)
- [ ] `GET|POST /account/create-membership` (page + out: membership for an account without one)
- [ ] `POST /account/delete` (action: asks for confirmation by email)
- [ ] `GET|POST /account/delete/<token>` (page + action; link in emails)
- [ ] `GET|POST /change-password` (page + action) → part of My Account
- [ ] `GET /forum` (page; link in emails) → the forum card's page; *Open forum* stays a link out
- [ ] `POST /forum/avatar` (action, upload) → API upload, with the cropper
- `GET /account/data-export` (download) -- stays

## Step 7 -- Public pages and signing in

- [ ] `GET /` (page) → the new home page (association information)
- [ ] `GET /join` (page) → signup on its own page
- [ ] `POST /process-membership` (out: signup, then Stripe Checkout)
- [ ] `GET|POST /register` (action today)
- [ ] `GET /thank-you`, `GET /cancel` (page; Stripe's return addresses) → pages
- [ ] `GET|POST /login` (page + action; also the forum's sign-in return) → login page, `POST /api/v1/session` to sign in
- [ ] `POST /logout` (action) → `DELETE /api/v1/session`
- [ ] `GET|POST /forgot-password` (page + action)
- [ ] `GET|POST /reset-password/<token>` (page + action; link in emails)
- [ ] `GET /verify-email/<token>`, `/verify-work-email/<token>` (link in emails) → Flask confirms and redirects to a page, as today
- [ ] `GET /legal` (page) → list of legal texts
- [ ] `GET /legal/<slug>[/<language>[/<version>]]` (page) → a legal text, also shown in the dialog over the signup form
