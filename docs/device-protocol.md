# Device protocol

How a device talks to the members' portal: everything its firmware needs to
know, in one place. Written for whoever writes the firmware (a person or a
coding agent). The decisions behind it are in `docs/devices-plan.md`. Admins
download this document, with their portal's address, on Admin › Settings ›
Devices › Firmware.

Each section says where it stands:

- **Built** -- the portal answers exactly this today.
- **Planned** -- agreed, and written here so the firmware can be built
  against it; the portal does not answer it yet.
- **Later** -- the direction; the details are settled when it is built.

**This protocol is not frozen.** When something can be done better, it is
changed, and the firmware is updated with it: "a small firmware update to all
devices that are older is not that much of a problem". Every change is listed
under *Changes* at the end. Firmware should still ignore fields it does not
know, so that adding a field never breaks it.

---

## 1. Basics (built)

### Where

All requests go to the device API: `https://<portal>/api/v1/device`, for
example `https://members.example.org/api/v1/device`. The firmware is
configured with the portal's address.

- **HTTPS only, with the server's certificate checked.** On ESP-IDF, use the
  certificate bundle (`esp_crt_bundle_attach`). Never turn the check off: the
  key travels with every request.
- **JSON** both ways, UTF-8. Send `Content-Type: application/json` with a body.
- **Timeouts:** give a request about 5 seconds. A device that cannot reach the
  portal says so on its screen and does not act. Nothing is queued on the
  device to be sent later: a sale needs the portal.

### Headers on every request

| Header | Example | What for |
|---|---|---|
| `Authorization` | `Bearer jadev_3f9a1c2b7d10_…` | The device's key. Every request except pairing. |
| `X-Device-Firmware` | `1.2.0` | The running firmware's version, exactly as its app description has it (`esp_app_get_description()->version`). The portal compares it with the version the device should run. |
| `X-Device-Hardware` | `98:A3:16:E5:06:3C` | The board's MAC address. Shown in the portal. Noted as an event when the key turns up on another board. |
| `X-Device-Board` | `jae-terminal-s3` | What the firmware calls the board. |
| `X-Device-Types` | `sales_point, attendance` | Every use this firmware has screens for, comma-separated, in the portal's names (section 8). The portal only lets an admin choose one of these for the device. |
| `X-Device-Outputs` | `4` | How many outputs it can switch (relays, a lock), 0 to 16; leave it out when it has none. Its owner can then have a sale switch them (section 8.1, "Actions"). |

### Answers

- `200` (or `201` for pairing): the answer, JSON.
- `304`: the sign of life when nothing changed (section 4). No body.
- Anything else is a refusal, with this body:

```json
{"error": {"code": "device_off", "message": "This device is switched off.", "fields": null, "details": null}}
```

`code` is stable: act on it. `message` is in words, for a screen or a log.

| Status | `code` | What the firmware does |
|---|---|---|
| 400 | `validation_error`, `device_report_too_large`, ... | The firmware sent something wrong: log it. `fields` says which field. |
| 401 | `device_key_missing`, `device_key_unknown`, `device_key_revoked` | The key does not work: the device was removed or got another key. Stop, show it, offer to pair again (section 3). |
| 403 | `device_off` | Switched off in the portal. Show it, try again in a minute. |
| 404 | `device_resource_unknown`, `device_report_unknown`, `firmware_none`, `pairing_unknown`, `pairing_expired` | See the section of the request. |
| 429 | `rate_limited`, `pairing_too_fast`, `pairing_busy` | Too often: slow down. At most 120 requests a minute per device (and 600 a minute per network address). |
| 5xx | | The portal's trouble: try again a little later. Repeat a report with the **same** `event_id` (section 7). |

### Times and money

- **Money** is in cents, as integers: `"price_cents": 150` is €1.50.
- **Times** are ISO 8601 with their offset. `server_time` is UTC
  (`"2026-10-14T14:02:11Z"`). Times meant for the screen (attendance) come in
  the association's local time (`"2026-10-14T16:02:11+02:00"`), so the device
  shows the hour and minute as they are and needs no time-zone rules.

---

## 2. The life of a device

