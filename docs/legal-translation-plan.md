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
  Free key, sent to `api-free.deepl.com`; British English) and **Azure AI
  Translator** (free tier: 2,000,000 a month; key and region; one English,
  `en`, with no British variant). Both can be set up at once, like
  the email accounts: one is used first, the other is the **fallback** when
  the first fails or is out of characters (decided: "support both … and make
  it switchable … maybe even have the other as fallback").
- Settings › Translation (`settings.credentials`): the keys (shown only as
  set), which goes first, *Try it* per service, DeepL's characters used this
  month, and the glossary. DeepL takes the glossary as a glossary of its own
  (made anew when it changes; DeepL is asked which it holds, so none is
  left over -- a Free account takes only a few; each installation's carry
  its address in their name, so the test and live servers may share a key);
  Azure as its dynamic
  dictionary, for the words as written.
- **English that waits** -- published before a service was set up, its
  tries run out -- is made as soon as a key is saved.
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

## Added afterwards (2026-11-07)

- **Translate again / Remove the English** per version published in the
  portal (Admin › Legal texts, `legal.publish`), asked for by the
  maintainer: "a button to scrap an English translation or redo it". Again:
  the kept translations of its paragraphs are forgotten, so the services are
  asked anew -- also the way to replace an English a fallback made, or a
  hand-made one from before. Removed: not made again by itself.
- **A sentence over several paragraphs** ("verpflichtet sich jedes Mitglied,"
  / "i) sich … zu verhalten;"): each paragraph is still translated on its
  own, and DeepL is told of the other as context (not counted). Tried first
  (2026-11-17): sent as one text -- DeepL ran the paragraphs into each other
  ("each membercommits to"), wrote "his conduct" again and missed the glossary
  ("club"); so not that. If the doubled "to" stays, it is accepted: a blemish,
  where the other way made mistakes.
- **Setup guide** for both services: `docs/maintenance.md`, "Setting up a
  translation service".

## Better English, and Azure that works (2026-11-19)

The privacy policy's English was weaker than DeepL is known for ("Right of
appeal" for *Beschwerderecht*, a verb lost in a long sentence, "his"), and
the maintainer would not fix it by rewording the German: "if I ever write
this text again myself or by others, this shouldn't fail the same way".

- **The whole German text as context** (the maintainer's idea): DeepL is
  told it with every request -- not translated, not counted; up to 50,000
  characters, for a longer text the part around. Before, a block was told
  only an English note, and a list item its lead-in. A block is still
  translated on its own (sent as one text, see above, was worse).
- **Kept by its own text**, no longer by its context: a change to one
  paragraph still costs that paragraph. Everything kept before is made anew
  once (the key changed) -- *Translate again* per version.
- **DeepL's better model and instructions**: `model_type`
  `quality_optimized`, and the house style (formal British English,
  gender-neutral: "they", "their") as `custom_instructions`. A plan that
  refuses them is asked without, from then on.
- **Azure's free tier** refuses more than about 33,000 characters a minute
  ("too many requests"): on the live server it refused the whole privacy
  policy, and DeepL stepped in unnoticed. Now portions of at most 10,000
  characters; in the background (publishing) the portal paces itself to
  30,000 a minute and waits when told to (`Retry-After`), up to four
  minutes in all; in the editor it goes to the next service at once. What
  a service managed is kept; only the rest goes to the next one.
- **Which service made it** is kept with a version's English
  (`translated_by`, `translation_note`) and shown: Admin › Legal texts
  ("English by DeepL, as Azure AI Translator failed", the reason in its
  dialog), the editor's *English* tab.
- **DeepL's characters:** the Free account made in November 2026 has
  1,000,000 characters for good, not per month (the maintainer; DeepL's own
  documentation still says 500,000 a month); the settings page no longer
  says "this month". At about 40,000 characters for the privacy policy and
  only changed paragraphs afterwards, they last years. The suggested order:
  DeepL first (better with context), Azure the fallback -- when DeepL's
  characters are used up, Azure (2 million a month) steps in by itself.

## Not built

- Keeping text typed in the last seconds before an editor lost its lock
  (taken over, laptop closed): at most the two seconds before the autosave;
  and the correction window, which has no autosave, "will likely never even
  be used at all" (the maintainer, 2026-11-05).

## A language model as a third service (decided 2026-11-19)

DeepL's Free account has 1,000,000 characters for good, not per month, so
waiting to see how DeepL with context does is "not really a long-term option
anyways" (the maintainer). A language model translates legal text better: it
reads the whole text, keeps to instructions (gender-neutral, British English,
the glossary in every inflected form, the HTML as it is), and costs a few
cents a text.

- **A third service, "Language model"**, beside DeepL and Azure, in the same
  order and fallback. Its **provider** is chosen: **Anthropic** (its own
  Python package), **OpenAI**, or **another server** with OpenAI's interface
  -- Ollama, llama.cpp, LM Studio, run locally for free ("If I really want
  to have this free, I could always just start a local [Ollama] server and
  connect this"). Settings: provider, key (none needed for a local server),
  address (another server only), and the **model** -- a field, never in the
  code (`CLAUDE.md`: no model names in code or documents).
- **What it is sent:** instructions, the glossary, the whole German text
  (cached at Anthropic between portions, so it is paid for once), and the
  paragraphs of a portion as a list; it answers a list in a fixed format
  (structured output). Each answer is checked: as many paragraphs as sent,
  the same HTML tags; otherwise that portion goes to the next service.
- **In the editor** a request has about 45 seconds: what is not done by then
  goes to the next service. **Publishing** (in the background) waits
  longer, and gives a paragraph a fallback made earlier -- in the editor, or
  while the first service failed -- another try with the first service; if
  it fails again, the earlier translation stays.
- **The order** becomes a list reordered by dragging, top first, instead of
  "Tried first" (`docs/design.md`, "An order").
- Not built: a model's own fallback at Anthropic (refusals): the next service
  in the order steps in instead.
