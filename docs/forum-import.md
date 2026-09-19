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

`display_name` may be null; the username is used instead. `year_group` may be
null. It can be recovered from the username suffix, which is the same rule the
portal uses in reverse: `…_L23` → `LAV23`, `…_M25` → `MAV25`. The importer does
that as a fallback rather than leaving the field empty, and the run's report
says how many were derived rather than exported. Anything that does not match
the rule exactly is left empty: a wrong year group is worse than a missing one.

## 4b. Running the import

```bash
flask --app aeronautics_members.app:create_app import-forum-people people.json --dry-run
flask --app aeronautics_members.app:create_app import-forum-people people.json \
      --avatar-dir /path/to/uploads/avatars
```

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