```
 power on
    │
    ├─ no key stored ──► PAIRING (section 3) ──► key stored
    │                                               │
    ▼                                               ▼
 RUNNING: sign of life every 2 s (section 4); taps, menus, reports (sections 7, 8)
    │
    ├─ firmware offered ──► UPDATE (section 6) ──► restart into the new version
    ├─ 401 on any request ──► key gone: show it, offer to pair again
    └─ portal out of reach ──► show it; settings only with the backup code (section 5)
```

---

## 3. Pairing (built)

A device without a key asks to be paired. An admin approves it in the portal,
and the device fetches its key itself. Nobody ever sees or copies the key.
These two requests carry **no** `Authorization` header, and the other headers
as always.

### 3.1 Ask to be paired

`POST /api/v1/device/pair/start` (no body) answers `201`:

```json
{
  "code": "K7Q-4X9",
  "pairing_id": "Yq3m…43 characters…",
  "url": "https://members.example.org/admin/settings/devices/pair?code=K7Q-4X9",
  "expires_in": 600,
  "poll_every": 5
}
```

- Show `code` large, and `url` as a QR code. Scanned with a phone signed in as
  an admin, it opens the approval page with the code filled in.
- Keep `pairing_id` in memory and never show it.
- The code works for `expires_in` seconds (10 minutes). After that, start again.
- Refused: `429 pairing_busy` (too many devices waiting: try again in a few
  minutes) or `429 rate_limited` (at most 20 a hour per network).

### 3.2 Ask whether it was approved

Every `poll_every` seconds, send `POST /api/v1/device/pair/poll` with this body:

```json
{"pairing_id": "Yq3m…"}
```

The answer is `200` and one of:

| Answer | Meaning |
|---|---|
| `{"status": "waiting", "poll_every": 5, "expires_in": 412}` | Keep asking. |
| `{"status": "paired", "key": "jadev_…", "device": {…}}` | Approved. Store `key` (NVS, encrypted where possible) and go on as the device: `device` is what the sign of life answers (section 4). Answered **once**. |
| `{"status": "refused"}` | An admin turned it away. Show it, and start again only when asked on the device. |

Refused:
- `404 pairing_unknown` or `404 pairing_expired`: the code ran out, or the
  answer was already given. Start again.
- `429 pairing_too_fast`: wait `poll_every`.

If the device missed the `paired` answer (the network), it starts again. The
admin then pairs it as replacing the device it became, and it keeps that
device's code, settings and history.

---

## 4. The sign of life (built)

`GET /api/v1/device`. Send it at start, then every `poll_every` seconds (2),
always with `If-None-Match` set to the `ETag` of the last `200`.

- **`304`, no body:** nothing changed. Keep what you have.
- **`200`:** something changed. Take the whole answer, and its new `ETag`:

```json
{
  "device_id": 3,
  "code": "K7MQ-2X9A",
  "name": "Workshop fridge",
  "type": "sales_point",
  "owner": "Rocket Team",
  "place": "Workshop, beside the fridge",
  "settings": {"items": [4, 7, 9, 12], "actions": {"on": true, "lines": [{"item": null, "output": 1, "seconds": 5}]}},
  "resources": ["menu"],
  "reports": ["card", "buy"],
  "backup_code_hash": "9f86d081884c7d65…",
  "firmware": null,
  "poll_every": 2,
  "server_time": "2026-10-14T14:02:11Z"
}
```

| Field | Meaning |
|---|---|
| `name` | Its name, else its code: show it where the device names itself. |
| `type` | What it is used as now. **It can change**: then switch to that use's screens. |
| `settings` | The settings of its use (section 8). Read them from here; never keep your own copy as the truth. A sales point's `actions` are the portal's: what to switch comes with each sale (section 8.1). |
| `resources`, `reports` | What this use may fetch and report. |
| `backup_code_hash` | Section 5. |
| `firmware` | `null`, or the firmware to install (section 6). |
| `poll_every` | Seconds until the next sign of life. Take it from every answer; never hard-code it. |
| `server_time` | The portal's clock, UTC. Set the device's clock from it. |

A `304` costs the portal next to nothing, which is why the device asks every
2 seconds (10 until 2026-10-16). Whatever is changed in the portal reaches the
device within 2 seconds. Sign-of-life requests answered `304` are not listed
on the device's page in the portal, nor in the server's access log; every
other request is.

