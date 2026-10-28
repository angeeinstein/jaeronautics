# Automatic updates

How the portal is to stay current without anybody watching -- agreed with the
maintainer on 2026-10-28.

## Why

"This is generally the way I would like to go with this, that the whole thing
isn't dependent on me, myself and my knowledge, and isn't dependent on you as
an agent being there. And so that just normal students who don't have that
much IT knowledge could manage the server and keep everything secure."

The packages stay pinned, with hashes: the server then runs exactly what CI
tested, a tampered release fails the install, and a rollback brings back
exactly its own packages. What was missing is something that moves the pins.
Daily checks that email the admins were turned down: "if I then get a
notification email each day that some packages are not up to date, this would
be rather annoying over time."

## The chain

1. **Dependabot proposes, CI tests and merges** -- built, part 1 below.
2. **The server installs from `main` by itself** -- part 2.
3. **Ubuntu keeps itself current** -- part 3.
4. **A person only where no machine can decide**, told only then -- part 4.

## Part 1: Dependabot, CI, merging (built 2026-10-28)

On branch `claude/automatic-updates`; how it works is in
`docs/maintenance.md`, "Dependencies". Decided:

- Security fixes at once; everything else weekly, grouped per kind (Python,
  front end, CI's actions), and only releases at least **7 days** old.
- Merged by CI when everything passed: **security fixes, minor and patch
  versions**. A **major version waits for a person**. Exception: the actions
  CI uses are named by major version only, so every update of one is a major
  one; they are merged too, since the run that tested them used them.
- The lock files moved to the layout Dependabot rebuilds itself
  (`requirements.in` compiled into `requirements.txt`); pins and hashes
  unchanged by the move.
- A merge made by a workflow starts no CI run, so the merging job starts CI on
  `main` itself, and the servers' CI check counts that run.

Started first, before part 2, on purpose: it changes the repository, not what
the live server does on its own, and a few weeks of it show how often updates
come and whether CI catches the ones that break -- the evidence to have
before the live server installs updates unattended.

## Part 2: installing automatically (not built)

A setting in Admin › Settings › Updates, *Install updates automatically*.
Once a night the server checks whether `main` has moved and CI passed there,
and installs it -- everything on `main`, the maintainer's merges too. A failed
update undoes itself already (code, packages, database). Proposed, not yet
decided: on by default, at **04:30** (after the 03:15 jobs).

## Part 3: Ubuntu (not built)

unattended-upgrades installs Ubuntu's security updates already. The installer
is to make sure it is switched on, and to let it restart the server at night
when an update needs it (a kernel's) -- today nothing restarts the machine.
Proposed: at **04:00**; about a minute offline.

## Part 4: telling a person, only when needed (not built)

System health shows whether all of the above works (unattended-upgrades' last
run, updates waiting, a restart pending), and something stuck: a security
update waiting more than 14 days, an update CI failed. Only that sends an
email, once. Also the support ends, a year ahead: Ubuntu 24.04 (standard
support until April 2029), Node.js 24 (April 2028), with a step-by-step guide
for someone without IT knowledge to move to the next release -- a new server,
`install.sh`, then the backup restored (`docs/maintenance.md`, "Rebuilding
the Portal Somewhere Else").

## Outside the code

The repository belongs to the maintainer's personal GitHub account. For the
association to keep it without them, it is to move to a GitHub organisation of
the association with at least two owners (the free plan is enough). The
maintainer's to decide and do.
