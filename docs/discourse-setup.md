# Discourse: What Has to Be Set, and Who Sets It

One list for configuring the forum from nothing, as for the move to Azure.
Everything the portal depends on is here, split by who does it. The first
column is **the one to work through by hand**; the second happens when the
commands run, and is listed so that nobody "fixes" it back.

Each line was found the hard way on the test forum in September 2026; the
reasons are in [forum-access-decisions.md](forum-access-decisions.md) and
[forum-content-import.md](forum-content-import.md).

## By hand, in Discourse (Admin → Settings)

| Setting | Value | Why |
|---|---|---|
| `enable discourse connect` | on | The portal is the only way in |
| `discourse connect url` | `https://<portal>/forum/discourse/connect` | Where Discourse sends people to sign in |
| `discourse connect secret` | the same as in the portal | Different on either side means every sign-in and every sync fails with "Login Error" |
| `login required` | on | Category permissions alone do not make the site private |
| `default trust level` | 1 | Level 0 may not attach files, and this forum exists to share exam papers; everybody arriving is a verified, paying member |
| `allow uncategorized topics` | off | Uncategorized cannot be deleted or restricted, so keep it empty |
| The `General` and `Site Feedback` categories | delete, or restrict to member groups | With `login required`, "everyone" includes expired members |

## By hand, elsewhere

| What | Where | Why |
|---|---|---|
| **API key for the portal** — User Level *Single User: `system`* | Discourse Admin → API; paste into the portal's Forum settings | Permanent. Everything the portal does is an admin call as `system` |
| **API key for the import** — User Level *All Users* | Discourse Admin → API; `DISCOURSE_MIGRATION_API_KEY` | Only for `import-forum-content`, which posts as each author. **Revoke afterwards** |
| **Disk: 40–50 GB** | the container / VM | The archive's uploads are 7 GB, Discourse keeps resized copies, and a backup needs as much again while it is made. The test forum ran out mid-import |
| `client_max_body_size` ≥ 200m | the container's nginx (`app.yml`) | Otherwise anything over 10 MB is refused before Discourse sees it |
| Raised API rate limits | `app.yml`, `DISCOURSE_MAX_…` | **For the import only** — put them back afterwards |
| `app.yml` itself | keep a copy | Not part of any Discourse backup |

## Set by the portal's commands — leave them as they are

| Setting | Set by | Left |
|---|---|---|
| `discourse connect overrides avatar` | `publish-forum-profiles` | **on** — the portal approves the pictures |
| `discourse connect overrides email` | `publish-forum-profiles` | **on** — otherwise a reclaimed account keeps `forum-mybb-…@imported.invalid` and no notification reaches its owner |
| `discourse connect overrides name` | `publish-forum-profiles` | **on** — otherwise a reclaimed account keeps its old username as its name |
| `max username length` | `publish-forum-profiles` | **raised to 30** — surname, initial and cohort need it |
| The groups (`members`, `students`, cohorts, `old_forum`, …) | `forum-permissions`, `publish-forum-profiles` | made; membership follows the portal |
| Permissions on the imported categories | `import-forum-content --mapping`, `forum-permissions` | members only; archive read-only |
| `authorized extensions` | `import-forum-content` | **widened** — PDFs, archives, office files |
| The 22 posting limits | `import-forum-content --adjust-settings` | loosened for the run, **put back** after |

The portal's **Test connection** button warns when the avatar, email or name
setting is off, so a forum that has been rebuilt says so the first time
anybody checks it.

## In the portal

| Setting | Where | Why |
|---|---|---|
| Group names, lecture groups | Settings → Forum | Nothing is restricted until they are set; `forum-permissions --dry-run` lists what is missing |
| *This portal decides who is admin or moderator on the forum* | Settings → Forum | Off by default; give the roles first, then tick it |
| Stripe secret key and webhook secret | Settings → Billing | Only then do they travel with `dump-portal-settings` |

## Checking a single person

`flask forum-explain <email or forum username>` shows what the portal would
send, what the forum has, any other forum account holding the same address,
and the queued work still waiting for them.
