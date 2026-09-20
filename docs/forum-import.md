# Importing the Old Forum's People

The old forum is MyBB. Bringing its users across means two things leaving that
server: **rows from its database** and **the avatar files**. The PHP itself is
just the application and is not needed — everything about a person is in MySQL.

## 1. Find the table prefix and the Jahrgang field

MyBB lets the installer choose a table prefix, and custom profile fields are
stored in columns named after their id, which differs per installation. Both
have to be looked up rather than assumed.

The prefix is in `inc/config.php`:

```php
$config['database']['table_prefix'] = 'mybb_';
```

The profile field id comes from the database. `Jahrgang` is a custom field, so:

```sql
SELECT fid, name, type FROM mybb_profilefields ORDER BY fid;
```

That returns something like `fid = 3, name = Jahrgang`. The corresponding
column in `mybb_userfields` is then **`fid3`**. Substitute your own number
everywhere `fid3` appears below.

## 2. Export the people

The quickest route is the dump you already have. MyBB's **Tools & Maintenance →
Database Backups** writes a gzipped `mysqldump`, and `scripts/mybb_export.py`
turns one into the importer's JSON without a database connection:

```bash
python scripts/mybb_export.py backup__20260919_210211_Xwyis10ESUeobhvu.sql.gz --out people.json
```

It finds the table prefix and the Jahrgang field itself, and prints what it
found so you can check before importing. The September 2026 dump gave:

```
table prefix       : mybb_
  ignored          : mybb_tapatalk_ (plugin tables)
users table        : mybb_users
year group field   : fid4 (Jahrgang)
people             : 740
  with year group  : 488
  with avatar file : 677
  remote avatars   : 1 (hosted elsewhere, no local file)
  files live in    : uploads/avatars/  (677)
```

Check three things against that before going further. **`year group field`**
must name `Jahrgang`, not some other custom field — everything downstream is
built on it. **`people`** must be roughly the number the forum's own member
list shows. And a **`NOT READ`** line means this script could not parse some
of the dump's rows and silently exported fewer people than the forum has;
stop if you see one.

Timestamps are read in the board's timezone (`--timezone`, default
`Europe/Vienna`): MyBB stores them as UTC but displayed them locally, so
reading them as UTC would move every late-evening registration a day earlier
than the forum has shown for a decade.

Nothing leaves your machine — the dump holds six hundred real addresses, and
only the JSON needs to travel.

### Or query the database directly

```sql
SELECT
    u.uid                                  AS source_user_id,
    u.username                             AS source_username,
    u.email                                AS source_email,
    NULLIF(TRIM(f.fid3), '')               AS year_group,
    u.postnum                              AS post_count,
    FROM_UNIXTIME(u.regdate, '%Y-%m-%d')   AS joined_on,
    NULLIF(FROM_UNIXTIME(u.lastpost, '%Y-%m-%d'), '1970-01-01') AS last_posted_on,
    u.avatar                               AS avatar_source
FROM mybb_users u
LEFT JOIN mybb_userfields f ON f.ufid = u.uid
ORDER BY u.uid;
```

Notes on the columns:

- `regdate` and `lastpost` are Unix timestamps. A member who never posted has
  `lastpost = 0`, which formats as `1970-01-01` — hence the `NULLIF`.
- `avatar` holds something like `./uploads/avatars/avatar_142.jpg?dateline=1699999999`,
  or an empty string, or an external URL on older installs. The importer only
  needs the file name; strip the query string.
- There is **no real-name field** on these profiles, so the display name falls
  back to the username. `PopovicA_L23` is surname, initial and year group, which
  is readable enough to be worth showing as-is.

phpMyAdmin will export that result as JSON directly. From a shell:

```bash
mysql --batch --raw -e "…query…" forumdb | …   # or use phpMyAdmin's JSON export
```

## 3. Copy the avatars

