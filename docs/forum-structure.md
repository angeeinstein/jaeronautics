# What the New Forum Should Look Like

The old board is not the plan. It is thirteen years of a filing system that
grew a folder per lecture per intake, and recreating it would recreate the
filing rather than the archive. The new forum is somewhere students look
things up — so the measure is whether a second-year can find last year's exam
in two clicks, not whether the tree matches.

This is the destination. It is written down because it has to be agreed before
the real import, not discovered during it.

## Two kinds of category

**Live.** One per lecture that still runs. Members can read *and* write: the
point is that students keep adding exams and summaries, so these are working
categories, not a museum.

**Archive.** Everything else — lectures that no longer run in that form, or at
all. Closed, so nothing new lands there, and out of the way. Nobody needs to
look at a 2016 homework sheet regularly, but throwing it away would be worse
than keeping it filed.

Which is which is a curriculum question. `flask forum-category-worksheet`
lays out the evidence: every old forum, how much is in it, and **when it
stopped**. A forum whose last post is from 2017 is not a live lecture. Every
year's version of the same course sits on adjacent lines, so the merges
propose themselves.

Two things on the page do most of the work:

**"Archive all of those"**, with a year. Most of a decade-old board is lectures
that stopped running, and deciding those one at a time is the bulk of the
effort for none of the judgement. One button clears them — only the ones not
yet decided, and every one can still be changed afterwards.

**The names line.** The hard cases are courses that still run under a similar
name but changed hands, because then the old exams are no longer worth putting
in front of anybody. Checking that by opening exams and comparing them is hours
of work; the lecturer's name is already in the subjects and the filenames —
`Exam Haselgruber`, `1. Termin AVF, Flöhr am 13.1.25` — so the page shows which
names appear lately and which appear only in the older material:

    names — lately: Flöhr · earlier only: Löffler

That is a heuristic on capitalised words, not a record of who taught what, and
it is labelled as one on the page. Three things are excluded from it, because
each is a way of being confidently wrong:

- **Everybody who was ever on the board.** A name in a filename is as likely to
  be the student who typed the thing up as the person who taught it —
  `Transcript_Robert_Niedergrottenthaler.docx` and
  `English_Meeting_Trinker_Steindl_Niedergrottenthaler.docx` are three
  students — and all of them are in the old board's user table, so all of them
  can be left out.
- **The lecture's own words**, taken from the name of the forum, so that
  `Grundlagen` and `Flugzeugentwurfes` are never candidates.
- **The vocabulary every exam thread uses** in both languages — `Klausur`,
  `Zusammenfassung`, `Transcript`, `Meeting`.

What is left is right often enough to turn "open every exam in this lecture"
into "read one line", and where it is unclear the evidence is one click away
under *What is in it*, every subject and filename now carrying its year,
newest first.

### Looking at the documents themselves

Where the names line is not enough, the answer is to look at this year's exam
and the old one together. Every upload is on disk as
`post_1234_1490000000_abcdef.attach` — the original bytes under a name that
says nothing and an extension no browser will render — so the files have to be
served rather than linked to. One command does both, the page and the files:

```bash
sudo -u jaeronautics env PYTHONPATH=/var/www/jaeronautics \
     /var/www/jaeronautics/.venv/bin/flask --app aeronautics_members.app:create_app \
     serve-forum-worksheet /var/tmp/forum-migration/dump.sql.gz \
     --uploads /var/tmp/forum-migration/uploads
```

The worksheet is at `/`, every attachment is under `/files/`, and the page
already knows that — there is nothing to fill in. Each PDF and image gets a
button carrying its year; open one, open another, and they sit side by side at
the bottom of the page. The real name and type come out of the dump, so a
`.attach` file arrives as `Prüfung_Flöhr_2025.pdf`, inline.

**It answers to anybody who can reach it, with thirteen years of exam papers
and no password.** Two ways to use it, and the choice is a real one:

- **Default (`127.0.0.1`) plus a tunnel from your own machine.** Nothing is
  exposed to the network at all:

  ```bash
  ssh -N -L 8765:127.0.0.1:8765 you@jaero-test
  ```

  then open `http://127.0.0.1:8765/`. The `localhost` in that URL is *your*
  machine; ssh carries it to the server.

- **`--host 0.0.0.0`**, and it is reachable at the server's own address from
  anywhere that can route to it — across subnets, without ssh. Simpler, and it
  means anyone on those networks can read the archive while it runs. On a
  trusted LAN for an afternoon that may be the right trade; make it knowingly.

Either way, stop it when the curating is done. It is a tool for an afternoon,
not a service. Nothing is served that the dump does not list, so paths outside
the board — including `..` — are 404s rather than files.

Note that decisions are saved per address: work done at
`http://127.0.0.1:8765/` is not the same store as work done in a page opened
from a file. Export before switching.

### Splitting the work between people

**Export JSON**, hand the file to somebody else, and they press **Import JSON**
and carry on. Only decided rows travel; where their file disagrees with a
decision already on the page, the page says how many and asks before taking
theirs. The curriculum travels with it too, so the next person does not retype
it — unless they have already written one, in which case theirs is kept.

