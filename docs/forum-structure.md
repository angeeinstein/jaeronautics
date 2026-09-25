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

**Three axes, not one.** What somebody has paid for, what kind of person they
are, and whether they run the place are different questions with different
answers — a lecturer is not a student whose membership is in another state —
and a category that wants only one of them should be able to say so. So each
axis drives its own group, and a category grants whichever it means.

| Axis | Group | Who is in it |
|---|---|---|
| Standing | `members` | active membership **and** an approved photograph |
| | `members-awaiting-photo` | active membership, photograph not approved yet |
| | `membership-inactive` | ran out, never paid, or the account was switched off |
| Kind of member | `forum_category_groups` | `Member.member_category`, **while active** |
| Runs the place | `forum_staff_group` | a portal role |

The first three are named for what is true of the people in them, because
those names end up on category permissions in Discourse and whoever reads one
there has only the name to go on. In particular `members-awaiting-photo` is
**not** "has not paid": it is "has paid, and the photograph is not approved
yet", which is the opposite answer to the question the old name got asked.
`membership-inactive` covers all three ways of not being current, so it is not
called "expired", which is only the commonest of them.

The portal already knows what kind of member somebody is — student, alumni,
staff, partner, honorary — so `forum_category_groups` is a line per kind under
**Admin → Settings → Forum**:

    student = students
    staff   = lecturers
    partner = companies

A kind with no line is in no such group, which is what "we have not decided
about them yet" should look like. Every group in the mapping is named on every
sync — one to join, the rest to leave — so a student who becomes an alumnus
moves between them without anybody touching the forum.

**The division of labour is deliberate.** Who is in which group is decided
here, because this is where it is known whether somebody has paid and what
they are. What each group may see is decided in Discourse, per category,
because that is a judgement about material rather than a fact about a person.

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

People from outside the association are sorted by the same mapping — a
`partner = companies` line makes and fills the group. What they should see is a
real question, and the answer that cannot be wrong while it is unanswered is
"not the lecture material", which is what they get by default.

### What is made for you, and what is not

| | |
|---|---|
| The groups | made by the import, and by `forum-permissions` |
| Lecture and archive permissions | set by the import, before it posts anything |
| The settings above | **yours to fill in**, and the run stops without the first |
| The categories nobody has made yet | **yours**, in Discourse |

`forum-permissions --dry-run` says which of the settings are still empty and
what goes wrong for each, so the answer to "have I forgotten anything" comes
from the tool rather than from a list somebody has to keep.

Each of them is empty by default because empty is a silence rather than an
error: no group to grant the material to, no way to tell a lecturer from a
student, nobody named as running the place, and people whose membership is not
current put nowhere at all. A run with them unset does something defensible and
not what anybody meant.

## Who may read what

`flask forum-permissions categories.json` is the whole of it, and
`import-forum-content --mapping` does the same thing itself once the categories
exist and **before it posts anything into them**.

**Not the member group.** That is the obvious answer and the wrong one: a
lecturer and a company representative are full members of this association who
pay the same fee, and the material is a decade of exam papers and transcripts
*about the lectures they give*. Granting `members` would show every exam to the
people who set them.

So the lecture material is granted to the **kind-of-member** groups, named in
`forum_lecture_groups`:

| | students, alumni | staff / committee | lecturers, companies |
|---|---|---|---|
| Live lecture | Create — start topics and reply | Create | nothing |
| Archive | See — read and search only | Create | nothing |
| A job board, when it exists | whatever you grant it | Create | Create |
| Uncategorized, Site Feedback | untouched | untouched | untouched |

Being in one of those groups therefore has to mean two things at once — *is a
student here* **and** *has paid* — and Discourse cannot express that. Its
permission check is a union across groups, never an intersection. So the
conjunction is made on this side: the kind-of-member group is held only while
the membership is current, and a student whose membership lapses leaves
`students`, not only `members`.

The three tiers that follow from it:

- **Committee and administrators** see everything, through the staff groups.
- **Student members** see the lecture material and the archive.
- **Lecturers and companies** see none of it. What they should see — a job
  board is the likely first thing — is a category made in Discourse and
  granted to their groups. `forum-permissions` will not touch it, because it
  is not under a semester or the archive.

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

**It does not undo decisions made on the forum.** By default it writes only to
categories that are still *public* — no permissions at all, or still granting
`everyone`. A category somebody has deliberately given permissions to is
counted, reported and left alone, because that decision was made with
knowledge this command does not have. Its standing job is the narrow one worth
doing forever: nothing is ever left open. `--enforce` is the other mode, for
the first run and for putting a forum back to a known state on purpose.

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

### People who cannot see the material

Three states cannot read the lectures: awaiting a photograph, inactive, and
never a member. Showing them an empty forum is how "the forum is broken"
reaches the committee's inbox, so the states have groups of their own and the
groups can be granted something small.

They need different things said to them, and Discourse cannot hide one topic
from one group inside a shared category — only whole categories. So:

| Category | Granted to | What is in it |
|---|---|---|
| About the association | everyone, or all logged-in groups | who this is, what the forum is for |
| Getting started | `members-awaiting-photo` | how to upload a photograph and how long approval takes |
| Membership | `membership-inactive` | how to renew, who to ask |

Starting with **one** category granted to both of the latter two groups is also
a fair answer — two pinned topics, and each person reads the one that applies.
Splitting it later is a new category and one changed grant. What is not a fair
answer is granting them nothing, which is the current default and reads as a
broken site.

### Adding a kind of person later

Nothing here has to be redone.

1. The portal gains the member category, if it is genuinely new (that is an
   edit to `member_categories.py` and a migration — one place, by design).
2. Add a line to `forum_category_groups`. The group is made on the next run of
   `forum-permissions` or the next import, and filled on each person's next
   sync.
3. In Discourse, grant that group whatever it should see, on the categories it
   should see. Nothing else changes: the other groups' permissions are
   untouched, and no post moves.

The one thing that is not free is *narrowing* what an existing group sees,
because by then they have seen it.

### New categories and new topics

**Topics need nothing.** A topic has no permissions of its own; it inherits
the category's. So every exam a student posts next March is members-only
because the category is, with nobody deciding anything.

**Categories are staff-only to create** in Discourse, so this is about what the
committee does, not what members do. A subcategory made in the UI picks up its
parent's security settings, but one made any other way starts public — so after
any structural change, run:

    flask forum-permissions categories.json --dry-run

It reports what every category grants now, including how many are open to
anybody, and it is safe to run as often as you like: a category already correct
is not written again.

## What is still open

- What the company and university-staff group can see, if anything.
- Whether `login_required` is on, which is what makes the site itself private.

None of these block the test imports. All of them block the real one.
