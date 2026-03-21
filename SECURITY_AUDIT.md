# Security Audit Report — Jaeronautics Members Application

**Date:** 2026-03-21  
**Scope:** Full repository and application (`aeronautics_members/`, deployment configuration, install scripts)  
**Deployment Context:** Primarily behind a Cloudflare Tunnel

---

## Executive Summary

The application is well-structured from a security standpoint. It uses Flask with CSRF protection (Flask-WTF), rate limiting (Flask-Limiter), proper password hashing (Werkzeug/PBKDF2-SHA256), session hardening, and an audit log throughout. The most significant actionable findings are: a GET-based forum logout endpoint that is susceptible to CSRF logout, the bootstrap/install pipeline not verifying download integrity, SMTP passwords stored in plaintext in the database, and a CSRF error handler that could allow an open redirect. None of the issues found are immediately critical in the intended Cloudflare Tunnel deployment, but several are worth fixing for defence-in-depth.

---

## Findings

### 🔴 HIGH — Forum Logout Endpoint Accepts GET (CSRF Logout)

**File:** `aeronautics_members/app.py`, line 4835  
**Endpoint:** `GET /forum/logout`

```python
@app.route("/forum/logout", methods=["GET"])
def forum_logout():
    if current_user.is_authenticated:
        ...
        logout_user()
```

The `/forum/logout` endpoint accepts GET requests and does not require a CSRF token. Any page on any origin can force a logged-in user to be silently logged out by embedding an element such as `<img src="https://app.example/forum/logout">`. This is a textbook CSRF logout vulnerability. The main `/logout` endpoint (line 4814) correctly requires POST, making the inconsistency more notable.

**Recommendation:** Change the endpoint to `methods=["POST"]` and require a CSRF token, consistent with the main logout endpoint. If a GET entry point is needed (e.g. from a Discourse redirect), redirect it to a confirmation page or a POST form rather than executing the logout directly.

---

### 🔴 HIGH — Install Bootstrap Downloads and Executes Without Integrity Verification

**File:** `bootstrap.sh`, lines 30–53

```bash
curl -fsSL "${INSTALL_URL}" -o "${TMP_SCRIPT}"
...
exec sudo -E bash "${TMP_SCRIPT}" "$@"
```

`bootstrap.sh` downloads `install.sh` from `raw.githubusercontent.com` over HTTPS and executes it directly with root privileges. No checksum or signature is verified. If the GitHub account were compromised, a supply-chain attacker could push a malicious `install.sh` and every new installation would run it as root. HTTPS protects against network-level interception but not against a compromised upstream repository.

**Recommendation:** Publish a SHA-256 checksum of each `install.sh` release (e.g. in a `CHECKSUMS` file or in the README) and add a verification step to `bootstrap.sh` before execution. Alternatively, pin to a specific Git commit hash in the raw URL rather than `main`.

---

### 🔴 HIGH — `SECRET_KEY` Not Validated at Startup

**File:** `aeronautics_members/app.py`, lines 165 and 2239  
**File:** `.env.example`, line 1 (`SECRET_KEY=change-me`)

The application reads `SECRET_KEY` from the environment at import time but does not check whether it has been changed from the placeholder, is sufficiently long, or is non-empty. If an operator deploys without setting a real secret key, all session cookies and signed tokens (password-reset, email-verify, forum-entry) can be forged by anyone who knows the default value `change-me`.

**Recommendation:** Add an explicit startup check in `create_app()`:

```python
if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "SECRET_KEY is not configured or too short. "
        "Set a random secret of at least 32 characters in your .env file."
    )
```

Also consider detecting the known placeholder value `change-me`.

---

### 🟠 MEDIUM — SMTP Passwords Stored in Plaintext in the Database

**File:** `aeronautics_members/db_models.py`, line 393  
**File:** `aeronautics_members/app.py` (mail account export, lines 4677–4690)

```python
class MailAccount(db.Model):
    password = db.Column(db.String(255), nullable=False)
```

SMTP passwords are persisted in the `mail_accounts` table as plain strings. If the database is read by an unauthorised party (SQL injection, database backup exposure, direct access), all SMTP credentials are immediately available. The JSON export feature also outputs them in plaintext.

**Recommendation:** At a minimum, encrypt SMTP passwords at rest using a symmetric key derived from `SECRET_KEY` (e.g. with `cryptography.fernet`). For the export feature, already gated behind password confirmation, this is an acceptable trade-off — plaintext in the export file is expected — but the in-database copy should be protected.

---

### 🟠 MEDIUM — CSRF Error Handler Redirects to `request.referrer` (Open Redirect)

**File:** `aeronautics_members/app.py`, lines 5202–5205

```python
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash(...)
    return redirect(request.referrer or url_for("index"))
```

The `Referer` header is attacker-controlled. An external site can send a cross-origin form POST that triggers a CSRF error and carry a `Referer` header pointing to a phishing page. The application will then redirect the user to that external URL. The `is_safe_next_url` guard used elsewhere is not applied here.

**Recommendation:** Replace `request.referrer` with a safe fallback:

```python
return redirect(url_for("index"))
```

