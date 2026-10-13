"""The firmware starter a device's key comes with (docs/devices-plan.md): one
Markdown file holding everything firmware for this device needs -- the
server, its code and key, every request its type makes and what it is
answered, the rules (a new event id per report, kept for retries; the board's
MAC address; checking the server's certificate) -- and an example sketch
for an ESP32 with the Arduino framework.

"I could just hand this firmware example package to a coding agent and ask
it to build a firmware around this." It is made only when the key is shown
(a device added, or its key rotated), since the key is in it.

The sketch is a starting point, written against Arduino-ESP32 and
ArduinoJson 7 but not compiled here.
"""

from . import devices as devices_service
from . import device_types
from .clock import get_now_utc

FIRMWARE_VERSION = "0.1.0"


def file_name(device):
    return f"device-{device.code}-starter.md"


def _requests(device_type, server):
    lines = [
        "### Sign of life",
        "",
        f"`GET {server}` -- at start, then about once a minute. It answers who the device is and its",
        "settings, which its owner may change at any time in the portal: read them again from here rather",
        "than keeping them in the firmware.",
        "",
        "```json",
        '{"device_id": 3, "code": "K7MQ-2X9A", "name": "Workshop fridge", "type": "' + device_type.slug + '",',
        ' "owner": "Rocket Team", "place": "Workshop", "settings": {...}, "resources": [...], "reports": [...],',
        ' "server_time": "2026-10-13T11:00:58Z"}',
        "```",
    ]
    for name, offered in sorted(device_type.resources.items()):
        lines += ["", f"### Fetch `{name}`", "", f"`GET {server}/resources/{name}` -- {offered.description}",
                  "", f'Answered `{{"resource": "{name}", "data": {{...}}}}`.']
    for name, report in sorted(device_type.reports.items()):
        lines += [
            "", f"### Report `{name}`", "",
            f"`POST {server}/events` with `Content-Type: application/json` -- {report.description}", "",
            "```json",
            f'{{"event_id": "8f2c4e0b9a1d4c7e8b3f6a2d1e0c9b7a", "kind": "{name}", "data": {{...}}}}',
            "```", "",
            'Answered `{"event_id": "...", "answer": {...}}`.',
        ]
    return lines


def starter(device, key, server):
    """The starter file for ``device`` with its new ``key``, as Markdown."""
    device_type = device_types.get(device.type)
    label = devices_service.label(device)
    lines = [
        f"# Firmware starter: {label} ({device.code})",
        "",
        f"Made {get_now_utc().strftime('%d.%m.%Y %H:%M')} UTC by the members' portal, for a device of type",
        f"**{device_type.label}** owned by **{devices_service.owner_name(device)}**.",
        "",
        "> **This file holds the device's key.** Keep it like a password: never in a public repository.",
        "> A new key (*Rotate* on the device's page in the portal) makes this one stop working.",
        "",
        "## The device",
        "",
        "| | |",
        "|---|---|",
        f"| Server | `{server}` |",
        f"| Code | `{device.code}` |",
        f"| Key | `{key}` |",
        f"| Type | `{device_type.slug}` ({device_type.label}) |",
        "",
        "## Talking to the portal",
        "",
        "- **HTTPS only, and check the server's certificate.** Never turn the check off (`setInsecure()`):",
        "  the key travels with every request.",
        "- **Every request carries these headers:**",
        f"  - `Authorization: Bearer {key}`",
        f"  - `X-Device-Firmware: {FIRMWARE_VERSION}` -- the firmware's version, shown in the portal;",
        "  - `X-Device-Hardware: 24:6F:28:AA:BB:CC` -- the board's MAC address. The portal shows it and",
        "    notes it when the key turns up on another board.",
        "- **A report's `event_id`** is made new for every event: 16 random bytes as hex",
        "  (`esp_random()`), at most 64 characters of `A-Z a-z 0-9 . _ : -`. Send it **again unchanged**",
        "  when a report has to be repeated (a timeout, no answer, a 5xx): the portal answers a repeated",
        "  report as the first time and counts it once. A counter is not enough -- it starts again after",
        "  a restart. Reports are remembered for 90 days.",
        "- **When the portal cannot be reached,** show it and do not act: nothing is queued on the device.",
        "- **Refusals** are HTTP statuses with a body `{\"error\": {\"code\": \"...\", \"message\": \"...\"}}`:",
        "",
        "| Status | `code` | Meaning |",
        "|---|---|---|",
        "| 401 | `device_key_missing`, `device_key_unknown`, `device_key_revoked` | The key is wrong or "
        "revoked: stop, and show it. |",
        "| 403 | `device_off` | Switched off in the portal: try again in a minute. |",
        "| 404 | `device_resource_unknown`, `device_report_unknown` | Its type does not offer that. |",
        "| 400 | `validation_error`, `device_report_too_large` | A request the firmware got wrong. |",
        "| 429 | `rate_limited` | More than 120 requests a minute: slow down. |",
        "| 5xx | | The portal's trouble: repeat a report with the same `event_id`, a little later. |",
        "",
        "## What a device of this type asks and reports",
        "",
        *_requests(device_type, server),
    ]
    if device_type.firmware_notes:
        lines += ["", f"## {device_type.label}: what the answers mean", "", device_type.firmware_notes.strip()]
    lines += ["", "## Example: ESP32 with the Arduino framework", "",
              "Written against Arduino-ESP32 (2.x/3.x) and ArduinoJson 7; a starting point, not tested",
              "firmware. Fill in the Wi-Fi and the server's root certificate.", "",
              "```cpp", _sketch(device, device_type, key, server), "```", ""]
    return "\n".join(lines)


