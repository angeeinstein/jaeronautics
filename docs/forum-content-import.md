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

Nothing is posted, and no setting can stop a dry run — it sends nothing, and
finding out what is missing is exactly what you want to do *before* changing
anything on the forum. It still prints the settings table, because that is what
the real run will need.

What it tells you:

- the date each post would be given and who it would be posted as. **If every
  author comes out as `system`, stop** — the author lookup has missed, and the
  run would anonymise the whole thread;
- every attachment it cannot find, **by full path**. That is the list of files
  to copy across; you do not need the other 9 GB to rehearse. Point `--uploads`
  at a directory that does not exist yet and it will name all of them.

After copying files in, `chown -R jaeronautics: /var/tmp/forum-migration`
again, and run the dry run until it stops reporting missing files.

### Fetching just the files one thread needs

The problems go to standard error, and every path in them is under the
`--uploads` directory, so the list can drive the copying. With
`python3 -m http.server` still running in the folder that contains `uploads`:

```bash
# ...the dry run as above, keeping what it complained about
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       migrate-forum-thread /var/tmp/forum-migration/dump.sql.gz \
       --thread 258 --uploads /var/tmp/forum-migration/uploads --dry-run' \
  2> /tmp/missing.txt

UP=/var/tmp/forum-migration/uploads
grep -o "${UP}/[^ ]*\.attach" /tmp/missing.txt | sort -u | while read -r path; do
    rel=${path#"${UP}/"}
    mkdir -p "${UP}/$(dirname "${rel}")"
    curl -fsS -o "${path}" "http://YOUR-PC:8000/uploads/${rel}" \
        || echo "could not fetch ${rel}"
done
chown -R jaeronautics: /var/tmp/forum-migration
```

Then run the dry run again. It should report no problems at all, and the
`files` column should show a count against the posts that carry attachments.

The MyBB names are not an accident to be corrected: every upload is stored as
`post_<pid>_<time>_<hash>.attach` — the original bytes, renamed so the
webserver cannot serve or execute them. The name somebody actually chose, and
its type, are columns in the database, and those are what get sent to Discourse.

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

## 6a. Answered: the dates survive

A run of one real thread on 2026-09-23 posted 2017-03-22 and Discourse
recorded 2017-03-22, for every post that landed. That is the question the
spike existed for, and it is settled: posting on somebody's behalf with a
`created_at` works, and the archive can keep its own history.

The same run found three things that nothing short of a real run would have:

- **"You're replying a bit too quickly. Please wait 29 seconds."** Discourse
  makes a new account pause between posts, and during an import the second
  post somebody makes arrives a second after their first. It is now waited
  out when it happens, and the four `rate_limit_*_create_post` and
  `..._create_topic` settings are loosened for the window, because waiting
  thirty seconds a post across 1,500 posts is twelve hours of doing nothing.
- **"new users can only put one embedded media item in a post"** and
  **"only put 2 links in a post"**. Both were a mistake in the check rather
  than in the forum: it measured the post as its author typed it in 2017, but
  what gets sent is that text *plus* every attachment appended as markdown —
  a picture inline, anything else as a link. A post with four screenshots and
  no URLs in it arrives carrying four embedded media items. Attachments are
  counted now.

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

## 8a. Knowing which files are already on the server

Nine gigabytes fetched a directory at a time over several sittings is not
something anybody can hold in their head, and the import reports a missing file
per post — the right shape for one thread and the wrong one for 2,347. So ask:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
  /var/www/jaeronautics/.venv/bin/flask \
  --app aeronautics_members.app:create_app \
  check-forum-uploads /var/tmp/forum-migration/dump.sql.gz \
  --uploads /var/tmp/forum-migration/uploads \
  --missing-to /var/tmp/forum-migration/still-needed.txt
```

It counts what is there, what is not, and how many gigabytes that still is. It
also checks **sizes**, which matters more than it sounds: the usual way a file
goes wrong here is not absence. A web server asked for something it has not got
answers with a page saying so, and a fetch that does not check writes that page
to disk under the name of the file it wanted — present, readable, and 200 bytes
of HTML where a PDF should be. Those are listed separately and included in the
paths to fetch again.

With `--missing-to`, the paths land in a file that drives the copying:

```bash
UP=/var/tmp/forum-migration/uploads
while read -r rel; do
    mkdir -p "${UP}/$(dirname "${rel}")"
    curl -fsS -o "${UP}/${rel}" "http://YOUR-PC:8000/uploads/${rel}" \
        || echo "could not fetch ${rel}"
done < /var/tmp/forum-migration/still-needed.txt
chown -R jaeronautics: /var/tmp/forum-migration
```

Run the check again afterwards. It exits non-zero while anything is outstanding,
so it can go in front of the import in a script.

## 9. The whole board, keeping the categories it had

**This shape is for testing, not for keeping.** What the new forum should look
like — live categories per current lecture, an archive for everything else,
and who can see what — is [forum-structure.md](forum-structure.md). Mirroring
the old board is chosen here because it makes the result comparable with the
old one post for post, which is what a test import is for.

Rehearsing one thread answers whether it works. Moving all 720 answers whether
it works at scale, and the honest way to find that out is to do it — into the
old structure rather than a new one. Recreating the old shape is a deliberately
dull choice for a first full run: it makes the result comparable with the old
board post for post, and rearranging categories afterwards is something
Discourse does well and a migration script does badly.

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics sh -c '
  DISCOURSE_MIGRATION_API_KEY=$(cat /var/tmp/forum-migration/api-key) \
  exec /var/www/jaeronautics/.venv/bin/flask \
       --app aeronautics_members.app:create_app \
       import-forum-content /var/tmp/forum-migration/dump.sql.gz \
       --uploads /var/tmp/forum-migration/uploads \
       --ledger /var/tmp/forum-migration/ledger.jsonl \
       --adjust-settings \
       --settings-file /var/tmp/forum-migration/settings-before.json \
       --limit 20'
```

