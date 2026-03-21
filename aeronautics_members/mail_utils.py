import os
import json
import ast
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from flask import current_app, has_app_context, render_template
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


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



def send_mail(from_account, to_email, subject, template_name=None, body=None, attachments=None, bcc_emails=None, return_error=False, **template_vars):
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
        recipients = []
        for email in [primary_recipient, *bcc_list]:
            if email not in recipients:
                recipients.append(email)

        message = MIMEMultipart("related")
        message["Subject"] = subject
        message["From"] = config["user"]
        message["To"] = primary_recipient

        if template_name:
            html_body = render_template(f"emails/{template_name}", **template_vars)
        elif body:
            html_body = body
        else:
            raise ValueError("Either 'template_name' or 'body' must be provided.")

        message.attach(MIMEText(html_body, "html"))

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
            with smtplib.SMTP(config["host"], config["port"]) as server:
                server.starttls(context=context)
                server.login(config["user"], config["pass"])
                server.sendmail(config["user"], recipients, message.as_string())
        else:
            with smtplib.SMTP_SSL(config["host"], config["port"], context=context) as server:
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
