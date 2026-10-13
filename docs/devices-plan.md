# Devices: card readers and other things that talk to the portal

Agreed 2026-10-12. Step 1 built and merged (#36, `claude/devices`).

## What the maintainer wants

Card readers first: an ESP32 with an NFC module, its firmware written
separately in ESP-IDF. Later perhaps other devices: a beverage dispenser with
a touchscreen, tool cabinets, attendance on site (the flight team's members
earn ECTS and must be there for a number of hours). Uses for the readers are
collected in `docs/credit-plan.md`, "Card readers" and "Access control".

"I would mainly build a modular and reusable API for such card readers and
maybe even other devices that could connect someday to our system and make
this part ready then." Which uses come, and when, is decided later.

## Decided

- **Step 1 is device management only.** It includes the devices, their keys,
  the API they talk to, and the admin page. It does not include sales, doors,
  attendance or linking cards to people.
- **Each device has a type**, and its firmware is written for that type: a
  dispenser with a touchscreen is not a plain reader. The portal serves what
  a type needs (a dispenser's items and prices) and decides what a device
  reports (a tap, a sale). A type is a module in the portal's code
  (`services/device_types.py`), so a new use is one module plus its firmware.
- **Sign-in:** a key per device, sent over HTTPS as a bearer token. The key
  is shown once when the device is added or given a new key, and the portal
  keeps only its hash. Client certificates were ruled out for now: the portal
  is behind Cloudflare's tunnel, which ends TLS itself, so a device's
  certificate never reaches the server without Cloudflare's paid add-on. The
  device's sign-in method is recorded per device, so a stronger one can be
  added later. The device checks the server with ESP-IDF's certificate
  bundle; there is nothing to download for that.
- **A "Test" type** comes with step 1. The firmware can be pointed at it
  before any real use exists, and the admin page can send a request as if it
  came from the device.
- **Only admins** manage devices ("this is rather technical"): a permission
  of its own, `devices.manage`, which the admin and superadmin roles have.
- **The menu name is "Devices"**, under Admin › Settings.

## Owners (agreed 2026-10-13, not built yet)

"An admin provisions the device and then just selects which team or the
association it belongs to. Then in the management page of a team or the
association the admins or leaders can use their device however they like."

- **Admins** provision a device -- its key, its type (use), and its
  **owner**: the association or one team -- and keep what is technical: the
  key, the request log, removing it, linking cards at a reader.
- **The owner** -- a team's leads and team treasurer on the team's
  management page; for the association, admins and the treasurer on
  Admin › Credit -- gets a *Devices* section: each of their devices, online
  or not, the recent taps in plain words ("so that they can actually see
  that this is working"), and freely its name, place, item and on/off.
- **A reader sells only its owner's items**, so every sale counts for the
  owner (today an admin could set a team's reader to an item of another
  seller).

## Direction: hardware once, its use set in the portal (discussed 2026-10-13)

The maintainer would like to go "a bit at least in this direction" of
ESPHome: provision a device once, change what it does in a central
dashboard. Talked through:

- **The firmware knows the hardware, the portal decides the use.** One
  ESP-IDF project, what is fitted chosen at build time (Kconfig: reader
  chip, LEDs, buzzer, relay, buttons, display driver); the device reports
  what it has with its sign of life.
- **Simple devices** -- a reader with LEDs, a buzzer, a relay, a button or
  two -- can be driven by the portal: the device reports what happened (a
  tap, a button), the answer says what to do (LED green, two beeps, relay
  500 ms). The maintainer: "this definitely seems like a feasible and good
  idea".
- **Devices with a screen** keep their menus in the firmware: "development
  of the menu on screen is definitely a lot more involved than just telling
  the device to show a list". The portal gives them data (a price list) and
  results, not screens.
- Later, not decided: provisioning without copying the key (a new device
  reports itself, an admin accepts it), firmware updates over the air, a key
  bound to its chip.

## How it works

### In the portal

Admin › Settings › Devices lists every device: its name, type and place, and
whether it is on. It also shows when the device was last seen, its firmware
version and its address.

- **Add a device:** name, type, place, and the settings its type has. The
  key is shown once, with *Copy* and *Download configuration*: a small JSON
  file with the portal's address, the device's id and its key.
- **One device's page:**
  - its settings and the switch;
  - its key on one line, shortened (`jadev_<key id>_…AbCd`: the last four
    characters are kept for that), when it was made and last used, with
    *Rotate* (a new key works at once, the old one stops; the new one is
    shown in the same card) and *Revoke* (no key works until a new one is
    made; the device stays) -- asked for after testing, 2026-10-12;
  - its last 100 requests, each with its answer (`200`, `403 device_off`,
    ...), and its recent events: refused sign-ins, errors, what it reported.
    Both refresh every 5 seconds while the page is open, for watching a
    device while its firmware is written;
  - *What the device can send*: every request a device of its type can
    send -- the sign of life, each resource, each report -- one per row, with
    what it is for (the type describes its resources and reports), its
    `curl` command, and *Send*: sent from the page as the device, the answer
    shown under it exactly as the device would get it, without the device
    being marked as seen. A report that would really change something (a
    sale) has no *Send*. When the device itself would be refused (off, no
    key), the card says so. This replaced a "Try it" card whose buttons
    ("Sign of life", "Fetch …") did not say what they did, and a separate
    card with the example commands -- after testing, 2026-10-12;
  - *Remove* the device, apart from the key.

### The device API (`/api/v1/device`)

Every request carries `Authorization: Bearer <key>`, and may carry
`X-Device-Firmware: <version>`. No session, no cookies, no CSRF token.

- `GET /api/v1/device` is the sign of life. It answers the device's id,
  name, type, place, its settings for its type, and the server time.
- `GET /api/v1/device/resources/<name>` returns what the type offers, such as
  a dispenser's items.
- `POST /api/v1/device/events` takes `{event_id, kind, data}`, something
  the device reports. The type's module decides and answers. `event_id` is
  the device's own (a counter or random), so a request sent again after a
  network error gets the first answer and is not counted twice.
- **Refusals:** `401` for an unknown or revoked key, `403` for a device switched off,
  `404` for what its type does not offer, `429` above the limit (per device),
  `400` for a report carrying more than 4 KB of data (as UTF-8 JSON).
  A run of 429s is listed on the device's page once a minute.
  The body is the API's usual error, with a stable `code` for the firmware
  to act on.
- **The Test type:**
  - setting: a greeting;
  - resource: `hello`;
  - event: `ping`, answered with what it was sent.

### Kept, and for how long

Events are kept 90 days (the nightly clean-up). A device's address is the
device's, not a person's. Keys are stored only as SHA-256 hashes: they are
long random strings, so a slow hash adds nothing.

## Later

- **Agreed 2026-10-13** (`docs/cards-plan.md`): cards linked to accounts, and
  the first use, a "Sales point" type selling a fixed item; attendance around
  January.
- Not decided: doors and cabinets (with a list kept on the device for when
  the portal cannot be reached), what attendance records and who may see it
  (and the privacy policy for it).
- Team leads managing their team's own devices.
- A stronger sign-in, and cards that prove themselves cryptographically
  (NTAG 424 DNA, DESFire) as well as plain card ids.
