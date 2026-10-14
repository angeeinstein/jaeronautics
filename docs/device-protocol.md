# Device protocol

How a device talks to the members' portal: everything its firmware needs to
know, in one place. Written for whoever writes the firmware (a person or a
coding agent). The decisions behind it are in `docs/devices-plan.md`.

Each section says where it stands:

- **Built** -- the portal answers exactly this today (branch
  `claude/device-pairing` until it is merged).
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
 RUNNING: sign of life every 10 s (section 4); taps, menus, reports (sections 7, 8)
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

`GET /api/v1/device`. Send it at start, then every `poll_every` seconds (10),
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
  "settings": {"items": [4, 7, 9, 12]},
  "resources": ["menu"],
  "reports": ["card", "buy"],
  "backup_code_hash": "9f86d081884c7d65…",
  "firmware": null,
  "poll_every": 10,
  "server_time": "2026-10-14T14:02:11Z"
}
```

| Field | Meaning |
|---|---|
| `name` | Its name, else its code: show it where the device names itself. |
| `type` | What it is used as now. **It can change**: then switch to that use's screens. |
| `settings` | The settings of its use (section 8). Read them from here; never keep your own copy as the truth. |
| `resources`, `reports` | What this use may fetch and report. |
| `backup_code_hash` | Section 5. |
| `firmware` | `null`, or the firmware to install (section 6). |
| `poll_every` | Seconds until the next sign of life. |
| `server_time` | The portal's clock, UTC. Set the device's clock from it. |

A `304` costs the portal next to nothing, which is why the device asks every
10 seconds. Whatever is changed in the portal reaches the device within
seconds. Sign-of-life requests answered `304` are not listed on the device's
page in the portal; every other request is.

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
app description, and of the same project as the files uploaded before (the
project name in the app description). Version, project, build date and chip
are read from the file itself, and a version cannot be uploaded twice.

*Planned:* a few bytes of the firmware's own, right after the app description
(`.rodata_custom_desc`), that mark it as this association's firmware. The
portal and the device both check them. What they are is to be settled with
the firmware.

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

   Or `{"result": "linked", "card": "Card"}` -- an admin was waiting at this
   device for an unknown card, and it is now linked to that person. Or
   `refused` (below) with `card_unknown`, `card_disabled`, `card_blocked`,
   `account_off`, `credit_off` or `not_set_up`.

2. **`buy`**, data `{"ticket": "t_5f2a…", "item": 4}`, sent when an item is
   chosen. The answer:
   - `{"result": "sold", "item": "Coffee", "price_cents": 80, "balance_cents": 1170, "first_name": "Alex"}`;
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
  own price list.
- Resource `menu`: `{"items": [{"id", "name", "price_cents", "icon"}, …]}`,
  the same items without `affordable`, for an idle screen ("tap your card").
  It changes when the owner changes the items or prices, which the sign of
  life's `ETag` shows: fetch it again after a `200`.

### 8.2 Attendance: `attendance` (planned, around January)

Two buttons on the screen, *Check in* and *Check out*. One is chosen, then the
card is tapped.

1. **`check_in`**, data `{"card": "…"}`. The answer:
   - `{"result": "checked_in", "first_name": "Alex", "at": "2026-10-14T16:02:11+02:00"}`, shown as "Welcome Alex, 16:02";
   - `{"result": "refused", "reason": "already_checked_in", "message": "Already checked in since 16:02: check out first.", "first_name": "Alex", "since": "2026-10-14T16:02:11+02:00"}`.
2. **`check_out`**, data `{"card": "…"}`. The answer:
   - `{"result": "checked_out", "first_name": "Alex", "at": "2026-10-14T18:36:40+02:00", "since": "2026-10-14T16:02:11+02:00", "duration_seconds": 9269}`, shown as "Goodbye Alex, 18:36, 2 h 34 min";
   - `{"result": "refused", "reason": "not_checked_in", "message": "…", "first_name": "Alex"}`.

Plus the card refusals of section 8.1 (`card_unknown`, `card_disabled`,
`card_blocked`, `account_off`). The portal works out the duration. What
attendance records, for whom, and who sees it is decided when it is built.

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
  They reach the device within 10 seconds, like everything else. The firmware
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
