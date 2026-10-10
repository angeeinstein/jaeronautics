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
- **Answering:** by normal email. The email to the admins has Reply-To set
  to the sender. A reply from inside the portal, as formatted HTML, "would be
  nice" -- later (ideas for later in `docs/todo.md`).
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

## Order of building

1. **Contact form, Admin › Messages, message the team leads.** These replace
   the unmonitored mailbox.
2. **Announcements:**
   - the sender setting and pacing;
   - unsubscribe and subscribe again;
   - the general assembly's invitation.
3. **Team mailings.**
4. **Privacy policy draft and docs.**
