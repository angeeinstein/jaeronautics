# Moving to the next Ubuntu release

Written for somebody who has not done this before. System health
(Admin › Settings › System health, *Staying current*) says when it is time:
a year before the Ubuntu the server runs stops getting security updates, it
says so, and the super admins are emailed once.

The portal keeps everything else up to date by itself -- its own new versions,
the packages it uses, Ubuntu's security updates. A new Ubuntu release is the
one thing it does not do: that is a new server. It takes about an hour, most of
it waiting.

## Why a new server, not an upgrade in place

Ubuntu can upgrade itself to the next release, but if that goes wrong half way
the portal is down until somebody repairs the machine. A new server next to the
old one is safer: the old one keeps running until the new one works, and if
anything is wrong you simply go back to it.

## When

- A new long-term release (LTS) comes every two years, in April: 26.04 in
  April 2026, 28.04 in April 2028.
- Wait for its first update, the ".1" (26.04.1), usually in August: by then the
  early problems are fixed.
- Then any time before the date System health shows. Choose a quiet week, not
  the start of a semester.

## What you need

- An account that can create a server where the current one is (Azure, for
  this association).
- Access to where the portal's address points: the Cloudflare tunnel, or the
  domain's DNS.
- A super admin account in the portal.
- A passphrase for the backup, which you make up and write down.

## Steps

1. **Make a backup.** In the portal: Admin › Settings › Backup and restore.
   Enter a passphrase twice, press **Back up**, wait until it says the file is
   checked, then **download** it. Keep the passphrase: without it the backup
   cannot be opened.

2. **Create the new server.** The same size as the old one, with the new
   Ubuntu LTS (for example "Ubuntu Server 26.04 LTS"). Sign in to it with SSH,
   as the hosting provider's page describes.

3. **Copy the backup to it**, for example with `scp`:
   ```bash
   scp portal-backup-YYYYMMDD-HHMMSS.jabackup you@NEW-SERVER:/tmp/
   ```

4. **Install the portal from the backup.** On the new server:
   ```bash
   git clone https://github.com/angeeinstein/jaeronautics.git /tmp/jaeronautics
   sudo bash /tmp/jaeronautics/install.sh --restore /tmp/portal-backup-YYYYMMDD-HHMMSS.jabackup
   ```
   It installs everything, asks a few questions (the same answers as the old
   server: the domain, the email for the certificate) and the backup's
   passphrase, then restores members, settings and files. Afterwards it is a
   copy of the old portal with its background work paused -- so the copy does
   not send emails or touch Stripe while the old one still runs.

5. **Point the address at the new server.** The Cloudflare tunnel (or DNS) to
   the new server's address, as the installer prints at its end.

6. **Switch the old server off** -- not delete: it is the way back for now.

7. **Resume the background work on the new server.** In the portal, now on the
   new server: Admin › Settings › Backup and restore, tick *This is the server
   members use*, press **Resume background jobs**, and wait until every line
   has a tick. A red cross says what to fix.

8. **Check.** System health shows no problems; *Staying current* shows the new
   Ubuntu and its support date; sign in once as a normal member.

9. **After a week** without trouble, delete the old server.

If something goes wrong before step 6, nothing is lost: the old server still
runs. After it, switch the old server back on and point the address back to it.

More detail: `docs/maintenance.md`, "Backup and Restore" and "Rebuilding the
Portal Somewhere Else".
