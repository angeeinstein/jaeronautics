# Legal Notes: What the Portal Does

Facts for the legal texts — privacy statement, terms, statutes, rules of
procedure, impressum — collected from the code. **Not a legal text and not
legal advice:** it says what the portal does, so whoever writes or checks
those texts does not have to read the code.

Keep it current: whenever a change collects new data, sends data somewhere
new, keeps it longer or shorter, or changes how money is taken, update this
file in the same commit. **To-do** marks what is still to be decided or
written; it is collected in section 10 so nothing is forgotten, without being
urgent.

*Last reviewed: October 2026.*

---

## 1. Who and what

- Operator: the association (Verein) Joanneum Aeronautics. The portal is its
  membership administration: signing up, paying the membership fee, the
  member's account, access to the members' forum, teams and their fees.
- Hosted on a server of the association in Microsoft Azure, region West
  Europe (Netherlands). Microsoft's data processing terms are part of Azure's
  standard terms.
- The forum (Discourse) is a separate system, signed into through the portal.
  **To-do:** note where it is hosted.
- Language of the portal: English only.

## 2. Personal data the portal holds

**Account**
- Email address (the private one; also the login), password (stored only as a
  hash — scrypt, Werkzeug's default), email-verified date.
- Roles (admin, treasurer, …) for the few people who run the portal.
- Disabled flag with reason, set by an admin.

**Member profile** (entered at signup)
- Salutation, title, first and last name.
- Address: street, house number, postcode, city, country.
- Private phone (required), private email (required).
- Work/university phone and email (optional). The university email is
  verified by a link and is how a student shows they are one.
- Member category (student, alumni, staff or lecturer, company or partner,
  honorary) and year group (cohort); required for students.
- That the legal texts were accepted: a yes/no, and since October 2026 which
  version of each text (statutes, rules of procedure, membership terms,
  privacy policy -- by version day) and when. For
  those who signed up before, the signup date stands for the version (the
  texts in force that day). The texts and all their versions are in the
  repository, `legal/`, and readable at `/legal`.

**Membership and payment**
- Membership start, end, renewal date, status, whether cancelled for the end
  of the period, one record per paid period (with the Stripe invoice ID).
- Stripe customer, subscription and checkout IDs. **No card or bank account
  numbers are stored in the portal** — they are entered on Stripe's page and
  stay with Stripe.
- Team fees: one record per payment (amount, refunded amount, what period it
  pays for, Stripe IDs).

**Profile changes**
- Name, category and year group can only be changed by request; the request,
  an admin's decision and an optional note to the member are kept.

**Forum**
- Forum username, the forum account link, the profile picture (uploaded,
  reviewed by an admin, then sent to the forum). Pictures are files on the
  server; an approved picture is reachable without login under an unguessable
  link, so the forum can fetch it.
- For people from the old forum: their old username, display name, picture
  and year group, imported to reconnect them; old posts were imported into
  the forum archive.

**Teams**
- Applications (with the text written), memberships, start/end and the reason
  it ended, roles (lead, treasurer), a leaving message to the leads.
- A team's own rules, if it has any: accepted with a tick when applying or
  joining; the membership keeps when, and which version (the day the rules
  last changed). Each change to the rules is logged with the old and new text.
  Members already in are not asked to accept a new version.
- **Leads' notes about a person** — never shown to that person. See 10.
- Team bank details (account holder, IBAN, BIC) — the team's account, which
  may be a private person's. Every change logged with old and new value.
- The last access list sent (names and university emails), to mark changes
  next time.

**Records the system keeps**
- Audit log: who did what, when (logins, admin actions, payments, changes),
  with before/after values where something was changed. No IP addresses.
- Emails sent and queued (recipient, type, status, errors).
- Stripe webhook events processed (IDs, for not processing one twice).

## 3. Where data goes

| Recipient | What | Why |
|---|---|---|
| **Stripe** (Stripe Payments Europe, Ireland; group in the US) | Email address; the member enters name, card or IBAN on Stripe's page. Internal IDs as metadata. | Taking the membership and team fees, receipts, renewal emails, SEPA mandates. Stripe keeps its records under its own retention duties. |
| **Discourse forum** | Email, forum username, full name, profile picture, year group, groups (member, cohort, teams, staff), admin/moderator flag. | Single sign-on and access to the members' area. Name and picture are visible to other forum members. |
| **Email providers** (SMTP, set per sender in the admin settings): IONOS (Germany) for some senders, Brevo (France) for others. Moving to Brevo entirely, perhaps to Microsoft later — update this row when it changes. | Recipient address, name, content of the email. | Sending the portal's emails. |
| **Room access recipients** (e.g. FH Joanneum facility staff), per team | Names and university emails of the team's current members, who joined and who left since the last list. | Access to the team's rooms. Covered in the privacy statement; no separate notice when joining a team (decided). |
| **Team leads** (fellow members) | Applicants' and members' name, university and private email, phone, cohort, member since, paid until (page and CSV export); notes. | Running the team. |
| **Team treasurer / association treasurer** | Who paid what for the team, the team's bank details. | Passing the money on to the team. |
| **Admins** | Everything above. | Running the association. |
| **Cloudflare** (Cloudflare, Inc., US; EU data processing addendum) | Every request to the portal passes through Cloudflare's network (Cloudflare Tunnel): visitor IP address, the pages and form contents in transit, which Cloudflare decrypts and re-encrypts. Possibly Web Analytics (see below). | Reaching the server without opening it to the internet; protection from attacks. **To-do:** name it in the privacy statement. |

No advertising, no tracking pixels, and the portal itself adds no
analytics. Its pages load nothing from other servers (fonts, scripts and
styles are served by the portal itself) -- except that the security policy
allows Cloudflare's Web Analytics script (`static.cloudflareinsights.com`),
which Cloudflare inserts into pages if Web Analytics is switched on for the
site in the Cloudflare dashboard. It counts visits without cookies.
**To-do:** check in the Cloudflare dashboard whether it is on; if so, name it
in the privacy statement, or switch it off and remove it from the policy
(`deploy/nginx/aeronautics.conf`). Stripe's payment page is Stripe's own,
under Stripe's privacy policy.

## 4. Cookies

- One session cookie, set by the portal: keeps a person signed in, holds the
  form protection token. Secure, HttpOnly, SameSite=Lax, valid 7 days.
- No other cookies from the portal. Stripe's payment page sets its own.
  Cloudflare may set its own security cookies (e.g. `__cf_bm`, for telling
  bots from people), which are needed for the service too.
- Needed for the service, so no consent banner is needed; the privacy
  statement should still describe the cookie.

## 5. How long data is kept

| What | How long |
|---|---|
| Account and profile | While the membership lasts, and after it ends until the person or an admin erases the account. **To-do:** decide whether former members' accounts are erased after some time. |
| Payment records (membership periods, team payments) | 7 years (§ 132 BAO). On erasure they are kept without the person's identity. |
| Stripe's records | At Stripe, under its retention duties; not erased with the account. The deletion page says so. |
| Signups never paid for | 90 days after the signup or the last attempt to pay. The person is emailed 7 days before; removal waits until that email has been out 7 days. Removed completely (account, profile, its log and email records), not anonymised: there is nothing the books need. Only bare signups — anything with a role, team, forum account, picture, change request, period, payment or Stripe subscription is left for an admin. |
| Audit log | Kept indefinitely (setting `AUDIT_LOG_RETENTION_DAYS`, default 0 = forever). On erasure, entries about the person lose their before/after values. **To-do:** decide a period. |
| Email and notification records | 1 year (monthly clean-up). |
| Leads' notes, team bank details history | Notes: deleted with the account. Bank-detail changes: in the audit log. |
| Encrypted backups (Backup & Restore page) | The last 5, on the server; AES-256 encrypted with a passphrase that is not stored. |
| Database copies made by each update | In `/var/backups` on the same server, the newest few kept; protected like the database itself. |
| Web server log (nginx, IP addresses) | System default rotation (typically 14 days). |
| Login rate limits | Short-lived counters; the email address is hashed. |

## 6. People's rights, as built

- **Access and portability:** "Download my data" on the account page — a JSON
  file with profile, membership periods, forum, profile change requests, teams,
  roles, payments, and the leads' notes about the person (with team and date,
  not which lead wrote them).
