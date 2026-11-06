# The English of the legal texts, translated by machine

Agreed with the maintainer on 2026-11-05. Built on `claude/legal-translation`.

## Why

A version's English translation was a second text: written, uploaded,
edited and corrected beside the German. The English is non-binding anyway
(every English page says the German applies), so keeping two texts right is
work without much gain. The maintainer: "we wouldn't save any version
history for the English version, therefore less complexity. It would always
be live translated from German."

## What

- **Only the German is written.** The editor has no English tab any more;
  uploads, corrections and *Replace with a file* take the German file only.
- **The English is made from it** by a translation service, paragraph by
  paragraph: each block of the German page (a paragraph, a heading, a list,
  a table) is translated as HTML -- headings, `§` anchors, links, lists and
  tables stay as they are -- and the result is kept by the block's exact
  text (`legal_translations`). A change to § 3 translates § 3 again, nothing
  else, so even a long text costs little per change.
- **In the editor** an *English* view beside *Preview* and *Changes* shows
  the translation of the draft as written, a few seconds after typing stops.
- **Published, the English is fixed** with its version (the English row of
  `legal_text_versions`, marked `machine_translated`): a member who accepted
  a version reads the English of that version, and it does not change because
  a translator improved. A correction within the 24 hours translates it
  again. Publishing never waits for it: the translation is outbox work
  (`services/outbox.py`), started at once and retried while the service is
  down; meanwhile the English page says there is no English of this version
  yet -- the German applies anyway.
- **Marked as a machine translation** on the page and in the PDF: made by
  machine, may contain mistakes, the German applies where they differ.
- **No corrections by hand** to the English (decided): a **glossary** under
  Settings keeps terms right -- Verein → association, Vorstand → board,
  Generalversammlung → general assembly, and so on.
- **Gender-neutral English:** the German says "sein Verhalten" of a member;
  DeepL is told, as context with every text (not translated, not counted),
  that the English uses "they/their". Azure has no such setting.
- **A paragraph's own number** (`i)`, `a)`, `3)`) is taken off before
  translating and put back as written: DeepL wrote some as `(i)` and left
  others (first test, 06.11.).
- **The texts from before:** published English versions (04.10.) stay as
  they are, accepted alongside their German. The hand-made English drafts
  of 08.10. are replaced by the machine translation when published (decided).
- **The draft's PDF** is German only; a published version's PDF has its
  English as before.

## The services

- **DeepL API** (Free: 500,000 characters a month -- the account set up in
  November 2026 shows 1,000,000 instead, possibly once rather than monthly;
  a key ending `:fx` is a
  Free key, sent to `api-free.deepl.com`) and **Azure AI Translator** (free
  tier: 2,000,000 a month; key and region). Both can be set up at once, like
  the email accounts: one is used first, the other is the **fallback** when
  the first fails or is out of characters (decided: "support both … and make
  it switchable … maybe even have the other as fallback").
- Settings › Translation (`settings.credentials`): the keys (shown only as
  set), which goes first, *Try it* per service, DeepL's characters used this
  month, and the glossary. DeepL takes the glossary as a glossary of its own
  (made anew when it changes; DeepL is asked which it holds, so none is
  left over -- a Free account takes only a few); Azure as its dynamic
  dictionary, for the words as written.
- The texts are public and hold no personal data: the privacy policy does
  not change.

## Where it is built

- `services/translation.py`: the two services, the order and fallback, the
  kept translations (table `translations`), the glossary, the cleaning of
  what comes back (an allow-list of the pages' own tags).
- `services/legal_translation.py`: a version's English -- made at publishing
  and after a correction as outbox work (`legal_translate`), kept as the
  English row of `legal_text_versions` (`machine_translated`, `source_hash`);
  `legal_texts.blocks()` cuts the German page into what is translated.
- API: `/admin/settings/translation` (and `.../<service>/try`);
  `/legal/drafts/<id>/english` for the editor's view.
- Pages: `frontend/src/pages/admin/settings/Translation.tsx`; the editor's
  *English* view in `frontend/src/pages/legal/Editor.tsx`.
- Migration `e3c8a1f6b9d2`. Tests: `tests/test_translation.py`, the English
  in `tests/test_legal_uploads.py` and `tests/test_legal_editor.py`, the pages'
  `*.test.tsx`.

## Not built

- Keeping text typed in the last seconds before an editor lost its lock
  (taken over, laptop closed): at most the two seconds before the autosave;
  and the correction window, which has no autosave, "will likely never even
  be used at all" (the maintainer, 2026-11-05).
