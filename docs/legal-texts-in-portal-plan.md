# Legal texts managed in the portal

Agreed with the maintainer on 2026-11-04; built on the branch
`claude/legal-texts-in-portal`.

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
- **Who:** two permissions (decided 2026-11-04), both in the `admin` and
  `superadmin` bundles, so whoever is promoted to either has them without
  anything to remember (`permissions.py`: a role is a fixed bundle):
  **Legal texts** (`Permission.LEGAL_TEXTS`, `legal.texts`) -- Admin › Legal
  texts, uploading, writing and deleting drafts; **Publish legal texts**
  (`Permission.LEGAL_PUBLISH`, `legal.publish`) -- publishing and correcting.
  A role holding only the first -- a secretary, say -- belongs to the finer
  roles the maintainer wants later.
- **Team rules:** a team's leads upload and edit drafts of their own team's
  rules -- also one an admin started; the holders of *Publish legal texts*
  are told and publish. Leads never publish. Today the
  leads send the text to the association, which goes through GitHub. The
  leads see the draft they uploaded with its PDF, to pass round in the team,
  and are told when it is published (decided 2026-11-04).
- **Uploading, and an editor** (decided 2026-11-04, after the first build:
  the plan had said upload only). Uploading stays -- a file is convenient
  to write elsewhere, with an AI agent or outside help. The editor is
  described below ("The editor").
