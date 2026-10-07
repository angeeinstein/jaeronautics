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
- [x] `GET /admin/reviews` (page) → `GET /api/v1/admin/reviews`; past decisions `GET /api/v1/admin/reviews/history`
- [x] `POST /admin/reviews/name-changes/<id>/approve|reject` → `POST /api/v1/admin/reviews/name-changes/<id>/approve|reject`
- [x] `POST /admin/reviews/pictures/<id>/approve|reject` → `POST /api/v1/admin/reviews/pictures/<id>/approve|reject`

### 4.4 Teams (the admins' part)
- [x] `GET|POST /admin/teams` (page + action: the list, creating) → `GET /api/v1/admin/teams`, `POST /api/v1/admin/teams`; the switch and names `PUT /api/v1/admin/team-settings`
- [x] `GET|POST /admin/teams/new` (page + action) → the page; creating as above
- [x] `GET|POST /admin/teams/<slug>` (page + action: the admins' settings) → `GET|PUT /api/v1/admin/teams/<slug>`; the fee `PUT /api/v1/admin/teams/<slug>/fee`
- [x] `POST /admin/teams/<slug>/archive` (action) → `PUT /api/v1/admin/teams/<slug>/archived`
- [x] `POST /admin/teams/<slug>/roles`, `/roles/revoke` (action) → `POST /api/v1/admin/teams/<slug>/roles`, `/roles/revoke`

### 4.5 Money
- [x] `GET /admin/money` (page) → `GET /api/v1/admin/money`; the check against Stripe `GET /api/v1/admin/money/overview?since=&until=` (the page keeps `?check=1&since=&until=`)
- [x] new: `GET /admin/money/<slug>` (page: a team's money, the association's side) → `GET /api/v1/admin/money/<slug>`; `POST .../transfers`, `PUT .../bank`
- `GET /admin/money/<slug>/transfer-code.svg?amount=&reference=` (image: the GiroCode for the amount and reference typed) -- stays

### 4.6 Logs
- [x] `GET /admin/logs` (page) → `GET /api/v1/admin/logs?q=&category=&user=&page=`; old links (`?category=all`) still work

### 4.7 Legal Texts
- [x] `GET /admin/legal` (page) → `GET /api/v1/admin/legal`
- [x] `POST /admin/legal` (upload a draft, answers its PDF) → `POST /api/v1/admin/legal/preview` (multipart, answers the PDF; what keeps it from being laid out is a 400 with `details.problems`)
- [x] `POST /admin/legal/pdfs/remake` (already JSON) → `POST /api/v1/admin/legal/pdfs/forget`, then `POST /api/v1/admin/legal/pdfs/make {key}` per PDF
- `GET /admin/legal/template`, `/admin/legal/waiting.pdf` (download) -- stay

### 4.8 Settings
- [x] `GET|POST /admin/settings` (page + action: every section's save) -- the save is gone: each section has a page of its own, `/admin/settings/<section>`; `/admin/settings` opens the first, and an old link to a tab (`#settings-forum`, `#backup-restore`) that part's page
  - [x] General → `GET|PUT /api/v1/admin/settings/general`
  - [x] Notifications → `GET|PUT /api/v1/admin/settings/notifications`
  - [x] Membership fee → `GET|PUT /api/v1/admin/settings/billing`
  - [x] Forum → `GET|PUT /api/v1/admin/settings/forum`
  - [x] Mail accounts → `GET /api/v1/admin/settings/mail`
  - [x] Test email → `GET /api/v1/admin/settings/test-email`
  - [x] Maintenance, split in three: System health → `GET /api/v1/admin/settings/health`; Updates → `GET /api/v1/admin/settings/updates`; Backup and restore → `GET /api/v1/admin/settings/backup`
- [x] `POST /admin/settings/mail-accounts` (action: add or change) → `POST /api/v1/admin/settings/mail/accounts`, `PUT .../accounts/<id>`
- [x] `POST /admin/settings/mail-accounts/<id>/delete` (action) → `DELETE /api/v1/admin/settings/mail/accounts/<id>`
- [x] `POST /admin/settings/mail-accounts/<id>/test-connection` (action) → `POST /api/v1/admin/settings/mail/accounts/<id>/test`
- [x] `POST /admin/settings/mail-accounts/import` (action, upload) → `POST /api/v1/admin/settings/mail/import`
- [x] `POST /admin/settings/mail-accounts/export` (download) → `POST /api/v1/admin/settings/mail/export`: asks for the current password, answers the file
- [x] `POST /admin/settings/send-test-email` (action) → `POST /api/v1/admin/settings/test-email`
- [x] `POST /admin/settings/test-forum-connection` (action) → `POST /api/v1/admin/settings/forum/test`
- [x] `POST /admin/system-update` (action) → `POST /api/v1/admin/settings/updates`; `GET /admin/system-update/status` (already JSON) stays for a Maintenance page opened before the update that brings the new front end, which asks there until the update is done
- [x] `POST /admin/backup`, `GET /admin/backup/status` (action, already JSON) → `POST /api/v1/admin/settings/backup`; the status is part of the page's answer
- [x] `POST /admin/backup/files/<name>/delete` (action) → `DELETE /api/v1/admin/settings/backup/files/<name>`
- [x] `POST /admin/forum-tasks/retry` (action; from the health report) → `POST /api/v1/admin/settings/health/forum-tasks/retry`
- [x] `POST /admin/undelivered-emails/<job>/<retry|dismiss>` (action; from the health report) → `POST /api/v1/admin/settings/health/undelivered/<job>/retry|dismiss`
- [x] `GET /admin/background-jobs/checklist`, `POST .../again`, `.../dismiss`, `POST /admin/background-jobs/resume` (already JSON / action) → `GET /api/v1/admin/settings/backup/checklist`, `POST .../checklist/again`, `.../checklist/dismiss`, `POST /api/v1/admin/settings/backup/resume`
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
- [ ] `POST /teams/<slug>/money/bank` (action); `/money/payouts` is gone: transfers are recorded at 4.5
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