def _sketch(device, device_type, key, server):
    tap = "tap" in device_type.reports
    report_kind = "tap" if tap else next(iter(sorted(device_type.reports)), "ping")
    top = f"""// {devices_service.label(device)} ({device.code}): {device_type.label}
// Made by the members' portal. Holds the device's key: keep it private.
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* WIFI_SSID = "...";
const char* WIFI_PASSWORD = "...";

const char* SERVER = "{server}";
const char* DEVICE_KEY = "{key}";
const char* FIRMWARE_VERSION = "{FIRMWARE_VERSION}";

// The root certificate of the server's certificate chain, in PEM. To find
// it: open the server in a browser, view its certificate, and export the
// topmost (root) one -- or run
//   openssl s_client -showcerts -connect {server.split("/")[2]}:443
// and take the last certificate. (On Arduino-ESP32 3.x the certificate
// bundle built into the core can be used instead: setCACertBundle.)
const char* ROOT_CA = R"PEM(
-----BEGIN CERTIFICATE-----
...
-----END CERTIFICATE-----
)PEM";

WiFiClientSecure tls;

// 16 random bytes as hex: a new one for every event, kept while it is retried.
String newEventId() {{
  char id[33];
  for (int i = 0; i < 16; i++) snprintf(id + 2 * i, 3, "%02x", (uint8_t)(esp_random() & 0xFF));
  return String(id);
}}

// One request to the portal. Returns the HTTP status (negative: no connection)
// and puts the body into `answer`.
int request(const char* method, const String& url, const String& body, String& answer) {{
  HTTPClient http;
  http.setTimeout(5000);
  if (!http.begin(tls, url)) return -1;
  http.addHeader("Authorization", String("Bearer ") + DEVICE_KEY);
  http.addHeader("X-Device-Firmware", FIRMWARE_VERSION);
  http.addHeader("X-Device-Hardware", WiFi.macAddress());
  int status;
  if (strcmp(method, "POST") == 0) {{
    http.addHeader("Content-Type", "application/json");
    status = http.POST(body);
  }} else {{
    status = http.GET();
  }}
  answer = status > 0 ? http.getString() : String();
  http.end();
  return status;
}}

// The sign of life: at start, then about once a minute.
bool signOfLife(JsonDocument& hello) {{
  String body;
  int status = request("GET", SERVER, "", body);
  if (status != 200) {{
    Serial.printf("Sign of life: %d %s\\n", status, body.c_str());
    return false;
  }}
  return !deserializeJson(hello, body);
}}

// A report, repeated with the same event id after a timeout or a 5xx, so
// the portal counts it once. Fills `answer` with the portal's answer.
bool report(const char* kind, JsonDocument& data, JsonDocument& answer) {{
  String eventId = newEventId();
  JsonDocument sent;
  sent["event_id"] = eventId;
  sent["kind"] = kind;
  sent["data"] = data;
  String body;
  serializeJson(sent, body);
  for (int attempt = 0; attempt < 3; attempt++) {{
    String text;
    int status = request("POST", String(SERVER) + "/events", body, text);
    if (status == 200) {{
      JsonDocument reply;
      if (deserializeJson(reply, text)) return false;
      answer.set(reply["answer"]);
      return true;
    }}
    Serial.printf("Report %s: %d %s\\n", kind, status, text.c_str());
    if (status > 0 && status < 500) return false;  // refused: repeating will not help
    delay(1000 * (attempt + 1));
  }}
  return false;
}}
"""
    if tap:
        body = """
// Read a card's ID (UID) as hex, upper case, without separators:
// "04A1B2C3D4E5F6". Fill in with the reader's library (MFRC522, PN532, ...).
bool readCardId(String& cardId) {
  return false;
}

void showSold(JsonDocument& answer) { /* green LED, one beep */ }
void showRefused(JsonDocument& answer) { /* red LED, two beeps; answer["reason"] says why */ }
void showLinked(JsonDocument& answer) { /* three short beeps */ }
void showOffline() { /* the portal cannot be reached: red LED blinking */ }

unsigned long lastSignOfLife = 0;

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  tls.setCACert(ROOT_CA);
  JsonDocument hello;
  if (signOfLife(hello)) Serial.printf("I am %s\\n", (const char*)hello["name"]);
  lastSignOfLife = millis();
}

void loop() {
  if (millis() - lastSignOfLife > 60000) {
    JsonDocument hello;
    signOfLife(hello);
    lastSignOfLife = millis();
  }
  String cardId;
  if (!readCardId(cardId)) return;
  JsonDocument data, answer;
  data["card"] = cardId;
  if (!report("tap", data, answer)) {
    showOffline();
  } else if (answer["result"] == "sold") {
    showSold(answer);
  } else if (answer["result"] == "linked") {
    showLinked(answer);
  } else {
    showRefused(answer);
  }
  delay(1500);  // one tap at a time; the portal also refuses a second sale within 5 s
}"""
    else:
        body = f"""
unsigned long lastSignOfLife = 0;

void setup() {{
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(250);
  tls.setCACert(ROOT_CA);
  JsonDocument hello;
  if (signOfLife(hello)) Serial.printf("I am %s\\n", (const char*)hello["name"]);
  JsonDocument data, answer;
  data["n"] = 1;
  if (report("{report_kind}", data, answer)) serializeJsonPretty(answer, Serial);
  lastSignOfLife = millis();
}}

void loop() {{
  if (millis() - lastSignOfLife > 60000) {{
    JsonDocument hello;
    signOfLife(hello);
    lastSignOfLife = millis();
  }}
}}"""
    return (top + body).strip("\n")
