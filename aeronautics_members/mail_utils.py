import os
import json
import ast
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from html.parser import HTMLParser

from flask import current_app, has_app_context, render_template
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# How long one step of a real send may wait on the mail server. Without it a
# server that accepts the connection and then goes quiet holds the request --
# a signup, a Stripe webhook -- for as long as the socket stays open, and a
# few of those take every worker the portal has. A normal send finishes in
# well under a second; a stuck one now fails like any other send error, which
# the callers already record and retry.
SMTP_SEND_TIMEOUT_SECONDS = 20


def load_mail_accounts_config(required=False):
    if has_app_context():
        try:
            try:
                from .db_models import MailAccount, db
            except ImportError:
                from db_models import MailAccount, db

            mail_accounts = {
                account.account_key: account.to_config()
                for account in db.session.execute(
                    db.select(MailAccount).order_by(MailAccount.account_key.asc())
                ).scalars()
            }
            if mail_accounts:
                return mail_accounts
        except Exception:
            pass

    raw_value = os.getenv("MAIL_ACCOUNTS_JSON", "").strip()
    if not raw_value:
        if required:
            raise ValueError("No mail accounts are configured in the database or MAIL_ACCOUNTS_JSON.")
        return {}

    candidates = [raw_value]
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ("'", '"'):
        candidates.append(raw_value[1:-1])

    for candidate in candidates:
        try:
            data = json.loads(candidate or "{}")
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    try:
        data = ast.literal_eval(raw_value)
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    raise ValueError("MAIL_ACCOUNTS_JSON is not a valid JSON object.")


def probe_mail_account_connection(config):
    try:
        context = ssl.create_default_context()
        host = config["host"]
        port = int(config["port"])
        username = config["user"]
        password = config["pass"]

        if config.get("starttls", False):
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(username, password)
        else:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
                server.login(username, password)

        return True, "SMTP connection and authentication succeeded."
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed."
    except smtplib.SMTPConnectError as exc:
        return False, f"Could not connect to the SMTP server: {exc}"
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return False, f"SMTP connection test failed: {exc}"



# The header of every email: the black band with the logo and the turquoise
# line, as one image. Mail apps in dark mode recolour backgrounds and text but
# never images, so a logo drawn onto its own background looks the same in every
# one of them -- where a white logo on a band an app had turned light vanished.
# Made from logo_joanneum_aeronautics_negativ.png at twice its shown size (640
# wide), for phone screens; 25 KB, where attaching the logo file itself sent
# nearly 300 KB with every email.
EMAIL_LOGO_FILE = "email_header.png"


def email_logo_attachment():
    """The logo as the inline image the templates reference (``cid:logo``), or None."""
    if not has_app_context():
        return None
    path = os.path.join(current_app.root_path, "static", EMAIL_LOGO_FILE)
    return {"path": path, "cid": "logo"} if os.path.exists(path) else None