- **Kept:** in the database and so in the backups; the PDFs made as now
  (`legal_pdf.py`, kept and remade by the text's hash); who uploaded,
  corrected and published what, and when, in the audit log.
- **The files in `legal/` stay**, read-only, as the published versions they
  are (decided while building, 2026-11-04 -- the plan had said "imported
  once"): they are published and unchangeable already, with their history in
  git, so moving them would bring risk and nothing else. New versions --
  drafts, teams' uploads, what is published from now on -- are in the
  database; the portal reads both, and the newest published one whose day has
  come is in force, wherever it is kept. CI's checks of the files stay as they
  are.
- **General:** another association running the portal uploads its own
  statutes, instead of changing the repository.

## Approval, and what members see

Decided 2026-11-04: publishing asks for the day the text was approved and by
whom (the board, the general assembly -- whose the decision is depends on the
text: a change to the statutes is the general assembly's, a team's rules the
board's), kept with the version and in the audit log. A record, not a check:
the portal does not verify it, and it may stay empty for a text that needs no
approval (the legal notice, say). The files in `legal/` may say it too
(`approved_on`, `approved_by` in the front matter).

Three dates, which may differ: **approved** (the decision, outside the
portal), **published** (put into the portal -- the version's day, by which
acceptances are kept) and **applies from**. Members are shown one line under
the title, on the page and in the PDF:

> Gültig ab 01.12.2026 · genehmigt vom Vorstand am 10.11.2026

-- the second half only when an approval was entered. The publishing day
stays where it is needed: in the PDF's footer ("Fassung vom …") as the
version's name, and for the administrators.

## The editor

Decided 2026-11-04.

- **Where:** *Edit* on a draft opens a page of its own,
  `/admin/legal/drafts/<id>`, or for a team's leads
  `/teams/<team>/manage/rules/drafts/<id>`.
- **Write and Preview side by side** (two tabs on a phone). The preview is
  made by the server, as the published page is, half a second after typing
  stops -- never a second renderer that could differ. The checks of an upload
  run with it and are listed under the text. *Preview PDF* as before.
- **The front matter is fields** -- title, revision -- not YAML; German and
  English as two tabs, English optional.
- **A small toolbar** (heading, bold, italic, list, link) and a short Markdown
  cheat sheet. CodeMirror 6: line numbers, Markdown highlighting, search;
  loaded on this page only.
- **Starting a new version:** from the version in force (the usual case: a
  change to it), from the template, or by uploading a file as before.
- **A file draft in `legal/`** (the drafts from before) gets *Edit* too: the
  first edit copies it into the portal as a draft, and the file is no longer
  listed; the next batch deletes it from the repository.
- **Saving:** a draft saves itself a few seconds after typing stops. Two
  people saving the same draft: the second is refused and told, never
  overwriting silently. Nothing is public until *Publish*, which refuses a
  text the checks refuse.
- **Back, forward, discard** (the maintainer: a way "to get back to the old
  status"): undo and redo buttons (and Ctrl+Z / Ctrl+Y) for everything since
  the editor was opened; *Discard changes* goes back to the draft as it was
  when it was opened; *History* lists the earlier states, one per editing
  session, with who and when, each restorable (restoring is undoable too).
- **Changes:** beside the preview, the text compared word by word with the
  version in force, paragraph by paragraph -- added underlined green,
  removed struck through red (the `diff` library, in the browser). For
  whoever approves a change.
- **Correcting** a published version within its 24 hours opens the same
  editor, without autosave: the correction is saved with a button that asks
  twice, and goes into the log with the earlier wording.

## Telling the members

Decided 2026-11-05. A new version applies to everybody from its day: members
are told, never asked ("They just have to accept it"; only one version can
be in force). Publishing sends the email at once:

- *Tell the members by email* in the publish dialog, ticked by default for
  the texts accepted at signup (statutes, rules of procedure, membership
  terms, privacy policy) and a team's rules, not for the legal notice and the
  webshop terms. An optional *What changed* goes into the email.
- To all active members as a **notice** (it cannot be switched off, like the
  invitation to the general assembly); a team's rules to the team's active
  members as a team mailing. Both are mailings like any other (Admin ›
  Announcements, the team's Messages page): delivery status kept, the
  provider's limits kept.
- The email: which text, from which day, who approved it, what changed, and
  links to the text and its PDF. Nothing to accept.
- No fixed waiting period (the maintainer: not weeks before the portal can
  be updated). The privacy policy is information, not a contract: it may
  apply from the day it is published. The membership terms say how they
  change (§ 36 of the draft of 08.10.): material changes told "rechtzeitig"
  before they apply, by email or in the portal; changes for a coming fee
  period apply from that period (fees: § 11); silence is not consent where
  consent is needed. So: publish material changes to them a little ahead of
  their day. Not legal advice -- the board decides what is material.

## Where it is built

- `db_models.LegalTextVersion` (table `legal_text_versions`): a version
  uploaded here, German, with its English translation as a row pointing at it.
- `services/legal_texts.py` reads files and rows alike; `validity()` is the
  line under the title. `services/legal_uploads.py`: uploading, publishing,
  correcting, deleting, and the emails (`legal_text_draft_uploaded` to the
  holders of *Publish legal texts*, `team_rules_published` to the team's leads).
- API: `api/admin_legal.py` (`/admin/legal/drafts`, `.../publish`,
  `/admin/legal/versions/<id>/correct`), `api/team_manage.py`
  (`/teams/<slug>/manage/rules`, its drafts). A draft's PDF:
  `/admin/legal/waiting.pdf?record=<id>`, `/teams/<slug>/manage/rules/waiting.pdf`.
- Pages: `frontend/src/pages/admin/LegalTexts.tsx`,
  `frontend/src/pages/teams/manage/Rules.tsx` (team permission *Rules*,
  `team.rules`, held by the leads).
- The editor: `services/legal_editor.py`, `api/legal_editor.py`
  (`/legal/drafts`, `/legal/imports`, `/legal/drafts/<id>/session`, saving,
  `.../check`, `.../snapshots/<id>`, `.../correct`), the table
  `legal_text_snapshots`, `frontend/src/pages/legal/Editor.tsx` with
  `components/legal/MarkdownEditor.tsx` (CodeMirror 6, given the page's
  nonce for the security policy) and `components/legal/Changes.tsx`.
- Tests: `tests/test_legal_uploads.py`, `tests/test_legal_editor.py`, the
  pages' `*.test.tsx`, `frontend/e2e/admin-legal.spec.ts`.
