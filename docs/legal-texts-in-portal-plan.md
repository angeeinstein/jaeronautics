# Legal texts managed in the portal

Agreed with the maintainer on 2026-11-04. **Not built yet**; the to-do item
is in `docs/todo.md`.

## Why

Today the association's legal texts and every team's rules are Markdown files
in `legal/` in the repository (`services/legal_texts.py`, `docs/maintenance.md`,
"Legal texts"). A new version, even a draft to pass round, means a commit, a
pull request, CI, a merge and an update of the server. TDT's rules of
2026-11-04 took exactly that path. The maintainer: it "would make life a lot
easier, especially for people who are not used to GitHub" not to need it.

**Turned down: the server pushing the files to GitHub.** The web process would
need a token that may write the whole repository (GitHub cannot narrow one to
a folder); a compromised web process could then put code into `main`, which
the automatic installs carry to every server. And it would not be faster: it
would still be a pull request, CI, a merge and an update, behind a button.

## What

The texts are the association's content, not the program, so they live where
its other content lives: in the database, managed under Admin › Legal Texts.

- **Uploading a draft:** the Markdown file, and its English translation if
  there is one. The checks and the preview with ENTWURF that the page has
  already are kept; the file is kept too, instead of thrown away. A draft is
  seen by whoever may manage legal texts, with its PDF, at once.
- **Publishing:** a button, with the day it applies (`effective_from`). From
  that day applicants accept it and members see that it changed -- as now.
  Deliberate, since it cannot be taken back: a button that asks again
  (`ConfirmButton`), no second person needed (the maintainer: "for now").
- **A published version is never changed or deleted**, with one exception
  (below). The portal keeps which version each member accepted, so that text
  has to stay. A change after that is a new version.
- **The exception -- a correction:** a published version may be corrected
  while **nobody has accepted it** (a typo found a minute after publishing
  needs no new version). Whether somebody has: the association's texts, from
  the acceptances kept at signup; a team's rules, from the memberships'
  `terms_version`. Every correction is in the audit log with the earlier
  wording. And at most **24 hours** after publishing (decided 2026-11-04):
  some texts are accepted by nobody (the legal notice, the webshop terms) and
  could otherwise be changed silently for ever.
- **Who:** a new permission, **Legal texts** (`Permission.LEGAL_TEXTS`), in
  the `admin` and `superadmin` bundles, so whoever is promoted to either has it
  without anything to remember (`permissions.py`: a role is a fixed bundle). A
  role holding only it -- a secretary, say -- belongs to the finer permissions
  the maintainer wants later.
- **Team rules:** a team's leads upload drafts of their own team's rules; the
  association's admins (the permission above) are told and publish. Today the
  leads send the text to the association, which goes through GitHub. The
  leads see the draft they uploaded with its PDF, to pass round in the team,
  and are told when it is published (decided 2026-11-04).
- **Upload only.** An editor in the browser, with a live preview, may come
  later (the maintainer: "uploading only is enough").
- **Kept:** in the database and so in the backups; the PDFs made as now
  (`legal_pdf.py`, kept and remade by the text's hash); who uploaded,
  corrected and published what, and when, in the audit log.
- **The files in `legal/`:** imported once by the update that brings this, so
  nothing is lost; after that the portal is the only place. What becomes of
  the folder and of CI's checks of it is decided when building (kept as the
  import's source until every server has run it, then removed).
- **General:** another association running the portal uploads its own
  statutes, instead of changing the repository.

## Approval by the board

Decided 2026-11-04: publishing asks for the day the association's board
approved the text ("approved by the board on …"), kept with the version --
shown in its history, and in the audit log. A record, not a check: the portal
does not verify it, and it may stay empty for a text that needs no approval
(the legal notice, say). Team rules and the association's own rules usually
do need it (TDT's rules, § 19).