**Run the converter first.** It prints the directory the avatar paths point at,
counted — `files live in: uploads/avatars/ (431)` — so the right folder gets
fetched. That matters on a webspace holding years of attachments: archiving all
of `uploads/` can mean gigabytes when the avatars are a few dozen megabytes.

On IONOS there are two routes:

- **Webspace Explorer** (Webspace nutzen → File Manager). Open the directory the
  converter named, tick it, press **Archivieren**, download the archive. No
  client, no credentials.
- **SSH**, which is faster for hundreds of small files because it is one
  transfer instead of hundreds. Check *Sichere FTP-Zugänge verwalten* first: an
  account has to be listed as **SFTP + SSH**, and a plain **SFTP** one is
  refused by `rssh` with *Allowed commands: sftp*.

  ```bash
  ssh u75967994@home511202816.1and1-data.host
  tar czf ~/avatars.tar.gz -C /homepages/31/d511202816/htdocs uploads/avatars
  exit
  scp u75967994@home511202816.1and1-data.host:~/avatars.tar.gz .
  ```

### Where the originals are



They are plain files under the forum root, in the path named by `u.avatar` —
by default `uploads/avatars/`. Copy the whole directory; the importer matches
each file to a person by the name recorded in the export.

Check whether any are remote URLs (`avatar` starting with `http`) before
assuming every person has a local file:

```sql
SELECT COUNT(*) FROM mybb_users WHERE avatar LIKE 'http%';
```

## 4. What the importer expects

A JSON array, one object per person. Only `source_user_id` and
`source_username` are required:

```json
[
  {
    "source_user_id": "142",
    "source_username": "PopovicA_L23",
    "source_email": "a.popovic@edu.fh-joanneum.at",
    "display_name": null,
    "year_group": "LAV23",
    "post_count": 2,
    "joined_on": "2023-11-23",
    "last_posted_on": "2024-06-02",
    "avatar_file": "avatar_142.jpg"
  }
]
```

### `source_group` — who had already left

`source_group` and `source_group_reason` are stored on the imported profile as
history, and **nothing acts on them**. They are kept because the old forum is
the only place this information exists, and switching it off destroys it.

On this board, MyBB's *Banned* group is not punishment. It is how a member who
stopped being a student was deactivated — the reasons in the ban log read
`non active student`, `Not active student/exchange semester`, `Is now a
Lecturer`. The September 2026 dump splits 507 Registered, 230 Banned, 3
Administrators, and the banned accounts are plainly real people: 220 of 230
follow the university naming convention, 188 have avatars, 89 have posts.

Two things follow, and both are decisions rather than defaults:

* **These people should almost certainly still be imported.** Eighty-nine of
  them wrote posts that need a name and a face against them, which is the whole
  reason for the import. Nothing about an imported account grants access
  anyway — no password, no membership, no permission — so importing a closed
  account is not reopening it.
* **The group is not mapped onto `users.disabled_at`.** That column means an
  administrator *here* decided something, with a person and a date attached; a
  MyBB group is a fact about a system being switched off. Writing one into the
  other would invent an admin action that never happened — and since a banned
  person can still come back (two of the 258 likely returners are banned), they
  would claim a dead account and be locked out with no way to tell why.
* **Not every row is a person.** `LAVBoard_System` is the board's own account
  (group Administrators, no posts) and `Test_User` is a test account. Decide
  whether to drop them from `people.json` before importing; the importer has no
  opinion about it.

### The remaining fields

`display_name` may be null; the username is used instead. `year_group` may be
null. It can be recovered from the username suffix, which is the same rule the
portal uses in reverse: `…_L23` → `LAV23`, `…_M25` → `MAV25`. A few people
registered before that convention settled and spelled the programme out, so
`…_LAV23` and `…_ATM18` are read too — `ATM` is a third programme that appears
in the old forum's Jahrgang field but never as a single letter. The importer
uses this as a fallback rather than leaving the field empty, and the run's
report says how many were derived rather than exported. Anything that does not
match one of those two shapes is left empty: a wrong year group is worse than a
missing one.

