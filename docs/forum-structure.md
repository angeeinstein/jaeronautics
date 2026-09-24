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

Fill in `target` where a forum belongs to a lecture that still runs. Leave it
empty for everything that is archive — the safe default, because getting it
wrong means a thread is filed one click further away rather than lost.

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

**The portal already drives three of these groups**, through Discourse
Connect, from membership state:

| Group | Who is in it |
|---|---|
| `members` | Active members |
| `member-onboarding` | Signed up, not yet active |
| (configurable) | Lapsed, if a group is named for it |

`_build_group_fields` sends both `add_groups` and `remove_groups`, and
`sync_user` pushes it through `/admin/users/sync_sso` rather than waiting for
a login. So a membership that lapses takes forum access with it, without
anybody remembering to do it. That is worth building on rather than around.

Two further groups are wanted and do not exist yet:

- **Staff / moderators** — the board, and whoever maintains this.
- **Companies and university staff** — people from outside the association
  who should see some of it. Which parts is an open question; the safe
  starting point is none of the lecture material.

### The starting position

Everything imported is **members only**. That is what the old board was, it is
what the material is, and widening access later is a decision anybody can make
in an afternoon — narrowing it after the fact is not, because by then it has
been indexed.

## What is still open

- The list of live lectures, from the current curriculum.
- What the company and university-staff group can see, if anything.
- Whether archive categories are visible to members or only on request.
- Whether the archive is one category or one per degree.

None of these block the test imports. All of them block the real one.