- **Rectification:** contact details directly on the account page; name,
  category and year group by request to the admins.
- **Erasure:** the member requests it by an emailed link, or an admin does it.
  Erasure cancels running subscriptions first, overwrites the person's data,
  keeps the anonymous payment records, removes the forum link and picture,
  deletes leads' notes, and blanks the person's details in the email history.
- **Objection / restriction:** no built-in function; by contacting the
  association.

## 7. Emails the portal sends

- Account: email verification, password reset, account deletion link,
  university email verification, notice that an unpaid signup is removed.
- Membership: welcome email, profile change and picture approved/rejected,
  fee change (14 days before the renewal at the new price). Nothing about
  failed payments or the membership ending — that is left to Stripe.
- Teams: application received (to leads), invitation, approved, payment due,
  reminder 14 days before a once-per-period fee runs out, membership ended,
  fee changes, ends with the association membership, member leaving/left
  (to leads), access list (to room access recipients), bank details changed
  (to the association treasurer).
- Admins: a digest of things waiting for review.
- **Stripe sends** receipts, renewal reminders and failed-payment emails —
  deliberately left to Stripe, as the more reliable sender for anything
  before a charge. Check in Stripe (Settings → Billing → Subscriptions and
  emails) that "upcoming renewal" and "failed payment" emails are on, on the
  live and the test account, and note how many days before a renewal the
  reminder goes out (the terms should say so).

## 8. Money: what the terms need to say

**Membership fee**
- One fee per calendar year, the same for every category (currently €15).
- Joining during the year: the rest of the year, pro rata, paid at once.
  Joining from 1 October: free until 31 December.