On the September 2026 export that covered 735 of 740 people — 488 from the
Jahrgang field, 247 from the username, 5 left unknown.

To see the whole picture rather than the counts, add `--year-groups` to the
import command. It prints every distinct year group with how many people carry
it, and then names everyone the rules could not place, with the year they
registered:

```
Year groups (33 distinct, 735 people):
  ATM17     5  #####
  LAV23    36  ####################################
  ...

5 people with no year group (nothing in the export's field, nothing readable
in the username):
  uid    11  dpilz                     registered 2014
  ...
```

Year groups are counted exactly as written, so a stray `lav23` or a trailing
space shows up as its own row rather than being quietly folded into `LAV23`.
To fill a gap, set `year_group` on that person in `people.json` and run the
import again — it updates rather than duplicates.

The report also counts `year_groups_disagreeing`: people whose exported field
and username say different things. This is normal and needs no action. It is
mostly somebody who registered as `…_L21` during the bachelor's and whose
Jahrgang field was later updated to `MAV24` when they moved to the master's —
the username never changes, the field does. **The exported field always wins.**
There were 20 of these. A run that suddenly reports hundreds means the export
lined up the wrong column, and is worth stopping for.

## 4b. Running the import

On a deployed server this runs as the application user, out of the virtualenv —
not as `root`, and not with a bare `flask`, which is not on `PATH`:

```bash
cd /var/www/jaeronautics

# Rehearse. Writes nothing at all, avatars included.
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     import-forum-people people.json --dry-run --year-groups \
     --avatar-dir /path/to/avatars

# The real thing.
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     import-forum-people people.json --avatar-dir /path/to/avatars
```

**As `jaeronautics`, not as `root`.** The import writes normalised avatars into
`storage/forum_avatar_staging/`, and files written by root there are files the
application cannot later replace or delete. Both `people.json` and the avatar
directory therefore have to be readable by that user — if the dry run reports
every avatar as missing, that is the reason, and the fix is to move the folder
somewhere readable rather than to run the import as root.

Always the dry run first — it produces the identical report and writes nothing.
The run is safe to repeat: people are matched on the old forum's `uid`, so a
second run updates rather than duplicating, and the `user.id` that Discourse
knows them by never moves. Avatars go through the same normalisation an uploaded
avatar gets, so an enormous 2014 JPEG does not become a second class of file the
rest of the application has never seen.

Two things are reported rather than resolved, because both need a person:

- an entry whose forum username already belongs to an account — almost
  certainly the same student returning, which is a link to make by hand;
- an avatar file named in the export but missing from the directory.

Neither stops the rest of the import.

## 5. Do the portal first, then Discourse

This ordering is not a preference. The portal assigns each person a `user.id`,
and that id is what Discourse Connect uses as `external_id` — it is already how
every current member is linked (`build_sso_payload` sends
`"external_id": str(user.id)`).

So:

1. Import into the portal. Every old user gets a `users` row and an
   `imported_forum_profiles` row, and therefore an id.
2. Import the content into the new Discourse, setting each user's `external_id`
   to the portal id from step 1.

Doing it the other way round means reconciling six hundred Discourse accounts
against six hundred portal accounts afterwards, by name, by hand.

## 6. Usernames, and what a collision would mean

The portal builds forum usernames as
`{Lastname}{FirstInitial}_{ProgrammeLetter}{Year}` —
`build_forum_username_base()` — so `LAV23` becomes `_L23` and `MAV25` becomes
`_M25`. The old forum used the same convention, which is why an imported
`PopovicA_L23` sits in the same namespace as the portal's own
`EinsteinA_L23`.

**This is not a new hazard.** Two different people collide only if they share a
surname, a first initial, a programme *and* an intake year, which was already
possible between two members and is no likelier because six hundred more rows
exist. Intake years only move forward, so somebody joining in 2027 gets `_L27`
and can never land on a 2023 name by accident.

