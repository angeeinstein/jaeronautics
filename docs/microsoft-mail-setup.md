# Setting up email through Microsoft 365

Step by step: the portal sends from shared mailboxes of the association's
Microsoft 365, and the replies to answers from Admin › Messages come back into
the portal. Why it is built this way: `docs/microsoft-mail-plan.md`. In the
portal this guide is under Settings › Email › Accounts › Microsoft 365,
*Setup guide*, with the IDs and the domain it knows already filled in.

It costs nothing: shared mailboxes need no licence, the app registration is
free, and no Azure service is used.

**What you need:** an account that is *Global Administrator* in the tenant (or
both *Exchange Administrator* and *Application Administrator*), the portal's
Settings › Email open in another tab, and -- for step 5 -- PowerShell on your
computer (Windows has it; on a Mac or Linux install
[PowerShell 7](https://learn.microsoft.com/powershell/scripting/install/installing-powershell)).

Takes about half an hour, plus waiting: after `Enable-OrganizationCustomization`
in step 5, Exchange may refuse the last two commands for up to a day, and a
mailbox's permission can take up to two hours. Step 5b lets you test in the
meantime.

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
4. Do **not** add any permission under *API permissions* (except, for a
   while, step 5b). Mail permissions granted there reach every mailbox in
   the organisation; step 5 grants them for the listed mailboxes only. (The `User.Read` that is there by
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

This is set in Exchange Online, with PowerShell ("RBAC for Applications"):
the app may send from the marked mailboxes and read only the replies' one --
nothing else in the organisation.

First find the **Object ID of the enterprise application** -- not the one of
the app registration: Entra › *Identity* › *Applications* › *Enterprise
applications* › `Portal mail` › *Overview* › **Object ID**.

Open PowerShell and run, one line at a time. Put your values in place of
`<...>`, without the angle brackets (PowerShell reads `<` as an operator).

```powershell
Install-Module -Name ExchangeOnlineManagement -Scope CurrentUser
Connect-ExchangeOnline -UserPrincipalName <your admin address>
```

Once per organisation (nothing happens if it was done already):

```powershell
Enable-OrganizationCustomization
```

If it did something, Exchange copies its settings into ones of your own, in
the background; until that is done, the role assignments below are refused
("you don't have access to create ... the management role assignment"), even
for a Global Administrator. Whether it is done (`False` is done):

```powershell
Get-OrganizationConfig | Format-List IsDehydrated
```

The app, known to Exchange:

```powershell
New-ServicePrincipal -AppId <Application (client) ID> -ObjectId <Object ID of the enterprise application> -DisplayName "Portal mail"
```

Mark the mailboxes -- the replies' one may also be read, the others only sent
from:

```powershell
Set-Mailbox -Identity support@<your domain> -CustomAttribute15 "portal-mail-replies"
Set-Mailbox -Identity noreply@<your domain> -CustomAttribute15 "portal-mail"
```

Two scopes -- every marked mailbox, and the replies' one:

```powershell
New-ManagementScope -Name "Portal mail" -RecipientRestrictionFilter "CustomAttribute15 -like 'portal-mail*'"
New-ManagementScope -Name "Portal mail replies" -RecipientRestrictionFilter "CustomAttribute15 -eq 'portal-mail-replies'"
```

The permissions -- sending from all of them, reading only the replies' one.
Refused? See above: `IsDehydrated` still `True`; try again later, after
`Disconnect-ExchangeOnline` and connecting again.

```powershell
New-ManagementRoleAssignment -App <Application (client) ID> -Role "Application Mail.Send" -CustomResourceScope "Portal mail"
New-ManagementRoleAssignment -App <Application (client) ID> -Role "Application Mail.Read" -CustomResourceScope "Portal mail replies"
```

Check it: support should list *Mail.Send* and *Mail.Read*, noreply only
*Mail.Send*, each *InScope: True*.

```powershell
Test-ServicePrincipalAuthorization -Identity <Application (client) ID> -Resource support@<your domain>
Test-ServicePrincipalAuthorization -Identity <Application (client) ID> -Resource noreply@<your domain>
```

**A mailbox added later** needs only its mark:
`Set-Mailbox -Identity <address> -CustomAttribute15 "portal-mail"`.

Microsoft can take **up to two hours** before the portal may use a mailbox;
until then a test email is refused ("does not let the app send from ...").

## 5b. Until Exchange is ready (optional)

To test before the role assignments go through: in Entra, the app
registration › *API permissions* › *Add a permission* › *Microsoft Graph* ›
*Application permissions*: `Mail.Send` and `Mail.Read`; then **Grant admin
consent** for the organisation -- without it they do nothing, and the
*Status* column shows a warning.

These reach **every mailbox** in the organisation: whoever had the portal's
key could read and send from all of them. So only for testing, and as soon as
step 5's check shows *InScope: True*: remove both permissions (*...* ›
*Remove permission*) and revoke the consent (*Enterprise applications* ›
`Portal mail` › *Permissions*). Both kinds add up -- the roles of step 5 do
not narrow these.

*Check sign-in* in the portal says which of these are granted ("Granted in
Entra for every mailbox: ..."); once they are gone it says only "Signed in".

## 6. The accounts in the portal

**Settings › Email › Accounts**:

1. Microsoft 365 panel: **Check sign-in** → "Signed in to Microsoft."
2. *Add an account* › **Microsoft 365**: a key (`support-microsoft`, or
   `support`), mailbox `support@<your domain>`, sender name (e.g.
   `Joanneum Aeronautics`). **Add.**
3. The same for `noreply@<your domain>`.
4. **Test** on each row: signs in and reads, sends nothing. For the replies'
   mailbox it should add "It can read the mailbox, so replies can come back."
5. Tab **Test**: send one from each account to yourself. Refused, it says
   why.

Then tab **Senders**:

- **Default**: the noreply account.
- **Replies to contact messages**: the support account -- replies come back
  into the portal only when it is a Microsoft 365 mailbox.
- **Backup**: an account of another provider, such as Brevo. When an account
  cannot send -- Microsoft down, the certificate run out -- the backup sends
  the email instead, from its own address, answers still going to the
  address it should have come from. Not announcements and team mailings:
  they wait. System health shows which account cannot send, until it can
  again.
- For large mailings an SMTP account such as Brevo can stay their sender.

Settings › **System health** shows the connection, the certificate's date and
whether replies come in at once.

## 7. Replies

Nothing to do: once the replies' account is a Microsoft 365 mailbox the app
may read, the portal asks Microsoft to tell it about new mail in that inbox.
A reply to an answer from Admin › Messages appears in its thread, and the
message is open again. Everything else in the mailbox is left alone.

If Microsoft does not notify the portal (System health says "looked for every
15 minutes only"), replies still come in, within 15 minutes.

## 8. When the old provider goes

Once the senders point at the Microsoft accounts and the test emails arrived:
remove the old provider's accounts on the Accounts tab (one may stay as the
backup).

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
| ... does not let the app send from ... | Step 5 for that mailbox, then wait up to two hours -- or, with 5b, the admin consent. |
| There is no mailbox ... in the tenant | The address of the shared mailbox (step 1). |
| Replies ... are looked for every 15 minutes only | The portal needs its https address, and permission to read the replies' mailbox (step 5). |
| ... cannot send ...: the backup sends its emails | That account's own message says why; fix it, and the next email it sends clears this. |

| PowerShell says | What to do |
| --- | --- |
| The operator `<` is reserved for future use | A `<...>` placeholder left in the line: your value, without the brackets. |
| ... is currently not allowed in your organization; run Enable-OrganizationCustomization | Run it (step 5). |
| You don't have access to create ... the management role assignment | Exchange is not ready after `Enable-OrganizationCustomization` (`IsDehydrated` is `True`): later, in a new session. Meanwhile 5b. |
