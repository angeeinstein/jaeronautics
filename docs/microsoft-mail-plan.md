# Email through Microsoft 365

Agreed 2026-10-16. The association's mail is at Microsoft already; the IONOS
SMTP accounts were kept only because they sign in with a plain password, and
IONOS is to go. Brevo (300 emails a day, free) is the stand-in.

## What was decided

- **Microsoft 365 accounts beside SMTP accounts.** Settings › Email, Accounts
  holds both kinds side by side, as many as wanted; each is chosen as a sender
  (the default, welcome, notifications, mailings, replies) like any other.
  Nothing that uses an account knows which kind it is.
- **Shared mailboxes, through Microsoft Graph.** One per purpose -- `support@`
  and `noreply@` to begin with, more later. A shared mailbox needs no licence
  and costs nothing; no Azure service is used (Azure Communication Services
  would cost per email -- ruled out: "nothing that costs us anything, not even
  credits").
- **The portal signs in as an app** (an app registration in Entra ID, OAuth
  client credentials): no person signs in, nothing expires for want of use.
- **With a certificate, not a client secret.** The portal makes the key pair;
  the private key never leaves the server; the admin uploads the certificate
  to the app registration. Valid three years; System health warns 30 days
  before, and a new one can be made and uploaded while the old one still
  works -- the portal switches once the new one signs in.
- **Only the mailboxes listed may be used** ("RBAC for Applications" in
  Exchange Online), not every mailbox of the organisation: the app gets no
  mail permission in Entra itself.
- **Replies come back into the portal.** When someone answers an answer
  written in Admin › Messages, their email lands in the thread there and the
  message is open again. Only those: "I don't think we need to process all
  incoming mail for now" -- everything else stays in Outlook, untouched.
- **SMTP stays** for Brevo or anything else.
- The step-by-step setup: `docs/microsoft-mail-setup.md`.

## How it works

- **Sending** (`services/microsoft_mail.py`, called from `mail_utils.send_mail`):
  the same email the portal builds for SMTP -- MIME, with the plain-text part,
  the logo, attached PDFs, List-Unsubscribe -- handed to Graph whole
  (`POST /users/{mailbox}/sendMail`, MIME in base64). It is kept in the
  mailbox's Sent Items like one sent from Outlook. Blind copies go in a `Bcc`
  header, which Exchange takes as recipients and removes.
- **The token**: a JWT signed with the certificate's key, exchanged at
  `login.microsoftonline.com/<tenant>/oauth2/v2.0/token` for an access token
  (scope `https://graph.microsoft.com/.default`), valid about an hour, kept in
  memory and fetched again when it runs out.
- **Limits** (Exchange Online, per mailbox): 10,000 recipients a day, 30
  messages a minute, 500 recipients per email. The 2,000-a-day limit for
  outside recipients Microsoft had announced was cancelled in January 2026.
  Mailings are paced by their own setting (Settings › Email, What goes out).
  Microsoft asks that bulk commercial email (newsletters) go through
  specialist providers; notices to the association's own members are not
  that, but for large, frequent mailings an SMTP relay can stay the
  mailings' sender.
- **Replies**:
  - An answer from Admin › Messages carries a Message-ID the portal makes and
    keeps (`contact_replies.email_message_id`). A reply to it names that ID in
    `In-Reply-To` / `References`; that is how it is recognised -- not by the
    sender's address (people answer from another one) and not by the subject.
    The ID is saved before the answer goes, so a notification quicker than
    the sending request still knows it; an email from one of the portal's own
    addresses (the answer's copy, or an admin answering in Outlook) is never
    taken as the sender's reply; and each incoming ID is stored once
    (unique), even when the notification and the safety net below take it at
    the same moment.
  - Graph tells the portal at once when a message arrives in the replies
    account's inbox (a change-notification subscription; the URL is
    `/webhooks/microsoft-mail`). The portal answers at once and reads the
    message in the background: its headers, then -- when it is a reply --
    only the new part (`uniqueBody`, as text), which is stored as an
    incoming reply (`contact_replies.incoming`). The message is open again.
  - The subscription lasts a few days and is renewed by the
    `notifications` job (every 15 minutes) when it is close to running out.
    The same job looks through what arrived since its last look, as a safety
    net for a notification missed (the server down, a deploy).
  - Attachments stay in the mailbox; the reply says it has some.
  - Only the account chosen for replies is watched, and only if it is a
    Microsoft account.
- **Kept**: the certificate and its key in the database (in the backup, like
  the SMTP passwords); incoming replies with their message, deleted with it,
  in the sender's data export.

## A backup account (decided 2026-10-18)

"If Microsoft authentication doesn't work or is down, or if the certificate
has expired and nobody has renewed it, this backup account could still work.
This would especially be helpful for things like password resets." The plan
is Microsoft 365 for everything, IONOS gone, Brevo (300 emails a day) kept
as the backup.

- **One** setting on Settings › Email, *Senders*: **Backup** -- not a second
  choice beside each kind of email. When the account an email goes out from
  cannot send it, the backup sends the same email. Empty: no backup, as now.
- Every kind of email uses it -- password resets, confirmations,
  notifications, welcome emails, replies to contact messages -- **except**
  announcements and team mailings: they wait and are tried again, as now,
  so a long outage does not spend the backup's daily allowance and leave
  password resets without. The test email does not use it either: it tests
  the account chosen.
- Not when the email itself is the problem: an address the server refuses
  goes nowhere else.
- From the backup's own address, with **Reply-To** the address it should
  have come from: a reply to an answer from Admin › Messages still reaches
  the replies' mailbox, and is recognised there (the Message-ID is the
  same).
- **System health** says it as a problem -- "noreply-microsoft cannot send
  (since ...): ...; the backup sends its emails" -- so it shows in the
  admin area until that account sends again.

## Open

- Whether admins should get an email when a reply arrives (now: it shows in
  Admin › Messages, and it is in the mailbox anyway).
