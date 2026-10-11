# Working on this repository

The membership portal of Joanneum Aeronautics: Flask and a JSON API
(`aeronautics_members/`, `aeronautics_members/api/`), a React front end
(`frontend/`), the installer and updater (`install.sh`). How the front end is
laid out: `docs/frontend-structure.md`; its look: `docs/design.md`.

**Read `docs/todo.md` at the start of every session.** It holds what is agreed
but not built yet, with the context each item needs.

## How we work

### Small changes go on the to-do list

A small, isolated change the maintainer asks for -- a button's size, a wording,
a link's name -- is **not built on the spot**. It is written into
`docs/todo.md` (see "Writing an item" below) and built with the next batch, so
the conversation can go on without waiting for test runs.

Built at once instead:

- when the maintainer says it is needed now;
- when a later step depends on it;
- anything that is not small: a feature, a fix for the live site, anything
  where waiting costs something.

### When the list is worked through

A **batch** is started -- and then **every open item on the list is done**,
the list is empty afterwards -- when the first of these happens:

1. the maintainer asks for something that is built now (see above): the list
   goes into the same batch;
2. the oldest open item is **7 days** old (each item carries the date it was
   added);
3. the list has **8** open items;
4. the maintainer asks for it.

Only an item the maintainer has explicitly put off ("later", "blocked by …")
stays. At the start of a batch the list is shown to the maintainer, who may
strike, add or mark something urgent.

### Checks

- **While working**, after each change: the quick checks of what was touched
  (type check, lint, that part's unit tests). Seconds, not minutes.
- **Once per batch, at the end**: everything below, then one commit (or a few
  for unrelated parts), one push -- the pull request comes later (below).

```sh
ruff check .
python -m pytest -q -n 4
cd frontend
npm run typecheck && npm run lint && npx prettier --check src e2e
npx vitest run && npm run build
npx playwright test
cd .. && python scripts/visual/snapshot.py shoot <a scratch folder>   # the screenshot checker
```

After API changes: `flask --app aeronautics_members api-schema --out
frontend/src/api/openapi.json`, then `npm run api:types` in `frontend/`.

### Branches and releases

- Never push to `main`; it is protected (pull request, CI green).
- A branch per feature or batch, from the current `main`, named after what it
  holds (`claude/credit`, `claude/account-overview-fixes`) -- at least roughly:
  a batch of small items is named after its main theme. The branch is pushed
  and the maintainer tests it on the test server (the command below); small
  changes found there go onto the same branch. **The pull request is opened
  only when the maintainer says the branch is ready** (or asks for one) --
  "Maybe there are some small changes I still want to make after having a
  look at the new features." One pull request per branch; the maintainer
  merges; the branch is deleted (GitHub does it at the merge). The next
  feature or batch gets a new branch.
- The to-do list is pushed on the branch of the next batch: the first item
  written after a merge opens that branch.
- The live server installs from `main`; the test server from the branch under
  test. Once a branch is pushed, the maintainer is given the one command that
  points the test server at it (and the pull request says it again):
  `sudo bash /var/www/jaeronautics/install.sh --mode update --branch <branch>`.

## Not losing context

Chats get long and are compacted; a new session starts with none of it. So
**what was agreed goes into the repository the moment it is agreed** -- not
when it is built:

- A small change: an item in `docs/todo.md`, written so that someone who never
  saw the conversation can build it (see below).
- A bigger feature or a design decision: a short note in `docs/` (or a section
  of the matching document there), linked from its to-do item.
- Something the maintainer turned down, and why: the "Turned down" section of
  `docs/todo.md`. It is a record, not a ban: it may be suggested again,
  saying what has changed since. Only what the maintainer explicitly says
  must never change is written down as such.
- A standing preference of the maintainer: "Standing preferences" below.

The list is pushed with the working branch whenever it changes. Until that
branch is merged, `main` has an older list: a new session also looks at open
pull requests from `claude/*` branches and takes the newest `docs/todo.md`.

### Writing an item

Each item has: the **date added**, **what** to change, **where** (page,
address, file), **why** -- the maintainer's words where they matter -- what
was **decided** in the conversation (choices, wording, things ruled out), and
**open questions**, if any. A screenshot the maintainer showed is described in
words: it will not be there later.

## Standing preferences

- Dark mode only, English only, minimal wording.
- Modern, no compromises: take the time to do it right.
- The portal should not only work but look good; use what the React front end
  can do (live updates, animation) where it helps -- every animation off when
  the device asks for less motion.
- Square, flat look (`docs/design.md`).
- Now rather than later: what can happen at once happens at once (on the
  event, a webhook, a live update), not at a scheduled time. A schedule is
  for what must not run too often -- emails, many requests to Stripe or the
  forum -- and as a safety net behind the immediate path. At once means
  started right away in the background, not making a page wait for Stripe
  or the forum. Steps tied to a date stay at 03:15 on purpose: it gives
  Stripe's own around-midnight changes time to arrive.
- Features stay general where it costs this association nothing. Other
  associations may use the portal one day, so no options only this
  association or one of its teams would use ("that would just confuse other
  people"). But nothing is made worse for this association for that
  possibility; it is kept in mind, not designed for.
- No model names, credentials or members' personal data in commits, code or
  documents.
