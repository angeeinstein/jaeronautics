# Importing the Old Forum's Posts

The people come first — that is [forum-import.md](forum-import.md), and none of
this works until it has been done, because every post is attributed to an
account that has to already exist.

What follows is the **rehearsal**: one thread, moved onto the real forum, to
find out whether the thing can be done at all. It is not the migration. It is
not idempotent — running it twice posts the thread twice — and it answers
questions that cannot be reasoned out, only asked:

1. Does Discourse keep the dates it is given when posting on somebody else's
   behalf? The archive spans 2014 to 2026, and a thread that arrives dated
   today is worse than one that never arrives.
2. Is an imported person allowed to post at all — never signed in, trust level
   0, an address on a domain that no longer receives?
3. Do the attachments upload, does the BBCode read sensibly, does each post
   land under the right name?

## Before you start

**Take the snapshots.** Both LXCs, the forum and the portal, before anything is
sent. This writes to Discourse and cannot be undone from inside it.

**Update the portal** from the maintenance page, so the commands below exist.

## 1. Put the files where the portal can read them

Two things are needed: the MyBB dump, and the old forum's `uploads` folder with
the `.attach` files in it.

Neither belongs in `/var/www/jaeronautics`. That directory is the application,
it is replaced by updates, and the dump holds seven hundred real email
addresses. Somewhere like `/var/tmp/forum-migration` is right.

Download as yourself and hand the whole directory over afterwards, rather than
downloading as `jaeronautics` into a directory it cannot write to. The order
matters, and getting it wrong reads as `curl: (23) Failure writing output`,
which sounds like a network problem and is a permissions one:

```bash
mkdir -p /var/tmp/forum-migration
cd /var/tmp/forum-migration
```

From your own machine, with `python3 -m http.server` running **in the folder
that contains `uploads`**, not inside `uploads` itself:

```bash
curl -O http://YOUR-PC:8000/backup__20260919_210211_Xwyis10ESUeobhvu.sql.gz
mv backup__*.sql.gz dump.sql.gz
```

The rename is worth it. Every command below names the dump, the real filename
has a random suffix, and a typo in it comes back as "File does not exist" after
you have already got the rest of a long command right.

Then, once everything is downloaded — and again after copying any attachments
across:

```bash
chown -R jaeronautics: /var/tmp/forum-migration
```

Check it took, rather than assuming:

```bash
sudo -u jaeronautics ls -l /var/tmp/forum-migration
```

The uploads folder is around 9 GB in total. For the rehearsal you do not need
all of it — see step 4, where the dry run names the handful of files it
actually wants.

## 2. Make a migration API key

In Discourse: **Admin → API → Keys → New Key**.

- Description: something you will recognise, e.g. `forum migration`
- User Level: **All Users**
- Scope: Global

**User Level is the one that matters, and it is not the same as scope.** A key
bound to a single user works perfectly for every admin call — they all act as
that user — and fails the moment it is asked to act as somebody else, which is
the whole of this job. The failure reads `invalid_access`, which sounds like a
permissions problem and is not.

Put it in a file, on one line, where you can see what actually arrived:

```bash
printf '%s' 'PASTE-THE-KEY-HERE' > /var/tmp/forum-migration/api-key
wc -c < /var/tmp/forum-migration/api-key
chown jaeronautics: /var/tmp/forum-migration/api-key
chmod 600 /var/tmp/forum-migration/api-key
```

**That `wc` has to say 64.** Discourse's keys are 64 hexadecimal characters,
and terminals lose the tail of a long paste more often than you would think.

Reading it back is the point of doing it this way. A prompt that does not echo
— `read -rs` and the like — keeps the key off the screen, and also keeps a
truncated key off the screen, which is worse: the failure it causes points
somewhere else entirely. Discourse treats a key it does not recognise as
**anonymous** rather than refusing it, and hides admin routes from anonymous
requests behind a 404. So a half-pasted key reads as

    GET /admin/site_settings.json failed (404): not_found

which looks like a missing feature, or a Discourse version that moved the
route, and is neither. `check-forum-settings` now says so before it gets that
far, but only because it counts the characters — which you can do yourself in
one command.