class _PlainTextFromHtml(HTMLParser):
    """Reads the text out of one of our emails, for the plain-text part.

    Enough for the emails this portal writes, not a general converter: blocks
    become line breaks, a link keeps its address in brackets, and what the
    reader never sees -- the stylesheet, the hidden inbox preview -- is left out.
    """

    BLOCKS = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "table", "ul"}
    SKIPPED = {"head", "style", "title", "script"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skipping = 0
        self.hidden = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in self.SKIPPED:
            self.skipping += 1
        elif tag == "div":
            hide = "display:none" in (attributes.get("style") or "").replace(" ", "")
            self.hidden.append(hide)
            if hide:
                self.skipping += 1
            else:
                self.parts.append("\n")
        elif tag == "a":
            self.links.append((attributes.get("href"), len(self.parts)))
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIPPED:
            self.skipping = max(0, self.skipping - 1)
        elif tag == "div":
            if self.hidden and self.hidden.pop():
                self.skipping = max(0, self.skipping - 1)
            else:
                self.parts.append("\n")
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            text = "".join(self.parts[start:]).strip()
            if href and not href.startswith("cid:") and href != text and not self.skipping:
                self.parts.append(f" ({href})")
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self):
        lines = [line.strip() for line in "".join(self.parts).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def html_to_text(html):
    parser = _PlainTextFromHtml()
    parser.feed(html)
    parser.close()
    return parser.text()


def send_mail(from_account, to_email, subject, template_name=None, body=None, attachments=None, bcc_emails=None, return_error=False, cc_emails=None, **template_vars):
    """
    Sends an email using pre-configured SMTP accounts.

    When ``return_error`` is True, the function returns ``(success, error_message)``.
    Otherwise it preserves the legacy ``True``/``False`` return value.
    """
    error_message = None
    try:
        mail_accounts = load_mail_accounts_config(required=True)
        config = mail_accounts.get(from_account)

        if not config:
            raise ValueError(f"Mail account '{from_account}' not found in configuration.")

        primary_recipient = (to_email or "").strip()
        if not primary_recipient:
            raise ValueError("A primary recipient email address is required.")

        bcc_list = [
            str(email).strip()
            for email in (bcc_emails or [])
            if str(email).strip()
        ]
        # Copies everybody can see, unlike the blind ones.
        cc_list = [
            str(email).strip()
            for email in (cc_emails or [])
            if str(email).strip() and str(email).strip() != primary_recipient
        ]
        recipients = []
        for email in [primary_recipient, *cc_list, *bcc_list]:
            if email not in recipients:
                recipients.append(email)

        message = MIMEMultipart("related")
        message["Subject"] = subject
        message["From"] = config["user"]
        message["To"] = primary_recipient
        if cc_list:
            message["Cc"] = ", ".join(dict.fromkeys(cc_list))

        if template_name:
            # Every template's footer carries the year. Supplied here rather
            # than by each caller: three that forgot it could not be rendered
            # at all, so those emails were never sent.
            template_vars.setdefault("now", datetime.now(timezone.utc))
            template_vars.setdefault("subject", subject)
            html_body = render_template(f"emails/{template_name}", **template_vars)
            logo = email_logo_attachment()
            if logo and not any((a or {}).get("cid") == "logo" for a in attachments or []):
                attachments = [*(attachments or []), logo]
        elif body:
            html_body = body
        else:
            raise ValueError("Either 'template_name' or 'body' must be provided.")

        # A plain-text part beside the HTML. Mail filters -- a university's
        # among them -- count an HTML-only message against it, and some
        # readers show nothing else.
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(html_to_text(html_body), "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        message.attach(alternative)

        if attachments:
            for attachment in attachments:
                try:
                    with open(attachment["path"], "rb") as handle:
                        img = MIMEImage(handle.read())
                        img.add_header("Content-ID", f"<{attachment['cid']}>")
                        message.attach(img)
                except Exception as exc:
                    if has_app_context():
                        current_app.logger.warning("Error attaching image %s: %s", attachment.get("path"), exc)
                    else:
                        print(f"Error attaching image {attachment.get('path')}: {exc}")

        context = ssl.create_default_context()
        if config.get("starttls", False):
            with smtplib.SMTP(config["host"], config["port"], timeout=SMTP_SEND_TIMEOUT_SECONDS) as server:
                server.starttls(context=context)
                server.login(config["user"], config["pass"])
                server.sendmail(config["user"], recipients, message.as_string())
        else:
            with smtplib.SMTP_SSL(
                config["host"], config["port"], context=context, timeout=SMTP_SEND_TIMEOUT_SECONDS,
            ) as server:
                server.login(config["user"], config["pass"])
                server.sendmail(config["user"], recipients, message.as_string())

        if has_app_context():
            current_app.logger.info("Email sent successfully to %s from %s", ", ".join(recipients), config["user"])
        else:
            print(f"Email sent successfully to {', '.join(recipients)} from {config['user']}")
        return (True, None) if return_error else True

    except Exception as exc:
        error_message = str(exc)
        if has_app_context():
            current_app.logger.error("Error sending email: %s", exc)
        else:
            print(f"Error sending email: {exc}")
        return (False, error_message) if return_error else False