- Renews automatically every 1 January, charged to the card or by SEPA debit
  (the mandate is given on Stripe's page). Stripe emails a reminder before.
- Cancelling: any time on the account page (Stripe's billing page); the
  membership then ends on 31 December, no renewal. No refund for the
  current year.
- A failed renewal: 21 days' grace with access, then the membership ends;
  rejoining by paying again.
- A fee change applies from the next renewal; each member is emailed 14 days
  before it. Cancelling before then avoids it.
- A chargeback (disputed payment) that is lost ends the paid period.
- Erasing the account cancels the subscription; no refund.

**Team fees** (set per team; free, a subscription, or once per period)
- The full fee for the period under way, also when joining late.
- Subscription: renews at each period start; Stripe sends renewal emails.
  Once per period: no automatic renewal; a reminder 14 days before the end;
  paying in advance continues without a gap; unpaid, the membership ends on
  the last day.
- Leaving: runs to the end of what is paid; no refund.
- Removal by a lead, the association membership ending, or erasure: the
  subscription stops at once; no refund.
- Rejoining within six months after not paying: by paying, without applying.
- What members pay goes in full to the team; the association pays Stripe's
  fees.

## 9. The statutes and rules of procedure vs. the portal

The texts on the portal's `/legal` page (files in `legal/`) are Statutes
Rev 1 (17.03.2019) and Rules of Procedure Rev 3 (26.02.2020). Where they no longer match:

- **Admission** (Statutes § 5 (2)): "the board decides on admission". The
  portal admits on payment, without a board decision — more relaxed than the
  text, which is intended. **To-do:** reword so admission is open, with the
  board reserving the right to decide on admission or make it stricter.
- **Leaving** (Statutes § 6 (2)): written notice by 24 December, by post
  ("postmark"). The portal: cancel online any time before 31 December —
  easier than the text, which is intended. **To-do:** reword to match
  (cancellation online, effective at the end of the paid year).
- **Fees** (Rules § 2): per section — €15 study, €20 alumni/supporter, €50
  company, free for staff — collected once a year by the treasurer by direct
  debit. The portal: one fee for all, by card or SEPA through Stripe, pro rata
  in the first year, free from October. **To-do:** change to the current
  €15 for everyone, and how it is collected.
- **Every member in a section or team** (Rules § 1 (2)). In the portal,
  teams are optional.
- **Team membership needs proof of insurance** (Statutes § 5 (4), Rules § 2
  (3)). The portal does not ask for it. **To-do:** decide and reword.
- **Team fees** decided by the extended board (Rules § 3 (1)). The portal
  takes them by Stripe, per team, and has a team treasurer role and payouts
  to teams that the rules do not mention.
- **Member categories:** statutes know ordinary, extraordinary and honorary
  members; the portal knows student, alumni, staff, company/partner,
  honorary. Who votes follows from the statutes, not the portal.
- **Change of status** (Statutes § 7): automatic move to alumni after
  studying, change by written request. The portal: by a change request,
  decided by an admin.
- **Invitations to the general meeting** (Statutes § 9 (3)): by email to the
  address given — the portal holds that address (private email).
- The signup checkbox names and links the statutes, rules of procedure,
  membership terms and privacy policy, which are accepted together. Since
  October 2026 all four are real texts (`legal/`).
- **The fee, twice (open):** the rules of procedure Rev 4 (02.03.2026) § 2
  still give per-section fees (EUR 10 study, EUR 20 alumni/supporter, EUR 50
  company), collected yearly by the treasurer by direct debit; the membership
  terms § 7 and § 9 say EUR 15 a calendar year, through Stripe. Both are
  accepted at signup. **To-do:** align them (a new rules version, or the
  terms).

## 10. To-do

Collected so nothing is forgotten; none of it is urgent.

**Legal texts**
- [x] Membership terms and privacy policy written (04.10.2026, German).
- [x] The footer's Impressum, Privacy and Statutes lead to the portal's own
      texts (October 2026).
- [x] English translations of the membership terms and privacy policy
      (04.10.2026). Others, if wanted: `legal/<text>/en/<same day>.md`.
- [x] Give the legal texts a version date: done, versions are files by
      date and the versions accepted are kept with the member.
- [x] Webshop and event terms and the impressum (04.10.2026).
- [ ] Statutes: admission open, board may decide or make it stricter.
- [ ] Statutes: leaving by online cancellation, at the end of the paid year.
- [ ] Rules of procedure: the fee is €15 for everyone, collected through
      the portal.
- [ ] Statutes / rules: team insurance — decide and reword.
- [ ] Impressum: name the portal (section 11).

**Decisions**
- [ ] Whether members already in a team must accept changed team rules.
- [ ] How long the audit log is kept.
- [ ] Whether former members' accounts are erased after some time.

**Portal**
- [ ] Check Stripe's "upcoming renewal" and "failed payment" emails are on
      (live and test), and note the reminder's lead time for the terms.
- [ ] Note where the forum is hosted.
- [ ] Check whether Cloudflare Web Analytics is on (section 3).

## 11. For the impressum

Facts the impressum needs (ECG § 5, MedienG § 25), to fill in from the
association's register entry: name, ZVR number, seat and address, contact
email, board members (Obmann/Obfrau), purpose of the association. The
portal's footer currently links to the main website's impressum, which can
cover the portal if it names it.