That is how 274 forums get decided by several people who each know part of the
curriculum, rather than by one person who has to know all of it.

Fill in `target` where a forum belongs to a lecture that still runs. Leave it
empty for everything that is archive — the safe default, because getting it
wrong means a thread is filed one click further away rather than lost.

## Importing into that structure

`import-forum-content --mapping categories.json` reads the worksheet's export
and builds the forum from it. Without `--mapping` the old board's own
categories are recreated, which is a shape for testing rather than one to keep.

What it makes, within Discourse's two levels:

    Bachelor 4. Semester            the semester, as the curriculum writes it
      └── Angewandte Thermodynamik  the lecture, holding every year of it

    Archiv
      ├── Bachelor                  everything retired, by degree
      └── Master

A lecture category holds every year of that course at once — three old forums
becoming one `Angewandte Thermodynamik` is the point of the exercise. An
archived topic, by contrast, sits in a category shared with eighty other
retired lectures, so **it carries its old lecture in its title**:
`Klausuren (02-09 Angewandte Mathematik 2)`. A live topic keeps its subject
unchanged wherever it can, because the category already says what the suffix
would.

Three ways a thread can end up somewhere nobody chose, all of which archive it
and say so rather than dropping it: a forum the mapping never mentions (the
dump is the authority on what exists), a row never decided, and a target with
no semester in front of it.

### Trying the structure on a forum that already has content

    --categories-only

makes the categories and posts nothing. The structure is what changes when the
mapping changes; posting is the part every run so far has proved. Separating
them means a new structure can be tried against the forum you already have —
where every title is taken, and posting would be 720 refusals that teach
nothing. Such a run touches no site settings either, because it sends no posts,
and the empty categories can be deleted afterwards.

## Groups

Discourse grants permission to **groups**, not to people, and a category
carries a permission per group at one of three levels:

| Level | What it allows |
|---|---|
| See | Read the category and its topics, and nothing else |
| Reply | See, and reply to what is there |
| Create | See, reply, and start new topics |

A category with no group permission at all is public. Making one members-only
is therefore two things: grant the members group, and **take `everyone` away**
— granting a group does not remove anyone.

**The portal drives these groups**, through Discourse Connect, from what it
already knows about the person:

| Group | Who is in it | Decided by |
|---|---|---|
| `members` | Active members | membership coverage, by date |
| `member-onboarding` | Signed up, not yet active | membership state |
| `forum_inactive_group` | Lapsed, if a group is named for it | membership state |
| `forum_staff_group` | Whoever administers the forum here | a portal role |
| `partners` | Companies and university staff | by hand; granted nothing yet |

**This is not an import step.** `_build_group_fields` sends both `add_groups`
and `remove_groups` on *every* sync, and `sync_user` pushes it through
`/admin/users/sync_sso` rather than waiting for a login. So a membership that
lapses takes forum access with it at the next sync, and somebody who joins or
leaves the committee gains or loses their forum standing the same way — without
anybody opening the forum's admin pages. Access that is only ever granted is
access nobody ever loses, which is why both halves are always sent.

The staff group is off by default: set a group name under **Admin → Settings →
Forum** and it starts being driven by the same role that opens the forum queue
in the portal. Leave it empty and nothing is said about it either way, so a
group maintained by hand on the forum stays maintained by hand.

`partners` is made and granted nothing. What people from outside the
association should see is a real question, and the answer that cannot be wrong
while it is unanswered is "not the lecture material".

## Who may read what

`flask forum-permissions categories.json` is the whole of it, and
`import-forum-content --mapping` does the same thing itself once the categories
exist and **before it posts anything into them**.

| | members | staff |
|---|---|---|
| Live lecture | Create — start topics and reply | Create |
| Archive | See — read and search, nothing more | Create |
| Everything else | untouched | untouched |

Live lectures are writable because students keep adding exams and summaries; a
read-only lecture category would make this a museum rather than somewhere
people look things up and then contribute back. The archive is readable because
throwing it away would be worse than keeping it filed, and not writable because
nobody should be adding to a lecture that stopped running in 2017.

"Everything else" is Discourse's own categories — `Uncategorized`, `Site
Feedback` — which are counted and reported and never touched. What belongs to
this arrangement is decided by the worksheet's own semester names, not by
guessing.

Run it with `--dry-run` first: it reports what each category grants now,
including how many are still open to anybody.

### The starting position

Everything imported is **members only**. That is what the old board was, it is
what the material is, and widening access later is a decision anybody can make
in an afternoon — narrowing it after the fact is not, because by then it has
been indexed.

That word is exact. A Discourse category with no group permission on it is not
"visible once you are logged in" — it is public, to anybody and to every
crawler. So this is not a hardening step for afterwards: the permissions go on
between making the categories and posting into them, because an archive that
was public for the two hours of an import has been public.

Category permissions are also not the same thing as a private forum. With
`login_required` off, an anonymous visitor still reaches the site and anything
still public on it.

## What is still open

- What the company and university-staff group can see, if anything.
- Whether `login_required` is on, which is what makes the site itself private.

None of these block the test imports. All of them block the real one.
