# Setting up email through Microsoft 365

Step by step: the portal sends from shared mailboxes of the association's
Microsoft 365, and the replies to answers from Admin › Messages come back into
the portal. Why it is built this way: `docs/microsoft-mail-plan.md`.

It costs nothing: shared mailboxes need no licence, the app registration is
free, and no Azure service is used.

**What you need:** an account that is *Global Administrator* in the tenant (or
both *Exchange Administrator* and *Application Administrator*), the portal's
Settings › Email open in another tab, and -- for step 5 -- PowerShell on your
computer (Windows has it; on a Mac or Linux install
[PowerShell 7](https://learn.microsoft.com/powershell/scripting/install/installing-powershell)).

Takes about half an hour. Changes in step 5 can take up to two hours before
Microsoft honours them.

---

## 1. The shared mailboxes

In the **Microsoft 365 admin center** (admin.microsoft.com):

1. *Teams & groups* › *Shared mailboxes* › **Add a shared mailbox**.
2. Name `Support`, email `support@<your domain>`. **Save changes.**
3. Again for `No reply`, `noreply@<your domain>` -- and later any other one
   the portal should send from.
4. Optional: under the support mailbox's *Members*, add the people who should
   also read it in Outlook. The portal does not need this.

Leave sign-in blocked for these mailboxes (the default): nobody signs in to
them, the portal included.

## 2. The app registration

In the **Microsoft Entra admin center** (entra.microsoft.com):

1. *Identity* › *Applications* › *App registrations* › **New registration**.
2. Name: `Portal mail` (any name). Supported account types: **Accounts in
   this organizational directory only**. Redirect URI: leave empty.
   **Register.**
3. On its *Overview*, note two values:
   - **Application (client) ID**
   - **Directory (tenant) ID**
4. Do **not** add any permission under *API permissions*. Mail permissions
   granted there would reach every mailbox in the organisation; step 5 grants
   them for the listed mailboxes only. (The `User.Read` that is there by
   default does nothing here; it may stay or go.) Added already? Remove
   them (*...* › *Remove permission*); and if admin consent was granted for
   them, also revoke it under *Enterprise applications* › `Portal mail` ›
   *Permissions*. Both kinds add up: the Exchange roles of step 5 do not
   narrow a permission granted here.

## 3. The certificate, made by the portal

In the portal, **Settings › Email › Accounts**, panel *Microsoft 365*:

1. Enter the **Directory (tenant) ID** and the **Application (client) ID**.
   **Save.**
2. **Make a certificate.** The portal makes a key pair; the private key stays
   on the server. The certificate (its public part) is valid three years.
3. **Download** it: a `.cer` file.

## 4. The certificate to the app registration

Back in Entra, in the app registration `Portal mail`:

1. *Certificates & secrets* › tab *Certificates* › **Upload certificate**.
2. Choose the `.cer` file, description `Portal`, **Add**.
3. The *Thumbprint* Entra shows must be the one the portal shows under
   *Certificate in use*.

Do not create a *client secret*: the certificate replaces it.

## 5. Which mailboxes the app may use

This is set in Exchange Online, with PowerShell ("RBAC for Applications").
Replace the values in `<...>`.

First find the **Object ID of the enterprise application** -- not the one of
the app registration: Entra › *Identity* › *Applications* › *Enterprise
applications* › `Portal mail` › *Overview* › **Object ID**.

Open PowerShell and run, one line at a time -- with your values in place of
`<...>`, without the angle brackets (PowerShell reads `<` as an operator):

```powershell
Install-Module -Name ExchangeOnlineManagement -Scope CurrentUser
Connect-ExchangeOnline -UserPrincipalName <your admin address>

# Once per organisation; nothing happens if it was done already. If it did
# something, the next commands may be refused at first ("... is currently not
# allowed in your organization" / "you don't have access to create ... the
# management role assignment"): Disconnect-ExchangeOnline, wait 15 minutes to
# an hour, connect again and go on from here.
Enable-OrganizationCustomization

# The app, known to Exchange.
New-ServicePrincipal -AppId <Application (client) ID> -ObjectId <Object ID of the enterprise application> -DisplayName "Portal mail"

# Mark the mailboxes: the replies' mailbox may also be read, the others only sent from.
Set-Mailbox -Identity support@<your domain> -CustomAttribute15 "portal-mail-replies"
Set-Mailbox -Identity noreply@<your domain> -CustomAttribute15 "portal-mail"

# Two scopes: every marked mailbox, and the replies' one.
New-ManagementScope -Name "Portal mail" -RecipientRestrictionFilter "CustomAttribute15 -like 'portal-mail*'"
New-ManagementScope -Name "Portal mail replies" -RecipientRestrictionFilter "CustomAttribute15 -eq 'portal-mail-replies'"

# Sending from all of them; reading only the replies' one.
New-ManagementRoleAssignment -App <Application (client) ID> -Role "Application Mail.Send" -CustomResourceScope "Portal mail"
New-ManagementRoleAssignment -App <Application (client) ID> -Role "Application Mail.Read" -CustomResourceScope "Portal mail replies"
```

If `New-ManagementRoleAssignment` says you have no access to create the role
assignment although `Enable-OrganizationCustomization` ran: a fresh session
(above), and an account that is *Global Administrator* -- the role
assignments are handed out by the *Organization Management* group, which
holds the Global Administrators.

Check it (both lines should list the roles as *InScope: True*):

```powershell
Test-ServicePrincipalAuthorization -Identity <Application (client) ID> -Resource support@<your domain>
Test-ServicePrincipalAuthorization -Identity <Application (client) ID> -Resource noreply@<your domain>
```

**A mailbox added later** needs only its mark:
`Set-Mailbox -Identity <address> -CustomAttribute15 "portal-mail"`.

Microsoft can take **up to two hours** before the portal may use a mailbox;
until then a test email is refused ("does not let the app send from ...").

## 6. The accounts in the portal

**Settings › Email › Accounts**:

1. Microsoft 365 panel: **Check sign-in** → "Signed in to Microsoft."
2. *Add an account* › **Microsoft 365**: key `support`, mailbox
   `support@<your domain>`, sender name (e.g. `Joanneum Aeronautics`). **Add.**
3. The same for `noreply`.
4. **Test** on each row: signs in and reads, sends nothing. For support it
   should add "It can read the mailbox, so replies can come back."
5. Tab **Test email**: send one from each account to yourself.

Then tab **Senders**: the default (e.g. `noreply`), **replies** `support` --
replies come back into the portal only when the replies' account is a
Microsoft 365 mailbox -- and the others as wanted. For large mailings an SMTP
account such as Brevo can stay their sender.

Settings › **System health** shows the connection, the certificate's date and
whether replies come in at once.

## 7. Replies

Nothing to do: once *support* is the replies' account and step 5 is in
effect, the portal asks Microsoft to tell it about new mail in that inbox. A
reply to an answer from Admin › Messages appears in its thread, and the
message is open again. Everything else in the mailbox is left alone.

If Microsoft does not notify the portal (System health says "looked for every
15 minutes only"), replies still come in, within 15 minutes.

## 8. When IONOS goes

Once the senders point at the Microsoft accounts and the test emails arrived:
remove the IONOS accounts on the Accounts tab.

Your domain's mail is at Microsoft already. Worth a look once, in the
**Microsoft Defender portal** (security.microsoft.com) › *Email &
collaboration* › *Policies & rules* › *Threat policies* › *Email
authentication settings* › **DKIM**: signing on for your domain, so emails
from the portal are trusted by others' mail servers.

## Every three years: a new certificate

System health warns 30 days before the certificate runs out.

1. Settings › Email › Accounts › Microsoft 365: **Make a new certificate**,
   **Download** it. The old one goes on working.
2. Upload it in Entra as in step 4 -- the old one stays there for now.
3. **Check sign-in** → "... with the new certificate; it is in use now."
4. In Entra, delete the old certificate (its thumbprint is no longer shown in
   the portal).

## When something does not work

| The portal says | What to do |
| --- | --- |
| Microsoft does not know this certificate | Step 4: upload the `.cer`; the thumbprints must match. |
| There is no app with this application (client) ID in that tenant | Check both IDs (step 2). |
| There is no tenant with this ID | The Directory (tenant) ID, from the app's Overview. |
| The app has no service principal in the tenant yet | Open `Portal mail` once under *Enterprise applications*. |
| ... does not let the app send from ... | Step 5 for that mailbox; then wait up to two hours. |
| There is no mailbox ... in the tenant | The address of the shared mailbox (step 1). |
| Replies ... are looked for every 15 minutes only | The portal needs its https address; and step 5's *Mail.Read* for the replies' mailbox. |