The case that does matter is narrower and is the opposite of a clash. A
returning student entering their original cohort generates their *own* old
username — `PopovicA_L23` — and `generate_unique_forum_username()` would resolve
that by appending a suffix, handing them `PopovicA_L23-2` beside their own
decade of posts. That is precisely the second identity the import exists to
prevent.

So the only thing worth doing is noticing it: a generated username that collides
with an **unclaimed imported profile** should be surfaced for an administrator
rather than silently suffixed. Given the arithmetic above, such a collision is
almost certainly the same person coming back, which makes it useful signal
rather than an error. It stays a small check, not a subsystem, and the claim
itself is still made by a person who recognises them — never by matching an
email address, since those are dead and possibly reissued.

## 6. Returning students reclaim their old account

Around 250 of the archived people are still studying and will sign up on the
new portal. They get their old forum identity back automatically, and the
evidence is the **verified email address**.

That works because of who they are: still students, so their
`@edu.fh-joanneum.at` address still receives mail, and the archived account
names it. Every one of the 258 people from cohort 2021 onwards has such an
address. Being able to read that inbox is the proof — and it is the only proof
available, since the usernames are derived from names and would be a guess.

The claim happens when the verification link is followed, in
`claim_archived_account()`:

* **The membership moves onto the archived `users` row**, not the other way
  round. Discourse knows people by `external_id = str(user.id)`, so the old
  posts hang off that id — keeping it is the difference between finding your
  history and finding an empty profile next to it. The row the signup created
  is emptied and marked `deleted_at`, not deleted, because audit entries
  written during signup point at it.
* **Nothing happens before verification.** Otherwise typing somebody else's
  university address at signup would be enough to take their posts.
* **A failure never costs a verification.** The claim runs inside a savepoint
  and any exception is logged and swallowed: the member ends up verified with
  an unclaimed archive, which is fixable by hand, rather than holding a
  verification link that does not work.

It refuses, rather than guessing, when two archived accounts share one address
(the real export has exactly one such pair), when the profile was already
claimed, and when the signup account holds a role, a forum account or avatar
submissions — anything the claim is not prepared to carry across.

**Tell students to sign up with their university address.** Somebody who uses
a private address gets a working new account and no claim, which then needs
linking by hand.

### Ordering

Because the claim is automatic, the import can run **before** the intake —
which is what the plan requires, since the Discourse content migration has to
be finished first. Without it, the ordering would matter a great deal: an
import that ran first would hold every returning student's username, and the
portal would hand them `PopovicA_L23-2` on signup.

## 7. Where the imported people show up

In the ordinary account directory, with everybody else. A former member is a
member the association still has a record of, and reconnecting one is the same
action as anything else done from an account page — so they are one list
narrowed by a filter, not a second page.

* **Account Type** filter: *All* / *Portal accounts* / *Former forum members*.
* The **Active** filter already excludes them, since they have no membership.
* Their rows show the address the old forum held and the name and year group
  it recorded, not the `@imported.invalid` placeholder, which would tell an
  admin nothing. Search covers both.
* Sorting is by email, so `forum-mybb-…` addresses land in the middle of the
  alphabet rather than burying the first page.

**A reconnected person stops looking like an archive.** Claiming attaches the
membership to that same row, so it becomes an ordinary member row with a
*Reconnected* marker beside its status — filing a current member under "former
forum member" for ever would be wrong.

The account page grows an **Old Forum Account** panel: the avatar, the forum
username, year group, post count, join and last-post dates, the address it was
registered under, and whether it has been claimed. The avatar is served by
`admin_archived_avatar`, admin-only and through the application rather than
from a static path — the staging directory also holds avatars awaiting review,
and none of it should be reachable by guessing a filename.

The dashboard counts them separately. Folding them into *Total Accounts* would
report 760 accounts for an association with twenty.