Or apply `is_safe_next_url` before trusting the referrer.

---

### 🟠 MEDIUM — Rate Limiting Falls Back to Per-Worker Memory Store if Redis Is Unavailable

**File:** `aeronautics_members/app.py`, lines 192 and 212  
**File:** `deploy/systemd/aeronautics.service` (3 Gunicorn workers)

Flask-Limiter uses Redis as a shared counter. If Redis becomes unavailable, by default Flask-Limiter fails open (using an in-memory store per process). With 3 Gunicorn workers the effective rate limits triple: the login endpoint becomes 30 attempts per 15 minutes per IP per worker process rather than 10 total.

**Recommendation:** Configure Flask-Limiter to fail closed on Redis outage:

```python
limiter = Limiter(
    ...
    on_breach=...,
    storage_options={"socket_connect_timeout": 2, "socket_timeout": 2},
    default_limits_deduct_when=lambda: True,
)
```

Or explicitly set `strategy="fixed-window-elastic-expiry"` with `swallow_errors=False` so requests are blocked rather than allowed through when Redis is unreachable.

---

### 🟠 MEDIUM — No Per-Account Brute-Force Lockout

**File:** `aeronautics_members/app.py`, lines 4759–4805

Rate limiting for the login endpoint is per source IP (`10 per 15 minute`). A distributed attacker (multiple IPs) can still enumerate and brute-force passwords for a targeted account without being throttled.

**Recommendation:** Implement a per-email-address counter stored in Redis alongside the IP-based limit. After a configurable number of failed attempts for a given email address (e.g. 20 over 60 minutes), require the user to reset their password or add a time delay before their next allowed attempt. The counter should be reset on a successful login.

---

### 🟠 MEDIUM — Sensitive Settings (API Keys) Stored in Plaintext in the Settings Table

**File:** `aeronautics_members/db_models.py`, lines 380–382  
**File:** `aeronautics_members/app.py`, lines 200 and 4221–4234

The `settings` table uses `VARCHAR(255)` for all values, including `discourse_api_key`, `discourse_connect_secret`, `stripe_secret_key`, and `stripe_webhook_secret`. All of these are stored in plaintext.

**Recommendation:** Encrypt sensitive setting values at rest using the same approach suggested for SMTP passwords (symmetric encryption keyed on `SECRET_KEY`). The existing `SENSITIVE_SETTING_KEYS` set already identifies which keys need protection.

---

### 🟠 MEDIUM — ProxyFix Trust Level May Allow IP Spoofing if Cloudflare Is Bypassed

**File:** `aeronautics_members/app.py`, line 2324

```python
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
```

With `x_for=1`, Werkzeug trusts the first `X-Forwarded-For` entry. When traffic flows through the Cloudflare Tunnel this is the real client IP set by Cloudflare, which is correct. However, if the Gunicorn socket or port is ever reachable directly (e.g. by someone on the same host or network segment), an attacker can set an arbitrary `X-Forwarded-For` header and appear to come from a different IP, bypassing IP-based rate limits entirely.

**Recommendation:** Ensure the Gunicorn bind address remains `127.0.0.1:8000` (already configured in the systemd unit) and is firewalled. If rate limiting is critical, also consider using Cloudflare's own rate-limiting feature as a secondary layer.

---

### 🟡 LOW — Password Reset Nonce Compared Without Constant-Time Equality

**File:** `aeronautics_members/app.py`, lines 3470–3472

```python
if user is None or not token_nonce or token_nonce != user.password_reset_nonce:
```

The nonce comparison uses Python's `!=` operator, which is not constant-time. A remote timing oracle attack is theoretically possible, though in practice the database round-trip time drowns out the string comparison timing signal.

**Recommendation:** Replace with `hmac.compare_digest`:

```python
import hmac
if user is None or not token_nonce or not hmac.compare_digest(token_nonce, user.password_reset_nonce or ""):
```

---

### 🟡 LOW — Minimum Password Length of 8 Characters

**File:** `aeronautics_members/forms.py`, lines 121, 167, 180, 190

```python
password = PasswordField(..., validators=[DataRequired(), Length(min=8, max=128)])
```

NIST SP 800-63B and OWASP currently recommend a minimum of 12 characters for user-chosen passwords (8 was the prior guidance).

**Recommendation:** Increase `min=8` to `min=12` across all password fields.

---

### 🟡 LOW — Health Check Endpoint Is Publicly Accessible and Discloses Hostname

**File:** `aeronautics_members/app.py`, lines 2911–2920  
**Endpoint:** `GET /__health`

```python
return jsonify({
    "status": "ok",
    "app": "jaeronautics",
    "timestamp": ...,
    "host": request.host,
})
```

The endpoint is unauthenticated and returns the internal hostname. While this is common practice for load-balancer health checks, the hostname field adds minor information disclosure.

**Recommendation:** Remove the `"host"` field from the response, or restrict access to the health endpoint to localhost / internal monitoring IPs via the NGINX configuration.

---

### 🟡 LOW — `send_mail` Does Not Set an SMTP Connection Timeout

**File:** `aeronautics_members/mail_utils.py`, lines 153–161