The key is in your shell history this way. On a test box that is a fair trade
for being able to see it; for a production run, clear the history line
afterwards or use a method that does not echo, now that you know what a
truncated key looks like.

**Revoke it when you are done** in any case. A key that can post as any member
of the forum should not outlive the afternoon it was made for.

## 3. Ask the forum whether it will take the archive

This reads and changes nothing:

```bash
cd /var/tmp/forum-migration
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       check-forum-settings /var/tmp/forum-migration/dump.sql.gz'
```

(The `sh -c` is not decoration. `env KEY=value` puts the key in a process's
arguments, where anyone on the box can read it out of `ps`; read from a file
inside the shell, it never appears there.)

It prints three things:

- how much there is, including the total size of the attachments — the forum
  needs room for that on top of what it already holds;
- a table of every setting the archive needs, what it is now, and what it has
  to be. **The `now` column is what gets put back afterwards**;
- a short list of threads worth rehearsing with, hardest first: several
  authors, several posts, attachments. Pick a `tid` from there.

Discourse's defaults are written for somebody typing into a box today, and the
archive is a decade of `Klausuren`, `Exams` and `Danke!`. Roughly eighteen
settings need loosening. You do not have to do it by hand — see step 5.

## 4. Rehearse without sending anything

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       migrate-forum-thread /var/tmp/forum-migration/dump.sql.gz \
       --thread 336 --uploads /var/tmp/forum-migration/uploads --dry-run'
```

Nothing is posted. What it tells you:

- the date each post would be given and who it would be posted as. **If every
  author comes out as `system`, stop** — the author lookup has missed, and the
  run would anonymise the whole thread;
- every attachment it cannot find, **by full path**. That is the list of files
  to copy across; you do not need the other 9 GB to rehearse.

## 5. The real run

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       migrate-forum-thread /var/tmp/forum-migration/dump.sql.gz \
       --thread 336 --uploads /var/tmp/forum-migration/uploads \
       --category 12 --adjust-settings \
       --settings-file /var/tmp/forum-migration/settings-before.json'
```

`--category` is the Discourse category id, the number in its URL: `/c/archiv/12`
is `12`. Make one for the rehearsal rather than posting into somewhere people
are reading.

`--adjust-settings` makes it loosen what has to be loosened, run, and put it
back. It writes what everything was into `--settings-file` **before it changes
anything**, because the run that most needs that file is the one that never
reaches its own ending. Name the file rather than letting it default: the
default lands in the working directory, which the application user may not be
able to write to.

**It will not write over a file that is already there.** A record still on disk
means an earlier run loosened these settings and may never have put them back;
overwriting it would record the loosened values as the originals and the way
back would be gone. Restore from it first, or move it aside if that is already
done.

## 6. If it does not finish

A dropped connection, a full disk, Ctrl-C — the forum is left with its guards
down. Put them back:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       restore-forum-settings /var/tmp/forum-migration/settings-before.json'
```

A setting that no longer holds the value the import gave it was changed by
somebody else since, and is left alone. `--force` overrides that.

Two settings are deliberately **not** restored:

- `authorized_extensions` stays widened. This forum exists so that people can
  share exam papers; one that accepts a PDF during the import and refuses one
  the next morning would be a strange thing to have built.
- `max_attachment_size_kb` is restored, but above 10 MB the webserver in front
  of Discourse has its own `client_max_body_size` and it answers first. A file
  over that comes back as `413`, which is not Discourse saying anything.

## 7. Then go and look at it

The report says what Discourse recorded against what was asked for, which is
the point of the whole exercise — but the report is written by the same code
that did the posting, so read the topic itself:

- are the dates the old ones, or today?
- is each post under the right name, with the right avatar?
- do the attachments open, and are they named what they were named?
- does the BBCode read as text, or are there stray `[size=4]` left in it?
- are quoted posts attributed to a person, not to `pid='112' dateline='…'`?

Only then is it worth talking about the other 719 threads.

## 8. Afterwards

- Revoke the API key, and delete `/var/tmp/forum-migration/api-key`.
- The dump holds around 740 real email addresses. Delete it from the server
  when it is no longer needed.
- Check that the settings went back: run `check-forum-settings` again and read
  the `now` column.
