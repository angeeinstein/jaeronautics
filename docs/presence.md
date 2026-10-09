# Presence: is anybody using the portal right now?

**Status:** built 2026-10-09. Code: `services/presence.py`, `api/presence.py`,
`frontend/src/lib/presence.ts`, `frontend/src/pages/admin/settings/Presence.tsx`.

## What the maintainer asked for

> "in the admin settings somewhere to see if someone is active anywhere on our
> page … when I am going to do an update of the server and I see that someone
> is actively clicking around something and doing something and entering
> data. I might wait here for a bit."

Then: "Let's start with just a very simple presence detection like with
forms, clicks etc. But keep it open if we later want more detailed
analytics."

## How it works

- **A tab says it is in use.** While it is in front and somebody clicked,
  typed or scrolled in it in the last minute, it posts to
  `/api/v1/presence` about every half minute, and at once on a new page.
  - A tab left open, refreshing itself, says nothing.
  - What it sends: a random id for the tab (kept in the tab's session
    storage), the page as the app's address pattern, and whether somebody
    typed since.
  - The page is sent as a pattern such as `/teams/:slug` or
    `/reset-password/:token`, never with what is in the address; the server
    keeps only patterns.
- **The server keeps one row per tab** (`presence` table) and nothing about
  who:
  - signed in or not;
  - the page;
  - first seen, last seen, last typed.

  A row goes 15 minutes after its tab went quiet. No user id, no IP address.
  At most 2,000 tabs are taken in, and the endpoint is rate-limited
  (`RATELIMIT_PRESENCE`).
- **Settings › Updates, beside "Install update now", shows:**
  - "Nobody else is using the portal right now (last seen 4 minutes ago)";
  - or how many people are there (signed in / visitors) and on which pages;
  - and a warning while somebody typed in the last three minutes.

  The admin's own tab is left out. It is asked again every 15 seconds.
- **"Active"** means the tab spoke in the last two minutes.

## Kept open: more detailed analytics later

Not built, and no decision yet. The same signal could carry more:

- How long people stay on a page.
- Which pages are used at all.
- Counts per day, kept as totals rather than rows.

Before doing so:

- Decide whether anything should identify a person (it does not now), and
  say so in the privacy policy if it does.
- Keep totals rather than rows wherever a total answers the question.
