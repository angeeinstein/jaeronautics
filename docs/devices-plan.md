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

## Owners (agreed and built 2026-10-13)

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
  owner (before, an admin could set a team's reader to an item of another
  seller). Given to another owner, a reader's item is cleared.
- **A team permission of its own**, `team.devices`, held by leads and the
  team treasurer (and so by admins and the association's treasurer); the
  team's side menu shows *Devices* once the team owns one.
- **Each device has a code,** random and made when it is added (`K7MQ-2X9A`,
  no 0/O/1/I/L): "a unique ID of just random characters, so that this does
  not seem like a name given by a person". A name is optional; the code
  stands in for it. The maintainer suggested the ESP32's MAC address as the
  ID; it was kept beside it instead, because the device is added in the
  portal before it has ever spoken, and a replaced board would otherwise be
  another device. The board sends its MAC address with every request
  (`X-Device-Hardware`); the device's page shows it, and an event notes it
  when the key turns up on another board.
- **A firmware starter** comes with every key (*Download starter*, beside
  *Download configuration*): one Markdown file with the server, the code and
  the key, every request the device's type makes and what it is answered,
  the rules (the headers, a new `event_id` per event kept for retries,
  checking the certificate), and an example sketch for an ESP32 with the
  Arduino framework -- "I could just hand this firmware example package to a
  coding agent and ask it to build a firmware around this"
  (`services/device_starter.py`; each type adds its notes,
  `DeviceType.firmware_notes`). The sketch is a starting point, not compiled
  here.
- **The firmware's behaviour stays in the firmware for now**; the portal
  driving simple devices (below) comes later.

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

## One device for everything (agreed 2026-10-14)

The maintainer built a reader with a touchscreen and an NFC reader below it,
in a 3D-printed enclosure, and wants it for every use: "why not use this
device for everything?" No devices with only a button or an LED.

- **One device, one firmware.** Every use (sales point, attendance, ...) is
  built into the firmware with its own screens: "this makes it a lot easier
  to actually create nice looking menus ... and a nice UI on the device
  itself". It costs more per device; the maintainer thinks it is worth it.
  This replaces "simple devices driven by the portal" from the Direction
  below; the screen devices' part of it (the portal gives data and results,
  not screens) holds.
- **The device says what it can do, the portal what it is for.** With its
  sign of life the device reports its board, its firmware version and the
  uses its firmware supports. The portal assigns one of them, with its
  settings, and offers only uses the device's firmware has. Changing the use
  needs no new key and nothing on the device. A new use is a firmware release
  plus its module in the portal (`services/device_types.py`), which still
  decides what a tap does.
- **Changes reach the device at once** (long polling): the device asks for
  what is new and the server holds the request up to about a minute,
  answering the moment something changes. Works through the Cloudflare
  tunnel.
- **Wi-Fi is the firmware's**, including WPA2-Enterprise (it has joined the
  university's network); nothing for the portal to do.

### Pairing (agreed 2026-10-14)

The device gets its key without anyone copying it (the scheme TVs use, "go
to this address and enter the code"); proposed by the maintainer, worked out
with the firmware:

1. The unpaired device asks `POST /api/v1/device/pair/start` with its MAC
   address and firmware version. It gets a short code for people (`K7Q-4X9`,
   safe on the screen) and a long random pairing id that only the device and
   the server know.
2. The screen shows the code and a QR code linking to the portal's approval
   page with the code filled in; whoever is not signed in signs in first and
   lands back there.
3. **An admin approves** -- admins only for now ("we could change this later
   if necessary"; a team's sales point could charge any member's credit, and
   card ids are not secret). The page shows the board (MAC), firmware and
   when it asked, and asks what the device is: owner, use, name (optional);
   or *Replaces device ...*, which moves that device to the new board and
   stops the old board's key.
4. The device polls `POST /api/v1/device/pair/poll` with its pairing id at
   the interval the server sets. After approval it gets its key once, with
   its code, owner and use; then the pairing is gone. Nobody ever sees the
   key; *Download configuration* and the starter stay for development.

Guards: a code works 10 minutes; few pairing requests per address.

### Settings on the device (agreed 2026-10-14)

- **A card opens them:** tapping a card on the device asks the portal whether
  its holder may manage the device -- admins (`devices.manage`) for now.
  "People that are admins in the membership portal are also admins for the
  devices. And if I change roles in the portal, this also immediately changes
  on the device."
- **A backup code** for when the server cannot be reached or the Wi-Fi is
  lost: one per device, shown to admins on the device's page and changeable
  there; the device keeps it (as a hash) from its last contact.

### Firmware updates (agreed 2026-10-14)

The firmware can already update itself over the air.

- **Admin › Settings › Devices › Firmware:** upload a firmware file. The
  portal reads the version, project and build date from the file itself and
  refuses a file that is not this firmware ("the server only accepts this
  when I upload a firmware"); it keeps its SHA-256.
- **One file for every device**, whatever it is used for.
- **Which version the devices run** is chosen there -- first one device,
  then all. The device learns of it with its next contact (at once, see long
  polling), downloads it signed in with its key, checks the hash, installs
  it when it is not in use, and reports the result. With ESP-IDF's rollback
  on, a new version counts only once it has reached the server again;
  otherwise the device goes back to the old one.
- **The device installs only this firmware**: it checks the same marks the
  server checks (below).

### Open (2026-10-14)

- **The marks of "our" firmware:** ESP-IDF's app description already holds
  the project name and version; with a few bytes of our own (a marker and
  the board) both the server and the device can tell this firmware from any
  other. That guards against a wrong file, not against someone who takes over
  an admin account or the server: anyone can copy the marker.
- **Signing the firmware:** the maintainer is not sure yet. Signed
  images (ESP-IDF's signature check on updates, the key kept on the
  maintainer's computer) would guard against that too. It can be switched
  on later with an ordinary update; the portal only stores and passes on the
  file either way.
- Which part first: pairing and the device reporting its uses, or firmware
  updates.

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
- Pairing by team leads for their own team (2026-10-14: admins only for now).
- A stronger sign-in, and cards that prove themselves cryptographically
  (NTAG 424 DNA, DESFire) as well as plain card ids.