**Start with `--limit 20`.** Twenty threads is enough to see whether the
categories come out right and whether anything degrades at volume, and it is
small enough to delete and start again. Drop the limit once it looks right.

**Keep the ledger file.** It is what makes the run repeatable. Every post that
lands is written to it before the next is attempted, so a run that stops — and
one this long will stop — carries on rather than posting everything twice.
Running the same command again is the recovery procedure; there is no other
one. Delete the ledger only when deleting what it refers to.

### What it does to the categories

**Two levels, not three.** Discourse nests three deep only where the site
reports `max_category_nesting` and allows it to be set; a forum that does not
refuses the third level with *"You can't nest a subcategory under another"* —
one category at a time, after every setting has already been changed. So the
depth is decided before anything is sent, from what the site actually reports,
and two is the default.

The old board is a heading, then a degree, then a semester, then a lecture.
The lecture keeps its own level, because it is the thing anybody is looking
for, and everything above it becomes one name:

    Bachelor Luftfahrt / Aviation / 01 Semester
      └── 01-02 Luftfahrtrecht

Category names stop at 50 characters, and the board has lecture names past
seventy. Where a joined name is too long the **outermost** level is dropped
first: cutting the end would take the semester number, which is what anybody
navigates by, and keep "Studium", which nobody does. A lecture name that is
still too long is cut at the end, since the course code at the front is what
identifies it. Two lectures that would end up with the same name under the
same parent are kept apart rather than merged — otherwise one of them loses
its threads into the other.

Forums nobody ever posted in get no category of their own; their names still
appear in their descendants' parent, which is where they were doing any work.



Threads go oldest first, so a run stopped halfway leaves an archive that ends
somewhere sensible instead of one with holes through it.

### Before the unlimited run

- **All 9 GB of uploads** have to be on the portal, and room for them again on
  the forum. Discourse keeps optimised copies of images beside the originals.
- The whole thing is one long sequence of API calls. Run it under `tmux` or
  `screen`, or a dropped SSH session ends it — recoverable, but slower than
  not needing to recover.

### When nine gigabytes will not fit

The portal needs room for the whole `uploads` folder, and the forum needs room
for it again — Discourse keeps optimised copies of images beside the originals.
On Proxmox that is a resize and nothing more:

```bash
pct resize <ctid> rootfs +12G
```

ZFS reports a full dataset as `Disk quota exceeded` rather than "no space left
on device", so a full container looks like a permissions problem — even `chown`
fails, because it cannot write the inode.

**If growing the disk is not an option, do it in batches.** This is safe, and
only because of one rule: a post whose files are not on this machine is *not*
posted. It waits. Posting the words without the PDF and writing the post down
as done would lose that PDF for good — the ledger skips it on every later run,
and a post that arrived looking complete is the last thing anybody would think
to check.

So the loop is:

1. `check-forum-uploads --missing-to still-needed.txt`
2. fetch as many as will fit: `head -300 still-needed.txt` through the copy loop
3. run `import-forum-content` — the threads whose files are here land, the
   rest are reported as **waiting**, and nothing about them is written down
4. delete the files that have now been posted
5. back to 1

The summary line counts waiting separately from failed, because they are not
the same thing: waiting needs a file, failed needs a look.

`--allow-missing-attachments` turns the rule off, for files that are gone for
good and words worth having anyway. It is not a way to save disk: those files
can never be added afterwards.

### The webserver in front of Discourse

`max_attachment_size_kb` is only half the limit. The webserver answers first,
and its refusal looks nothing like Discourse's:

    POST /uploads.json failed (413): 413 Request Entity Too Large
    ...<center>nginx</center>

**190 of the 2,347 attachments are over 10 MB**, which is nginx's usual
default, and 32 are over 50 MB. So this is not a handful.

Raise `client_max_body_size` in the nginx in front of Discourse to something
above the largest file — 200m covers the 166 MB one. Where that setting lives
depends on how Discourse was installed; on the standard Docker install it is a
template change followed by a rebuild, not a file you edit in place, because
the container's nginx config is regenerated.

A post carrying a file the forum refuses **waits**, exactly as one whose file
is not on the machine does. Nothing about it is written down, so raising the
limit and running the same command again picks it up. The first time this
happened the posts went out without their archives and were recorded as done,
which is how a file stops existing quietly.

## 10. Checking a file the new forum shows oddly

Every upload is on disk as `post_<pid>_<time>_<hash>.attach`: the original
bytes under a name that says nothing. The name somebody chose is in the
database, so the uploads folder cannot be searched for it. Look it up:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
  /var/www/jaeronautics/.venv/bin/flask \
  --app aeronautics_members.app:create_app \
  inspect-forum-attachments /var/tmp/forum-migration/dump.sql.gz --thread 258
```

It prints, per attachment, the on-disk name, the name the old board recorded
and the type it recorded. `--name <fragment>` searches the whole board instead.

Then, because the `.attach` file is the original bytes whatever it is called:

```bash
file /var/tmp/forum-migration/uploads/201705/post_280_1493750319_....attach
```

That settles what a file actually is. A name ending `.JPG` on something `file`
calls a PDF is not a fault in the import — Discourse appends the extension
that matches the contents, and the contents win.
