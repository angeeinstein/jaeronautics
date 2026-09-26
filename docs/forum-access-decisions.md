# Forum Access: The Decisions, and Why

A record of what was settled about groups and permissions, so that the reasons
survive the conversation they were worked out in. [forum-structure.md]
(forum-structure.md) says how the thing is set up and run; this says why it is
shaped that way, and what would have to be true again to change it.

Written down because most of these were arrived at by finding the obvious
answer to be wrong, and an obvious answer that has been refuted still looks
obvious to the next person.

## 1. The portal decides who; the forum decides what

**Who is in which group** is decided in the membership portal, because that is
where it is known whether somebody has paid and what kind of member they are.
**What each group may see** is decided in Discourse, per category, because that
is a judgement about material rather than a fact about a person.

The portal pushes group membership through Discourse Connect on every sync,
sending `add_groups` *and* `remove_groups` every time. Both halves always,
because access that is only ever granted is access nobody ever loses.

## 2. Three axes, not one

What somebody has paid for, what kind of person they are, and whether they run
the place are different questions. A lecturer is not a student whose membership
is in a different state. So each drives its own group and a category grants
whichever one it means.

| Axis | Group | Who is in it |
|---|---|---|
| Standing | `members` | active membership **and** an approved photograph — or the old forum's photo on a reclaimed account (§12) |
| | `members-onboarding` | active membership, photograph not approved yet |
| | `membership-inactive` | ran out, never paid, or account switched off |
| Kind | `forum_category_groups` | `Member.member_category`, **only while active** |

The kind mapping is one box per kind of member on the settings page, each
labelled with the kind and holding only the forum group's name. It is stored as
`kind = group` lines, which is what everything reads, but nobody types the
left-hand side: it is fixed — `student`, `alumni`, `staff`, `partner`,
`honorary` — so a box that could hold anything else was a box that could hold a
typo, and a typo there is silent. The right-hand side is a name somebody
chooses, and the group is made on the forum from it.

`staff` on the left is the portal's word for anybody employed at the institute,
a secretary as much as a lecturer. It is deliberately **not** reused as the
group name, because `forum_staff_group` and Discourse's own `staff` both mean
the opposite thing — whoever runs the forum. Three different senses of one word
is how somebody grants the wrong one.
| Runs the place | `forum_staff_group` | holds the portal's forum-admin role |

The standing groups are named for what is true of the people in them, because
those names end up on category permissions where the reader has nothing but the
name to go on. The onboarding group is `members-onboarding`, plural: they *are*
members, who have paid and are part-way through onboarding. The singular
`member-onboarding` read as "not a member yet", which is the opposite answer to
the only question the name gets asked. `members-awaiting-photo` was considered
and not chosen (2026-09-26).

## 3. The lecture material is **not** granted to `members`

This is the decision everything else bends around, and the one that was got
wrong first.

`members` means "has paid". A lecturer and a company representative are full
members of this association who pay the same fee — `MemberCategory` has said so
all along — and the material is a decade of exam papers and transcripts *about
the lectures they give*. Granting `members` would have shown every exam to the
people who set them.

So the lecture categories are granted to the **kind-of-member** groups, named in
`forum_lecture_groups`, which has no default. A run with it empty stops and says
why, because the obvious default is exactly that mistake.

`members` is still driven and still useful — it is the right group for a general
area where a lecturer and a student are equally welcome. It simply is not the
key to the archive.

## 4. Being in a kind group means two things at once

The lecture categories are granted to `students`, so being in `students` has to
mean *is a student here* **and** *has paid*. Discourse cannot express that: its
permission check is a union across groups, never an intersection, and there is
no "members AND students".

So the conjunction is made on the portal side. The kind group is held **only
while the membership is current**. A student whose membership lapses leaves
`students`, not only `members`. Without that, a lapsed membership would go on
reading every exam paper.

## 5. Discourse has no nested groups

There is no group-of-groups, so "permission groups that member groups belong to"
cannot be built. Category permissions always name the groups people are actually
in.

The indirection is real anyway, one layer up: the portal computes membership, so
a category granting `students` automatically covers everybody the portal decides
is a paid-up student. That is the same benefit without the hierarchy.

## 6. Three tiers of access

| | students, alumni | committee, staff | lecturers, companies |
|---|---|---|---|
| Live lecture | Create | Create | nothing |
| Archive | See | Create | nothing |
| Job board (when it exists) | as granted | Create | Create |
| Discourse's own categories | untouched | untouched | untouched |

Live lectures are writable because students keep adding exams and summaries; a
read-only lecture category makes the forum a museum rather than somewhere people
look things up and contribute back. The archive is readable because throwing it
away would be worse than keeping it filed, and not writable because nobody
should be adding to a lecture that stopped running in 2017.

