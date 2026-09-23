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
addresses. Somewhere like `/var/tmp/forum-migration` is right:

```bash
sudo mkdir -p /var/tmp/forum-migration
sudo chown jaeronautics: /var/tmp/forum-migration
cd /var/tmp/forum-migration
```

From your own machine, with `python3 -m http.server` running **in the folder
that contains `uploads`**, not inside `uploads` itself:

```bash
sudo -u jaeronautics curl -O http://YOUR-PC:8000/backup__20260919_210211.sql.gz
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

Keep the key out of your shell history and out of `ps`:

```bash
umask 077
sudo -u jaeronautics tee /var/tmp/forum-migration/api-key >/dev/null
# paste the key, then Ctrl-D
```

**Revoke it when you are done.** A key that can post as any member of the forum
should not outlive the afternoon it was made for.

## 3. Ask the forum whether it will take the archive

This reads and changes nothing:

```bash
cd /var/tmp/forum-migration
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       check-forum-settings /var/tmp/forum-migration/backup__20260919_210211.sql.gz'
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
       migrate-forum-thread /var/tmp/forum-migration/backup__20260919_210211.sql.gz \
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
       migrate-forum-thread /var/tmp/forum-migration/backup__20260919_210211.sql.gz \
       --thread 336 --uploads /var/tmp/forum-migration/uploads \
       --category 12 --adjust-settings'
```

`--category` is the Discourse category id, the number in its URL: `/c/archiv/12`
is `12`. Make one for the rehearsal rather than posting into somewhere people
are reading.

`--adjust-settings` makes it loosen what has to be loosened, run, and put it
back. It prints the path of the file recording what everything was **before it
changes anything**, because the run that most needs that file is the one that
never reaches its own ending.

## 6. If it does not finish

A dropped connection, a full disk, Ctrl-C — the forum is left with its guards
down. Put them back:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       restore-forum-settings /var/tmp/forum-migration/forum-settings-20260923-141500.json'
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