**Keep the connection open** between requests (ESP-IDF's HTTP client does so
when the same client is reused): a new HTTPS connection every 2 seconds would
cost the device a TLS handshake each time. The server keeps an idle one for
75 seconds.

*Planned (later): `config` and `commands` in this answer (section 9).*

---

## 5. Settings on the device (built)

The device's own menu (Wi-Fi, pairing again, information) opens two ways.

### 5.1 With an admin's card

Tap a card while the device asks for it, and send
`POST /api/v1/device/manager` with:

```json
{"card": "04A1B2C3D4E5F6"}
```

The answer is `{"allowed": true, "name": "Alex"}` or
`{"allowed": false, "name": null}`.

It is asked every time, so a role changed in the portal counts at once. It is
allowed for people who manage devices in the portal (admins).

### 5.2 With the backup code, when the portal cannot be reached

Each device has a six-digit backup code, shown to admins on the device's page
in the portal. The device only has its hash, from the sign of life:
`backup_code_hash` is the SHA-256, as lowercase hex, of `"<code>:<digits>"`.
For a device `K7MQ-2X9A` and the code `123456`, that is the SHA-256 of
`"K7MQ-2X9A:123456"`.

Keep the last hash received (NVS), and compare the hash of what is typed. When
an admin makes a new code, the new hash arrives with the next sign of life.

---

## 6. Firmware updates (built)

One firmware for every device, whatever it is used for. An admin uploads it in
the portal and chooses which version the devices run: for all of them, or for
one device first.

### 6.1 When

The sign of life says when to update:

```json
"firmware": {"version": "1.3.0", "size": 1468006, "sha256": "…64 hex…"}
```

It is `null` when there is nothing to do. The same can be asked on its own
with `GET /api/v1/device/firmware`:

```json
{
  "running": "1.2.0",
  "wanted": {"version": "1.3.0", "size": 1468006, "sha256": "…", "built": "Oct 14 2026 09:12:44",
             "url": "https://members.example.org/api/v1/device/firmware/file"},
  "install": true
}
```

### 6.2 How

1. Wait until the device is not in use.
2. Say so: `POST /api/v1/device/firmware` with
   `{"version": "1.3.0", "result": "started"}`.
3. Download `GET /api/v1/device/firmware/file`, with the usual headers,
   straight into the next OTA partition (`esp_https_ota`, or
   `esp_ota_begin`/`write`/`end`). A download cut off is resumed with
   `Range: bytes=<from>-` (answered `206`). The answer carries
   `X-Firmware-Version` and `X-Firmware-SHA256`.
4. Check the SHA-256 of what was written against `sha256`, then switch to it
   and restart.
5. With ESP-IDF's app rollback on (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`),
   mark the new app valid (`esp_ota_mark_app_valid_cancel_rollback`) only
   once it has reached the portal: a sign of life answered `200` or `304`.
   Its `X-Device-Firmware` then tells the portal that the update worked.

When it fails, say why:

```json
{"version": "1.3.0", "result": "failed", "detail": "SHA-256 does not match"}
```

That version is then not offered to this device again until an admin clicks
*Try again*, so a broken file is not tried over and over.

### 6.3 What the portal accepts

Only a whole ESP-IDF app image: checksum and appended SHA-256 intact, with an
app description, and of the same project and for the same chip as the newest
file uploaded before (the project name in the app description, the chip in
the image header). Version, project, build date and chip are read from the
file itself, and a version cannot be uploaded twice.

Nothing of the firmware's own is needed in the file: what ESP-IDF puts in
every build is what is checked (decided 2026-10-15).

### 6.4 A fresh board: the factory image (built 2026-10-16)

A board without firmware is flashed from the portal, over USB in the
browser (Admin › Settings › Devices › Firmware, *New device*). It needs one
file with everything a fresh board needs, written from address `0x0`:

```sh
idf.py merge-bin -o factory.bin            # ESP-IDF 5.3 and later, after idf.py build
# or, from the build directory:
esptool.py --chip esp32s3 merge_bin -o factory.bin @flash_args
```

The portal reads the bootloader's chip, finds the app through the partition
table (the `factory` partition, else `ota_0`) and accepts the file only if
the app is whole, for the same chip as the bootloader and of the same
project as the update files. It need not be the newest version: after
flashing, the board asks for its Wi-Fi on its screen, is paired (section 3)
and updates itself (section 6). The page erases the whole flash first unless
told not to, so a used board flashed again forgets its key and pairs anew.

