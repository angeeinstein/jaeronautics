# Design Concept

The one look of joanneum Aeronautics' member portal -- and the reference for
anything else that should look like it belongs (emails, the forum theme,
event pages, a later React front end).

It started as a proposal made with Claude Design (colours and parts of it
were themselves taken from this portal and the Odoo website). The proposal
was a starting point, not a specification: where it did not fit the portal,
this concept differs, and every difference is listed with its reason under
[Where this differs from the proposal](#where-this-differs-from-the-proposal).

`aeronautics_members/static/style.css` implements it on top of Bootstrap 5.3.
Change the two together.

## Principles

- **Always dark.** One dark theme, never a light one, whatever the visitor's
  device is set to. `base.html` switches Bootstrap's dark theme on for good.
- **One accent, and it means "act here".** Cyan marks the main action, the
  link, the focused field, the current page. It is not decoration.
- **Flat.** Surfaces are told apart by a 1px hairline and a slightly lighter
  fill, never by a drop shadow. The only shadow is on things that float over
  the page (menus, dialogs).
- **Square.** No rounded corners -- buttons, fields, cards, badges, dialogs.
- **Quiet motion.** Short fades (about 0.15s). The one glow is the navbar
  hover.

## Colour

| Token | Value | Used for |
| --- | --- | --- |
| `--ja-bg` | `#161718` | Page background |
| `--ja-surface` | `#1f2123` | Cards, panels, dialogs |
| `--ja-surface-alt` | `#26292b` | Card header and footer bands, menus |
| `--ja-surface-input` | `#1b1b1c` | Form fields |
| `--ja-navbar-bg` | `#000000` | Navigation bar |
| `--ja-border` | `#34383b` | Hairlines around surfaces, table rules |
| `--ja-border-strong` | `#3a3d40` | Form field borders |
| `--ja-text` | `#ffffff` | Text |
| `--ja-muted` | `#aab4bb` | Secondary text, help text under fields |
| `--ja-placeholder` | `#8b8b8c` | Placeholders, empty tick-box outlines |
| `--ja-accent` | `#00dfff` | Primary buttons, current page, focus |
| `--ja-accent-hover` | `#33e7ff` | Hover on the above |
| `--ja-accent-active` | `#00c2df` | Pressed |
| `--ja-accent-text` | `#081114` | Text on the accent |
| `--ja-link` / hover | `#66ecff` / `#33e7ff` | Text links |
| `--ja-success` | `#28a745` | Success messages, "active" states |
| `--ja-warning` | `#f2b84b` | Warnings, things waiting |
| `--ja-danger-strong` | `#dc3545` | Destructive buttons |
| `--ja-danger` | `#ff6b6b` | Error text and tints on the dark page |

Tints for messages are the tone colour at 12-14% over the surface, with a
border in the same colour at about half strength. Text on a tint stays white.

## Type

| Face | Used for |
| --- | --- |
| **Inter** | All text: body, labels, buttons, tables |
| **Archivo** | Headings |
| **IBM Plex Mono** | Figures (the admin dashboard counts), code, logs |

- Headings: Archivo; `h1`/`h2` bold (700), `h3`-`h6` semibold (600). Sizes
  are Bootstrap's, which get smaller on a phone.
- A card's title (in its header band): 1.125rem, semibold.
- Body: 1rem, line height 1.5. Small print: 0.875rem.
- The opening sentence of a page (thank-you, cancelled, error pages):
  1.125rem, light (300), line height 1.55.
- Small uppercase labels ("TOTAL ACCOUNTS") get letter spacing of 0.08em.
- Field labels: 0.875rem, medium (500).
- Buttons: semibold (600).

The fonts are served from the portal itself (`static/fonts/`, SIL Open Font
License), Latin and Latin Extended, so names such as Popović render in the
right face.

## Sizing

- Fields are 3rem tall. Buttons are at least 2.75rem (44px, the usual
  minimum for a comfortable tap); small ones 2.25rem, large ones 3.25rem.
  Button text is 1rem, small 0.875rem, large 1.25rem, with side padding of
  1 / 0.75 / 1.5rem.
- Spacing follows Bootstrap's scale (0.25, 0.5, 1, 1.5, 3rem), which matches
  the proposal's steps where they overlap.
- Content is at most 1320px wide (Bootstrap's container); the admin
  workspace at most 1280px.

## Components

**Buttons** -- say what kind of action they are:

| Kind | Look | For |
| --- | --- | --- |
| Primary | Cyan, dark text | The main action of a section |
| Secondary | Grey, white text | Everything else: back, reset, cancel, open |
| Danger | Red, white text | Deleting, deactivating, rejecting |
| Outline | Cyan border, cyan text | A lighter alternative next to a primary |
| Link | Text only | Inside navigation |

One primary per section. In the admin section bar the current section is the
primary and the others are secondary.

**Fields** -- dark fill, strong border; on focus the border turns light cyan
with a 2px soft cyan ring. Help text under a field is muted. A dropdown looks
like a field with a white chevron.

**Tick boxes and radio buttons** -- a visible grey outline when empty; cyan
with a dark tick or dot when chosen.

**Cards** -- surface fill, hairline border, optional header and footer bands
in `--ja-surface-alt`. No shadow.

**Messages (alerts)** -- a tint of their tone with a border in the same tone,
no icon: info (cyan), success (green), warning (amber), danger (red),
secondary (grey).

**Status labels** -- a state in one word: small capitals (0.75rem,
semibold) on a faint tint of the state's colour, text in that colour,
square. Five tones, chosen by what the state means:

| Tone | Look | For |
| --- | --- | --- |
| `status-active` | Green | Confirmed, active, healthy, completed, claimed |
| `status-pending` | Amber | Waiting on someone: not confirmed, not active, rolled back |
| `status-failed` | Red | Something is wrong: deactivated, failed, needs attention |
| `status-neutral` | Grey | A plain fact: a role, erased, unclaimed, no sign-in yet |
| `status-info` | Cyan | Worth noticing, not a problem: old forum, reconnected, running |

`<span class="status-label status-active">Active</span>` -- the words stay
in normal case in the template; the style capitalises them.

**"It worked" tick** -- a bare green tick with no frame that draws itself in
once (0.45s) at the start of a success message: after something the person
just did worked (saved, submitted, confirmed). Messages that describe a
steady state ("nothing needs attention") do not get it. With reduced motion
switched on in the system, the tick is simply there.

**Tables** -- transparent on the card, hairline rules, cyan semibold column
headings, faint zebra stripes where rows are long.

**Navigation bar** -- black, 3px cyan line along the bottom, the negative
logo lockup. Links are white. The section you are in is cyan with a thick
cyan line under it; a link under the pointer stays white and gets a thin
white line with a soft glow. The two never look alike.

**Menus and dialogs** -- surface fill, hairline border, header and footer
bands, and the one real shadow (`0 12px 32px rgba(0,0,0,0.45)`).

## Logo and icon

- The full lockup (the "A" with the airplane, then "joanneum AERONAUTICS")
  is the logo: `static/logo_joanneum_aeronautics_negativ.svg`, white and
  cyan for dark backgrounds, 52px tall in the navbar.
- Where there is only room for a square -- browser tab, phone home screen,
  an avatar -- the "A" with the airplane alone, on black:
  `static/favicon.svg` (cut from the lockup's own shapes, so it is the same
  drawing), with `favicon-32.png` and `apple-touch-icon.png` (180px) for
  browsers that want a picture. The same files can be uploaded as the
  forum's favicon and touch icon.

## Wording and formats

- English only.
- Dates `31.12.2026`, times `31.12.2026 14:05`, 24-hour, always in Vienna
  time (Europe/Vienna, with summer time) -- on pages and in emails. Stored
  times are UTC.
- Links in running text may be underlined when they would otherwise be lost
  (the terms link in the signup form); elsewhere links carry colour only.

## Where this differs from the proposal

| Proposal | Here | Why |
| --- | --- | --- |
| React components | Bootstrap 5.3 with its variables set to these colours | The portal is server-rendered Flask; a React front end is planned for later and can take this concept over as it is |
| Fonts from Google Fonts | Served from the portal | No visitor's address goes to Google (a German court awarded a visitor damages over exactly this in 2022), and the site's security policy allows no outside fonts |
| Button heights 2.25 / 3 / 3.5rem, text 0.875 / 1 / 1.125rem, wide side padding | Minimum heights 2.25 / 2.75 / 3.25rem, text 0.875 / 1 / 1.25rem, side padding in proportion to the text | Tried as proposed: the label looked small in a box with a lot of empty space around it, and large buttons (login, payment) had smaller text than before. 2.75rem keeps normal buttons easy to tap on a phone. Minimums, not fixed heights, so a label that wraps still fits |
| Primary button hover: fade to 88% | Lighter cyan (`--ja-accent-hover`) | The proposal defines that colour itself; fading towards the dark page reads like "disabled" |
| Fixed heading sizes (h1 2.25rem ...) | Bootstrap's responsive sizes, the proposal's faces and weights | Fixed sizes are too large on a phone; the look comes from the face and weight |
| Tick box outline `--ja-border-strong` | `--ja-placeholder` (lighter grey) | The proposal's outline is about 1.5:1 against a card, and empty boxes were easy to miss; accessibility guidance (WCAG) asks for 3:1, the lighter grey gives 4.7:1 |
| Message border in full tone colour | Tone colour at about half strength | Many messages here are quiet notices ("no log entries yet"); full-strength borders made them shout louder than the buttons |
| Mono face for dates and year groups too | Only for figures, code and logs so far | Dates and codes sit inside running text and tables; setting them apart needs markup changes, left for later |
| Navbar logo 32px tall | 52px | The lockup's small "joanneum" line is unreadable at 32px |
| Four status tones: active, pending, ended, failed | Five: ended is `status-neutral` and also covers roles; an extra `status-info` (cyan) | Roles and "old forum" are neither good nor bad; showing them green or grey-as-ended would say something they don't |
| Failed status text in the strong red (`#dc3545`) | The softer red (`#ff6b6b`) | The strong red on its own dark tint is hard to read (about 3:1); the softer red is the proposal's own error red for the dark page |
| Tick in cyan | Tick in green | It sits inside a green success message, where a cyan tick clashed; green says "worked" on its own |
| Page-change progress bar (thin cyan bar at the top) | Not used | Every click here loads a whole new page, which the browser already shows; the bar pays off with a front end that swaps content without reloading (React, later) |
| Loading placeholders (skeletons) | Not used | Pages arrive from the server complete, so there is never a moment to show them; they would only fake a delay. Useful once parts of a page load on their own (React, later) |

## Not built yet

- The mono face for dates and codes (year groups, invoice numbers):
  deferred, see the table above.
- The page layouts of the proposal's portal kit (login, signup, account,
  admin dashboard). Current pages keep their structure.

## Checking a change

`scripts/visual/snapshot.py` screenshots every page at six sizes, flags
overlaps and sideways scrolling, and confirms that the device's light/dark
setting changes nothing:

```bash
python scripts/visual/snapshot.py shoot /tmp/after
python scripts/visual/snapshot.py compare /tmp/before /tmp/after
```