## 7. Permissions go on before the posts, never after

A Discourse category with no group permission is **public** — not "visible once
logged in", public, to anybody and every crawler. So `import-forum-content
--mapping` applies permissions between creating the categories and posting the
first thread. An archive that was public for the two hours of an import has been
public.

## 7a. Children before parents

Discourse will not let a category be restricted while one of its subcategories
still admits a group the parent would not:

    Any group that is allowed to access a subcategory must also be allowed to
    access the parent category. The following groups have access to one of the
    subcategories, but no access to parent category: everyone.

Every category starts public, so restricting a semester before its lectures is
restricting a parent while eleven children still admit everyone. The first run
against a real forum set 97 categories and was refused all ten of the top-level
ones for exactly this. The plan is therefore ordered deepest first.

Anything refused anyway is tried once more in the opposite order, because a
*widening* needs the reverse: there the parent has to be opened before the child
is allowed to be.

## 7b. The authors may post while the import runs, and only then

The archive is posted *as* its authors, and Discourse checks each of them
against the category exactly as it would somebody typing. They are the old
forum's people -- in `old_forum` and their year group -- and not in `students`
or `alumni`, and they should not be: `old_forum` is for ever, and a lapsed
member would keep the archive through it. So with the categories closed before
the first post (§7), every post was refused. The refusal is the same
`invalid_access` a key bound to one user gets, which is why the first diagnosis
was the key.

So the import adds `old_forum` to every category it owns once they are closed,
and takes it away again when it finishes, whichever way it finishes. It is
added to and removed from what each category has *now*, so a permission set
somebody chose on the forum comes back exactly as it was. A run that is killed
outright leaves `old_forum` on; `forum-permissions categories.json --enforce`
takes it off again, along with anything else that differs from the plan.

## 8. The command closes what is open; it does not impose an opinion

`forum-permissions` writes only to categories that are still public — no
permissions at all, or still granting `everyone`. A category somebody has
deliberately configured is counted, reported, and left exactly as it is, because
that decision was made with knowledge the command does not have.

Its standing job is the narrow one worth doing forever: **nothing is ever left
open**. `--enforce` is the other mode, for the first run and for putting a forum
back to a known state on purpose.

Categories the mapping does not own — Discourse's `Uncategorized`, a job board,
a lounge — are never touched at all. Which categories are ours is decided by the
worksheet's own semester names.

## 9. Groups are made before anybody is put in them

`add_groups` in a Connect payload is not a way to *make* a group: Discourse
matches the names against the groups it already has and drops the rest without a
word. Thirty-four groups came out empty on a run that reported no failures
before this was understood.

## 10. A membership ending by the calendar is not an event

Every other change is something somebody causes, and each syncs the forum where
it happens — a payment, an account switched off, a photograph approved. Time
passing causes nothing: nobody clicks, no code runs, and the forum goes on
believing the last thing it was told.

So a daily timer compares what the portal says each member's state should be
against what their forum account records, locally, and syncs only the
differences. On an ordinary night it makes no API calls at all.
`reconcile-billing` does not cover this, because its query begins at members
with a Stripe reference and a membership paid by transfer has none.

## 11. Empty settings are said out loud

Four settings are empty by default, and each empty one is a silence rather than
an error — a run with them unset does something defensible and not what anybody
meant. `forum-permissions --dry-run` names them and says what goes wrong for
each, so "have I forgotten anything" is answered by the tool rather than by a
list somebody has to keep.

## 12. A reclaimed account's old photo counts as approved

Decided 2026-09-26, after the first real reclaim: a returning student paid and
stayed in `members-onboarding` with nothing to approve. Their old forum picture
is the face the association already showed, it is on their forum profile before
they ever sign in, and they uploaded nothing new -- so there is nothing to
review, and asking them to upload again would be the first thing they are told.

So a reclaimed account that brought a picture with it is treated exactly like
one with an approved upload. The member's page and the forum's groups ask the
same function (`ForumService.get_reclaimed_avatar`); they were two checks once,
and the page said "your forum access is ready" while the forum disagreed.

Somebody who reclaims an account with **no** picture -- 63 of the 740 --
uploads one and has it approved like anybody new. They may always replace the
old picture; a new upload goes through approval as usual.

## What is deliberately still open

- **The categories that do not exist yet**: Membership, Getting started, General
  discussion, a job board. They are Discourse's to make, and nothing here will
  touch them.
- **Whether a paid member with no approved photograph should be able to read.**
  Today they cannot: the kind group arrives only at `active`, which requires the
  photograph (or a reclaimed old one, §12). Defensible, and a real gate on 250
  people in October.
- **`login_required`**, which is what makes the site itself private. Category
  permissions are not the same thing.
- **Whether the committee and administrators should differ from each other.**
  One group today; the model takes a list.