An update over the air never changes the partition table: a new layout
needs the boards flashed over USB again.

### 6.5 Crash reports (built 2026-10-16)

On a crash -- an exception, an assert, a watchdog -- ESP-IDF keeps a core
dump (an ELF; only the last) and the board restarts. After the first
successful sign of life, while idle, the device sends what ESP-IDF read from
it, then the dump when asked. Admins see it on the device's page,
*Crashes*, and decode the dump on a computer with the firmware's program
file (`espcoredump.py info_corefile -c <dump> <program file>`).

**1. The summary:** `POST /api/v1/device/crash`

```json
{
  "crash_id": "9b1e…64 hex…",
  "build": "72ce52965e229fed",
  "task": "portal",
  "pc": "0x4209F87C",
  "backtrace": ["0x4209F879", "0x4209FB05", "0x4209D2EA", "0x42008809"],
  "backtrace_corrupted": false,
  "reset_reason": "panic",
  "size": 41260
}
```

| Field | |
| --- | --- |
| `crash_id` | The dump's SHA-256, 64 hex. The same crash sent again is answered as the first time. |
| `build` | The first 16 hex of the crashed program file's SHA-256 (`esp_app_desc_t.app_elf_sha256`): the portal names the version by it. |
| `task` | The task that crashed, at most 32 characters. |
| `pc`, `backtrace` | Addresses as `0x` and up to 8 hex; at most 16 in the call path. |
| `reset_reason` | `panic`, `task_watchdog` or `interrupt_watchdog`. |
| `size` | The dump's size in bytes, at most 131072. |

The running version is the `X-Device-Firmware` header, as always. Answers:
`201 {"upload": true}` -- send the dump now; `200 {"upload": false}` -- the
portal has it already: delete it. `400` with `fields`: a field is wrong.

**2. The dump:** `PUT /api/v1/device/crash/<crash_id>/file`, the raw ELF as
the body (`Content-Type: application/octet-stream`), at most 128 KB. Answer
`204`: delete it from the flash. Refused: `404 crash_unknown` (no summary
first), `400 crash_file_mismatch` (its SHA-256 is not `crash_id`), `400
crash_file_not_elf`, `400 crash_file_too_large`. On a network error, try
again after the next restart or an hour later; until it is answered, keep
the dump, also across restarts.

The portal keeps a report 90 days and at most 20 dumps a device: a new one
lets the oldest dump go, its summary staying. So it always asks for a new
crash's dump.

---

## 7. Reports and resources (built)

### 7.1 Reports

What the device reports -- a card tapped, an item chosen -- goes to `POST /api/v1/device/events`:

```json
{"event_id": "8f2c4e0b9a1d4c7e8b3f6a2d1e0c9b7a", "kind": "card", "data": {"card": "04A1B2C3D4E5F6"}}
```

It is answered:

```json
{"event_id": "8f2c4e0b9a1d4c7e8b3f6a2d1e0c9b7a", "answer": {…}}
```

`kind` is one of the use's reports, and `answer` is the use's (section 8).

**`event_id`:**
- Make a new one for every event: 16 random bytes as hex (`esp_random()`).
  At most 64 characters of `A-Z a-z 0-9 . _ : -`.
- Send it **again, unchanged**, when the report has to be repeated (a
  timeout, no answer, a 5xx). The portal answers a repeated report as the
  first time and does it only once: a sale is never counted twice.
- A counter is not enough: it starts again after a restart.
- Reports are remembered for 90 days.

`data` is at most 4 KB (as UTF-8 JSON). A report of a kind the use does not
have is answered `404 device_report_unknown`.

### 7.2 Resources

What a use offers to read -- the menu of a sales point -- is at
`GET /api/v1/device/resources/<name>`, answered `{"resource": "<name>", "data": {…}}`.
A name the use does not offer is answered `404 device_resource_unknown`.

### 7.3 Card IDs

A card's ID is its UID as the reader reads it, in hex:
`"04A1B2C3D4E5F6"`. Case and separators do not matter (`04:a1:…` is the
same). UIDs of 4, 7 or 10 bytes are accepted (8, 14 or 20 hex characters).
Report every card. Whether it is known, linked or allowed is the portal's to
decide.