```python
with smtplib.SMTP(config["host"], config["port"]) as server:
    server.starttls(context=context)
    ...
```

The `probe_mail_account_connection` helper sets a 15-second timeout, but the live `send_mail` function does not. A slow or unresponsive SMTP server can block a Gunicorn worker thread indefinitely, reducing application availability.

**Recommendation:** Add `timeout=30` (or a configurable value) to both `smtplib.SMTP(...)` and `smtplib.SMTP_SSL(...)` calls in `send_mail`.

---

### 🟡 LOW — External CDN Scripts Loaded Without Subresource Integrity (SRI)

**File:** `deploy/nginx/aeronautics.conf`, line 8 (CSP)  
**Templates** (Bootstrap/JS loaded from `cdn.jsdelivr.net`)

The Content-Security-Policy allows scripts and styles from `https://cdn.jsdelivr.net/npm/` without restricting to specific file hashes. If jsdelivr.net were compromised or the URL accidentally served a malicious file, users' browsers would execute it.

**Recommendation:** Add `integrity="sha384-..."` attributes to each CDN `<script>` and `<link>` tag (using the SRI Hash Generator at https://www.srihash.org/), and add `require-sri-for script style` to the CSP.

---

### 🟡 LOW — Missing CSP Directives: `connect-src` and `report-uri`

**File:** `deploy/nginx/aeronautics.conf`, line 8

The current CSP:
```
default-src 'self'; script-src 'self' https://js.stripe.com https://cdn.jsdelivr.net/npm/;
style-src 'self' https://cdn.jsdelivr.net/npm/;
frame-src https://js.stripe.com;
img-src 'self' data:;
```

Missing directives:
- **`connect-src`**: Stripe.js makes XHR/fetch calls to `https://api.stripe.com`. Without an explicit `connect-src`, these may be blocked or the policy is incomplete.
- **`report-uri` / `report-to`**: CSP violations are currently silently discarded. Adding a reporting endpoint would surface injection attempts.
- **`font-src`**: If any web fonts are used, they will be blocked by the `default-src 'self'` fallback unless explicitly allowed.

**Recommendation:**
```
connect-src 'self' https://api.stripe.com;
font-src 'self';
report-uri /csp-report;   # optional but recommended
```

---

## Positive Findings

The following security controls are correctly implemented and should be maintained:

| Area | Implementation |
|------|---------------|
| Password hashing | Werkzeug `generate_password_hash` (PBKDF2-SHA256 with salt) — `db_models.py:87-94` |
| Session cookies | `Secure`, `HttpOnly`, `SameSite=Lax` — `app.py:2251-2256` |
| CSRF protection | Flask-WTF `CSRFProtect` on all state-changing forms; Stripe webhook correctly CSRF-exempt — `app.py:205` |
| Host header injection | `validate_request_host` before-request hook with `is_trusted_host` whitelist — `app.py:2305-2311` |
| Open redirect prevention | `is_safe_next_url` applied to all `next` parameter redirects — `app.py:1237-1243` |
| Rate limiting | Per-IP limits on login, registration, password reset, admin email endpoints — `app.py:193-197` |
| Stripe webhook verification | `stripe.Webhook.construct_event` with signature validation — `app.py:4879` |
| Password reset nonce | Single-use nonce rotated on every `set_password` call — `db_models.py:88-89` |
| Token expiry | Short TTLs on all signed tokens (24h reset, 1-week verification, 1h forum auto-login) |
| Audit logging | Comprehensive audit trail with sensitive field redaction — `app.py:1697-1709` |
| Image upload safety | Magic-byte validation + Pillow processing with pixel-bomb guard (25 MP limit) — `forum_service.py:104-113` |
| SQL injection | SQLAlchemy ORM with parameterized queries throughout |
| XSS | Jinja2 auto-escaping; no `|safe` filter on user-controlled data found |
| Admin role protection | `admin_required` decorator + minimum-admin-count guard on role revocation |
| Logout on password set | `password_reset_nonce` cleared when password changes; token becomes immediately invalid |
| SMTP TLS | TLS enforced on all SMTP connections (`SMTP_SSL` or `STARTTLS`) — `mail_utils.py:152-161` |
| Gunicorn binding | Binds to `127.0.0.1:8000` only — `aeronautics.service:10` |
| Security headers | X-Frame-Options, X-Content-Type-Options, Referrer-Policy set in NGINX — `aeronautics.conf:5-8` |
| Mail export gated | Password confirmation required before exporting SMTP credentials — `app.py:4658` |

---

## Summary Table

| Severity | # | Finding |
|----------|---|---------|
| 🔴 High | 3 | Forum GET logout (CSRF), bootstrap without integrity check, missing SECRET_KEY validation |
| 🟠 Medium | 5 | CSRF error open redirect, Redis fallback rate limit, per-account brute force, plaintext secrets in DB, ProxyFix IP spoofing |
| 🟡 Low | 6 | Non-constant-time nonce compare, 8-char password minimum, health endpoint disclosure, SMTP timeout, CDN without SRI, incomplete CSP |

No critical remote-code-execution or authentication-bypass vulnerabilities were found. All high-severity issues are straightforward to remediate.
