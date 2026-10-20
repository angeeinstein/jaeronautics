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

## Whole conversations, and team leads answering in the portal (decided 2026-10-18)

Tried on the test server with a few answers back and forth: "the reply that
is sent out by our portal … shows the original message that the person sent
in the contact form … even after some writing back and forth … and this
could be a bit confusing."

**The email that goes out quotes the whole conversation.** *Built 2026-10-20*
(`messages._quoted_conversation`, `QUOTED_MOST`; our answers are named after
the replies account's sender name).

- Newest first, each step under its own line ("On 18.10.2026 at 16:24,
  Anna Example wrote:"), as a mail program nests a thread. Built from what the
  portal keeps, not from what the person's mail program sent, so it stays
  clean.
- Capped at the last steps (about 10) in a long thread -- **but the contact
  form's message is always in it**, at the bottom, even when older steps
  are left out: "this message was the original question or problem. So
  everything in this conversation is around this original contact form
  message."
- Today: `messages._send_reply` passes `quoted_text=message.body` (always the
  first message); `templates/emails/contact_reply.html` shows one quote.

**An answer that comes back is shown without what it quotes.** *Built
2026-10-20* (`messages.split_quote`; the API gives the cut part as `quoted`).

- Their mail program quotes our email below their answer; the portal showed
  all of it (our logo text, "Support <…> schrieb am …", the copyright
  line). Graph's `uniqueBody` (asked for already, `mail_replies._text_of`)
  leaves the quote out for Outlook, not for Gmail.
- Cut at the first quote line: "On … wrote:", "Am … schrieb …:",
  "… schrieb am …:", "-----Original Message-----", Outlook's
  "From:/Sent:" block, lines starting with `>`.
- The whole text is kept; *Show quoted text* under the answer opens the
  rest, so a wrong cut loses nothing.

**Team leads answer a message to their team in the portal.** *Not built:
put off ("I don't want to work on this now").*

- Today a team's message is emailed to each lead's login address (usually
  private) with the sender as Reply-To; *Answer by email* opens their own
  mail program -- the sender then sees the lead's private address and the
  portal never sees the answer.
- **The team's mail account:** chosen by the admins in the team's settings
  (Admin › Teams, the team) from the mail accounts of Settings › Email --
  leads cannot add accounts, which hold credentials. For this association,
  each team's Microsoft 365 shared mailbox ("A team will always have its own
  mailbox"); optional all the same, for other associations.
- **Reply** on the team's Messages page, as in Admin › Messages: written
  there, sent from the team's account, the message then done, the same
  quoting as above. **Only the team's leads in force** answer (admins see
  the page as now).
- **Without a team account, as today (decided 2026-10-18):** *Answer by
  email* stays for such a team -- only a `mailto:` link
  (`MessageList.tsx`, `replyHref`) -- and goes as soon as the team has an
  account: "as long as no sender email account is configured for this team,
  this button exists and as soon as a sender account is present … this
  button goes away." The component already shows either *Reply* or *Answer
  by email* (`answersHere`).
- **The email to the leads** about a new message carries the sender as
  Reply-To today (`messages.deliver`), so a lead can answer from their mail
  program. For a team with an account: no Reply-To, only *Open in the
  portal* -- else a lead answers from their private address after all.
- **Answers come back** into the team's thread when the team's account is a
  Microsoft 365 mailbox: the portal watches that inbox too (today one inbox,
  the Replies account's: `mail_replies.keep_watching`; then that one and
  every team's). The leads get an email that an answer came, with a link;
  the message is open again.
- The team mailbox's *Sent Items* keeps every answer, so whoever opens the
  shared mailbox in Outlook sees the conversation too.
- With several team mailboxes the app's mail permissions grow: see the
  to-do item on narrowing them (a mail-enabled security group, later).

## Order of building

1. **Contact form, Admin › Messages, message the team leads.** These replace
   the unmonitored mailbox.
2. **Announcements:**
   - the sender setting and pacing;
   - unsubscribe and subscribe again;
   - the general assembly's invitation.
3. **Team mailings.**
4. **Privacy policy draft and docs.**