### 7.4 Linking an unknown card at the reader (built)

A card nobody has linked can be linked by its owner right at the reader, with
any phone: the reader shows a QR code, the phone opens it and links the card
to the account signed in there. The reader is busy with it only briefly.

**1. The refusal carries a link.** A `card` report of an unknown card is
refused with `card_unknown` and, on a reader that can show it, a `link`:

```json
{
  "result": "refused",
  "reason": "card_unknown",
  "message": "This card is not linked to an account.",
  "link": {
    "id": 17,
    "url": "https://members.example.org/link/Xk3vQ9…",
    "show_for": 30,
    "check_every": 2
  }
}
```

- Show `url` as a QR code (ESP-IDF's `qrcode` component), with a line such
  as "Not linked yet: scan to link it". The URL is about 60 characters.
- The QR code never holds the card's ID; nothing about the card is in it.
- Show it for at most `show_for` seconds. Without `link`, show the message
  as for any refusal.
- The same answer comes again for the same `event_id`, as with every report;
  a new tap (a new `event_id`) gets a new link.

**2. Ask about it while it is shown**, every `check_every` seconds:
`GET /api/v1/device/card-links/<id>`, answered:

| `state` | What the reader does |
|---|---|
| `waiting` | Nobody has opened the link yet: keep the QR code. |
| `opened` | A phone has opened it: take the QR code away **at once**, show "Continue on your phone" for about 3 seconds, then the normal screen. Stop asking. |
| `linked` | Done already (with `"first_name": "Alex"`, may be `null`): "Linked, hi Alex", then the normal screen. Stop asking. |
| `expired` | Over (another tap of the same card started a new one, or its time ran out): the normal screen. Stop asking. |

After `show_for` seconds without `opened` or `linked`, go back to the normal
screen and stop asking. These requests are not listed on the device's page.
A link of another device is answered `404 card_link_unknown`.

**3. The rest happens between the phone and the portal.** The portal kept
the card at the tap; the reader takes no part any more. The link works for
10 minutes, once, and only on the first phone that opened it. Whoever lost
it simply taps the card again: a new QR code, and the earlier link stops
working. The next tap after linking is an ordinary one (the menu, at a sales
point).

While the QR code is shown, a card tapped is reported as usual; it replaces
the QR code with whatever its answer says.

---

## 8. Uses

The portal's name for each use is what `X-Device-Types` lists and `type` says.

### 8.1 Sales point: `sales_point` (built)

A card tapped opens a menu for a few seconds: up to four items, each with its
name, price and icon, with "Hi Alex" and the balance. One is chosen and bought.
Two reports:

1. **`card`**, data `{"card": "…"}`, sent when a card is tapped. The answer:

   ```json
   {
     "result": "menu",
     "ticket": "t_5f2a…",
     "expires_in": 20,
     "first_name": "Alex",
     "balance_cents": 1250,
     "items": [
       {"id": 4, "name": "Coffee", "price_cents": 80, "icon": "coffee", "affordable": true},
       {"id": 7, "name": "Tea", "price_cents": 60, "icon": "tea", "affordable": true},
       {"id": 9, "name": "Club-Mate", "price_cents": 250, "icon": "mate", "affordable": true},
       {"id": 12, "name": "Beer", "price_cents": 200, "icon": "beer", "affordable": true}
     ]
   }
   ```

   - The items are in the owner's order: at most four for now, maybe more
     later (paging).
   - `first_name` can be `null` (an account without a member's details).
   - `affordable: false` items can be shown greyed out.
   - The menu closes after the device's own timeout (about 8 seconds), which
     must be shorter than `expires_in`.

   Or `refused` (below) with `card_unknown` (with the QR code's `link`,
   section 7.4), `card_disabled`, `card_blocked`, `account_off`, `credit_off`
   or `not_set_up`.

2. **`buy`**, data `{"ticket": "t_5f2a…", "item": 4}`, sent when an item is
   chosen. The answer:
   - `{"result": "sold", "item": "Coffee", "price_cents": 80, "balance_cents": 1170, "first_name": "Alex", "actions": [{"output": 1, "seconds": 5}]}`
     -- `actions`: see below;
   - or `refused` with `ticket_expired`, `ticket_used`, `item_off`,
     `not_enough`, `credit_off`, or -- the card changed while the menu was
     open -- `card_unknown`, `card_disabled`, `card_blocked`, `account_off`.

A refusal is `{"result": "refused", "reason": "…", "message": "…"}`:

| `reason` | Meaning |
|---|---|
| `card_unknown` | No account has this card. |
| `card_disabled` | Its owner switched the card off. |
| `card_blocked` | An admin blocked it. |
| `account_off` | The card's account is switched off. |
| `credit_off` | Credit is switched off in the portal. |
| `not_set_up` | No items chosen for this device yet. |
| `ticket_expired` | The ticket is too old, or not this device's: tap the card again. |
| `ticket_used` | One ticket buys once: tap the card again. |
| `item_off` | The item is no longer on this device's menu, or no longer on sale. |
| `not_enough` | Not enough credit; `balance_cents` says what there is. |

Show `message` for a reason the firmware does not know.

- A ticket belongs to the card it was given for, to this device, and to one
  purchase. Without the card being tapped again, nothing more can be bought.
  Repeating a `buy` with the same `event_id` gets the first answer, as every
  report does.
- Setting: `"items"`, the item ids in order, chosen by the owner from their
  own price list. Setting `"actions"` is the portal's (below): the device
  need not read it.
- Resource `menu`: `{"items": [{"id", "name", "price_cents", "icon"}, …]}`,
  the same items without `affordable`, for an idle screen ("tap your card").
  It changes when the owner changes the items or prices, which the sign of
  life's `ETag` shows: fetch it again after a `200`.

**Actions (built 2026-10-15).** A sale can switch the device's outputs -- a
fridge's magnetic lock, a coffee machine's relay. The device says how many it
has with `X-Device-Outputs`; its owner sets, on their Sales page, which
output a sale switches and for how long: after any sale, or after a given
item. A successful `buy` carries what to do now:

```json
"actions": [{"output": 1, "seconds": 5}, {"output": 3, "seconds": 0.5}]
```

- Switch each `output` (counted from 1) on for its `seconds` (0.1 to 600,
  one decimal), then off; all of them at once. `actions` is `[]` when there
  is nothing to switch, and is never there in a refusal.
- An output above what the device said it has is never sent.
- What an output is wired to -- a relay on I2C, a pin -- is the firmware's
  business. Opening a door without a sale (restocking) is in the device's own
  settings menu (section 5), not in the portal.

### 8.2 Attendance: `attendance` (built 2026-10-15)

Two buttons on the screen, *Check in* and *Check out*. One is chosen, then the
card is tapped. What the portal keeps, for whom, and who sees it:
`docs/attendance-plan.md`.

1. **`check_in`**, data `{"card": "…"}`. The answer:
   - `{"result": "checked_in", "first_name": "Alex", "at": "2026-10-14T16:02:11+02:00"}`, shown as "Welcome Alex, 16:02";
   - `{"result": "refused", "reason": "already_checked_in", "message": "Already checked in since 16:02: check out first.", "first_name": "Alex", "since": "2026-10-14T16:02:11+02:00"}`.
2. **`check_out`**, data `{"card": "…"}`. The answer:
   - `{"result": "checked_out", "first_name": "Alex", "at": "2026-10-14T18:36:40+02:00", "since": "2026-10-14T16:02:11+02:00", "duration_seconds": 9240}`, shown as "Goodbye Alex, 18:36, 2 h 34 min";
   - `{"result": "refused", "reason": "not_checked_in", "message": "Not checked in here: check in first.", "first_name": "Alex"}`.

- Times are the portal's local time, with their offset; `at` is when the
  portal took the tap.
- `duration_seconds` is what counts: whole minutes, both ends by their minute
  (16:02 to 18:36 above), so always a multiple of 60.
- A stay belongs to the reader's owner -- a team, or the association -- not
  to the reader: checked in at one of the team's readers, out at another, is
  one stay.
- A stay still open at 03:15 the next night is closed as not checked out; the
  next `check_in` simply starts a new one.

Refusals besides the card's (section 8.1: `card_unknown` with its `link`,
`card_disabled`, `card_blocked`, `account_off`), each with `first_name`:

| `reason` | Meaning |
|---|---|
| `attendance_off` | Attendance is switched off for the reader's owner. |
| `not_a_member` | Not an active member of the reader's team ("Only members of Rocket Team check in here."); at the association's readers, not an active member. |
| `already_checked_in` | A stay is open: check out first. `since` says from when. |
| `not_checked_in` | No stay open here to check out of. |

No settings, no resources.

### 8.3 Test: `test` (built)

For trying the protocol out.

- Setting `"greeting"` (default `"Hello"`).
- Resource `hello`: `{"greeting": "Hello", "device": "<name>"}`.
- Report `ping`, any data, answered
  `{"result": "ok", "message": "<greeting>", "echo": <the data>}`.

Change the greeting on the device's page in the portal and watch it arrive
with the next sign of life.

---

## 9. Settings of the device itself, and commands (later)

Kept in mind, not built. The shape, so nothing gets in its way:

- **`config`** in the sign of life: the device's own settings, set by an admin
  on its page. For example `{"brightness": 80, "volume": 60, "menu_seconds": 8}`.
  They reach the device within 2 seconds, like everything else. The firmware
  ignores keys it does not know and keeps its own defaults for keys missing.
- **`commands`** in the sign of life: things to do once, such as
  `{"id": "c_81f2", "kind": "restart"}`, `"identify"` (beep and blink, to find
  the device) or `"message"` (a text on the screen). The device confirms each
  one (`POST /api/v1/device/commands/<id>`) and does it once.
- **Wi-Fi is set on the device**, in its own menu (section 5), not from the
  portal: a wrong password would lock the device out where only someone
  standing at it can fix it.

---

## 10. Icons (built)

Every item on a price list has an icon, chosen from this list where the item
is added. The firmware carries one picture per name. For a name it does not
know, it shows `other`. A new icon is a new name here and a firmware update.

The list (`ICONS` in `aeronautics_members/services/credit_sales.py`):

| Name | For |
|---|---|
| `coffee` | Coffee, espresso, cappuccino |
| `tea` | Tea |
| `hot_chocolate` | Hot chocolate |
| `beer` | Beer |
| `soft_drink` | Cola, lemonade |
| `mate` | Club-Mate and the like |
| `energy_drink` | Energy drinks |
| `juice` | Juice |
| `water` | Water |
| `snack` | Snacks, crisps |
| `sweets` | Chocolate bars, sweets |
| `ice_cream` | Ice cream |
| `other` | Anything else; also the fallback |

---

## Changes

- **2026-10-14:** first version. Built: pairing, the sign of life with `ETag`,
  the headers, firmware updates, settings by card and backup code, the sales
  point's `tap`, the test use. Planned: the menu (`card`, `buy`, icons), the
  device's own markers, attendance. Later: `config` and `commands`.
- **2026-10-14:** the sales point's menu built: reports `card` and `buy`,
  setting `items`, resource `menu`, icons on price-list items. **Replaces**
  `tap`, the `item` setting and resource, and the `too_soon` refusal; a
  device on the old firmware needs the update.
- **2026-10-14:** linking an unknown card at the reader with a QR code
  (section 7.4) -- `link` in the `card_unknown` refusal, and
  `GET /api/v1/device/card-links/<id>`. Built as first written down.
- **2026-10-15:** outputs: the `X-Device-Outputs` header and the `actions`
  of a successful `buy` (section 8.1, "Actions"). Firmware that ignores them
  is unaffected.
- **2026-10-15:** a firmware file is checked by what ESP-IDF puts in it: the
  same project and chip as the newest one (section 6.3). The planned marks
  of its own are dropped.
- **2026-10-15:** attendance built (section 8.2) as first written down, with
  the refusals `attendance_off` and `not_a_member`; `duration_seconds` counts
  whole minutes.
- **2026-10-16:** the factory image, for a fresh board flashed from the
  portal (section 6.4). Nothing changes for firmware already running.
- **2026-10-16:** crash reports (section 6.5), from the firmware's proposal,
  without its `firmware` field (the header says it).
- **2026-10-16:** the sign of life every 2 seconds, not 10 (`poll_every`
  says so in every answer; firmware that takes it from there needs nothing).
- **2026-10-14:** the `card` report's `linked` answer is gone (it came only
  while an admin waited at a reader, which was removed). Firmware that still
  handles it is harmless: it never comes.
