import os
import json
import ast
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from flask import has_app_context, render_template
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


def send_mail(from_account, to_email, subject, template_name=None, body=None, attachments=None, **template_vars):
    """
    Sends an email using pre-configured SMTP accounts from .env.

    :param from_account: The key of the sender account in the .env config (e.g., 'office').
    :param to_email: The recipient's email address.
    :param subject: The subject of the email.
    :param template_name: (Optional) The name of the HTML template file in 'templates/emails/'.
    :param body: (Optional) A raw string to be used as the email body.
    :param attachments: (Optional) A list of dictionaries for files to attach, e.g., [{'path': 'path/to/logo.png', 'cid': 'logo'}]
    :param template_vars: A dictionary of variables to pass to the email template.
    """
    try:
        # 1. Load SMTP account configurations from .env
        mail_accounts = load_mail_accounts_config(required=True)
        config = mail_accounts.get(from_account)

        if not config:
            raise ValueError(f"Mail account '{from_account}' not found in configuration.")

        # 2. Prepare the email message
        message = MIMEMultipart("related")
        message["Subject"] = subject
        message["From"] = config["user"]
        message["To"] = to_email

        # 3. Get the HTML body from either a template or a raw string
        html_body = ""
        if template_name:
            html_body = render_template(f"emails/{template_name}", **template_vars)
        elif body:
            html_body = body
        else:
            raise ValueError("Either 'template_name' or 'body' must be provided.")
        
        # 4. Attach the HTML body to the email
        message.attach(MIMEText(html_body, "html"))

        # 5. Handle embedded images
        if attachments:
            for attachment in attachments:
                try:
                    with open(attachment['path'], 'rb') as f:
                        img = MIMEImage(f.read())
                        img.add_header('Content-ID', f"<{attachment['cid']}>")
                        message.attach(img)
                except Exception as e:
                    print(f"Error attaching image {attachment['path']}: {e}")

        # 6. Send the email
        context = ssl.create_default_context()
        
        # Check if we should use STARTTLS (explicit TLS)
        if config.get("starttls", False):
            with smtplib.SMTP(config["host"], config["port"]) as server:
                server.starttls(context=context)
                server.login(config["user"], config["pass"])
                server.sendmail(config["user"], to_email, message.as_string())
        else:
            # Use implicit TLS
            with smtplib.SMTP_SSL(config["host"], config["port"], context=context) as server:
                server.login(config["user"], config["pass"])
                server.sendmail(config["user"], to_email, message.as_string())
        
        print(f"Email sent successfully to {to_email} from {config['user']}")
        return True

    except Exception as e:
        # In a real app, you'd want more robust logging here
        print(f"Error sending email: {e}")
        return False
