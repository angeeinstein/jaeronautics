# Devices: card readers and other things that talk to the portal

Agreed 2026-10-12. Step 1 is being built on `claude/devices`.

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
  - *New key* (the old one stops working at once) and *Delete*;
  - its recent events: refused sign-ins, errors, what it reported;
  - *Try it*: what the device would be answered, without it being marked as
    seen;
  - an example of the requests, for writing firmware.

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
- **Refusals:** `401` for an unknown key, `403` for a device switched off,
  `404` for what its type does not offer, `429` above the limit (per device).
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

## Later (not decided)

- Linking cards to accounts.
- The uses themselves: sales points, doors and cabinets (with a list kept on
  the device for when the portal cannot be reached), attendance (needs the
  privacy policy, and who may see it).
- Team leads managing their team's own devices.
- A stronger sign-in, and cards that prove themselves cryptographically
  (NTAG 424 DNA, DESFire) as well as plain card ids.
