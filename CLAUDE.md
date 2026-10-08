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
  for unrelated parts), one push, one pull request.

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
- Each batch: a branch from the current `main`, one pull request, the
  maintainer tests on the test server and merges, the branch is deleted.
- The live server installs from `main`, the test server from the branch under
  test.

## Not losing context

Chats get long and are compacted; a new session starts with none of it. So
**what was agreed goes into the repository the moment it is agreed** -- not
when it is built:

- A small change: an item in `docs/todo.md`, written so that someone who never
  saw the conversation can build it (see below).
- A bigger feature or a design decision: a short note in `docs/` (or a section
  of the matching document there), linked from its to-do item.
- Something decided *against*, and why: the "Decided against" section of
  `docs/todo.md`, so it is not proposed again.
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
- No model names, credentials or members' personal data in commits, code or
  documents.
