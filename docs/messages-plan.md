# Messages: contact form, announcements, team mailings

**Status:** built 2026-10-09 (`claude/messages`). Running it:
`docs/maintenance.md`, "Messages, announcements and team mailings".

**One change from the proposal:** no "voting members" choice of recipients.
Which of the portal's kinds of member are the statutes' ordinary members is
not decided (`docs/legal-notes.md`, section 9). The general assembly's
invitation goes to all members anyway, as § 10 (3) asks.

## What the maintainer asked for

> "a better contact feature or help feature. Currently, there is a mail to
> link in the footer … goes to our office email address that I don't really
> monitor … a real contact form … send an email to all members or a team lead
> could send an email to all team members … an invitation for the general
> assembly what we are already legally required to do … for the team leader
> … some newsletter or some information about the team … and maybe also the
> other way around, that there is a function to message the team leader or
> message the admin of the site, no matter who this actually is."

Then, to the proposal: "I like all of your ideas. Please implement them just
like you proposed."

## Decided (2026-10-09)

**Contact form** (`/contact`, the footer's *Contact*):
- **Topics:**
  - Membership & payment;
  - Account & forum;
  - Teams;
  - Problem with the portal;
  - Something else.
- **Recipients:** every topic goes to **all admins and super admins**, not
  the treasurer ("route this to all admins or super admins, but not to the
  treasurer").
- **Who can use it:**
  - signed in, the form is filled in and the account is attached (member
    number, membership status, the page they came from);
  - visitors give a name and an email address.
- **Answering:** from the portal since 2026-10-16 (below, "Answering from
  the portal"). The email to the admins still has Reply-To set to the
  sender, so answering it by email still works.
- **Kept** under Admin › Messages, marked *done* when answered, deleted a
  year after.
- **Spam:**
  - rate limits;
  - a hidden field;
  - a form sent faster than a person can type is refused.

  No captcha service: the portal loads nothing from elsewhere.

**Message the team leads:**
- A form on each team's page, for anybody signed in.
- It goes to the team's leads; their addresses stay hidden until they answer.
- It is kept for the team: the leads see it on the team's management page,
  the admins do not.

**Announcements** (Admin › Announcements):
- **Recipients:**
  - all active members;
  - one or more kinds of member;
  - teams (their active members);
  - all team leads.

  Only **active, paying members** ("really active and paying members get
  the emails"), never applicants.
- **Sending:**
  - a test to oneself, then send;
  - paced through a queue, with progress shown;
  - a record of what was sent, when, to how many, and which addresses
    failed.
- **Two kinds:**
  - **News** can be switched off: an unsubscribe link in every news email's
    footer (and the one-click unsubscribe header mail programs show), and a
    way back -- "subscribe again" on that page and a switch in My Account.
  - **Notices** cannot: the general assembly's invitation and the like.
- **The general assembly's invitation:**
  - date, time, place and agenda;
  - the invitation written from them;
  - a warning under two weeks.

  Statutes § 10 (3): all members, at least two weeks ahead, by email to the
  address they gave, with the agenda.
- **Sender:** selectable -- which of the mail accounts sends announcements
  and team mailings, the same as the other emails or a different provider.
  How many per hour and per day, for the provider's limits.

**Team mailings** (the team's management page, *Write to the team*):
- **Who may send:** a new team permission. Leads have it by default.
- **Recipients:** the team's active members only -- not applicants.
- **No unsubscribing:** "a team lead should be able to contact the team
  members" -- invitations to team events rather than news.
- **Sender:** the announcement sender, shown as the team's name, with
  Reply-To going to the lead who wrote.

**Across all:**
- Text with simple formatting (Markdown), sent as HTML and plain text in the
  portal's email layout.
- No member-to-member messages: the forum is for that.
- **Privacy policy:** contact messages (what, who sees them, a year), team
  messages, announcements and team mailings (recipients, the record,
  unsubscribing). Added to the 2026-10-08 draft, which is not in force yet.

## Answering from the portal (agreed and built 2026-10-15/16)

> "it's possible to answer a message from within the system … as I'm using
> my private email address here, as a login, I would also get this admin
> notification on my private email and when I reply there, I would reply to
> this person with my private email. And this doesn't look that good and this
> doesn't fit our professional attitude here."

- **Where:** Admin › Messages, *Reply* on a contact-form message: written
  there (Markdown: bold, lists, links), sent as HTML and plain text in the
  portal's email layout, the person's message quoted below it. The message
  is then *done*; the answer is kept under it -- who wrote it, when, and
  whether it went out (one that could not is tried again by the
  notification timer and says so).
- **Only the contact form's:** "when a team leader is contacted, I don't want
  them to reply from the same support email address." Messages to a team's
  leads keep *Answer by email*.
- **From which account:** Settings › Email, *Senders*, *Replies to contact
  messages* -- a mail account of its own (a *support* address the
  maintainer adds on the *Accounts* tab), or, left empty, the default.
- **The person's answer:** a reply goes out with no other Reply-To, so an
  answer to it lands in that account's mailbox, read in a mail program ("I
  could just configure this mailbox in Outlook"). When that account is a
  Microsoft 365 mailbox, the answer also comes back into the thread here and
  the message is open again (agreed 2026-10-16:
  `docs/microsoft-mail-plan.md`, "Replies").
- **A copy:** a setting beside the account -- each reply also goes, as a
  blind copy, to the account's own address, so the whole conversation is in
  that mailbox ("you could just make this a setting in the settings page
  where I set the support email sender").
- **Later, if wanted:** a separate Reply-To address.
- **Kept and deleted with the message**; in the sender's data export with
  their message. The privacy policy draft (2026-10-08) says so.
- Code: `services/messages.py` (`reply`), `db_models.ContactReply`,
  `api/messages.py` (`POST /admin/messages/<id>/replies`),
  `services/mail_senders.py` (the two settings),
  `frontend/src/components/MessageList.tsx`,
  `frontend/src/pages/admin/settings/Email.tsx`.

## Order of building

1. **Contact form, Admin › Messages, message the team leads.** These replace
   the unmonitored mailbox.
2. **Announcements:**
   - the sender setting and pacing;
   - unsubscribe and subscribe again;
   - the general assembly's invitation.
3. **Team mailings.**
4. **Privacy policy draft and docs.**
