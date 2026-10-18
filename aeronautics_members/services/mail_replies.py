"""Replies by email to answers written in Admin › Messages, brought back into
their thread (docs/microsoft-mail-plan.md, "Replies").

Only for the account chosen for replies, and only when it is a Microsoft 365
mailbox: the portal may read it through Graph. An answer goes out with a
Message-ID the portal keeps (``ContactReply.email_message_id``); an email
that names one of those in ``In-Reply-To`` or ``References`` is a reply to
it, whoever it is from. Its new part is kept as an incoming reply and the
message is open again. Anything else in the mailbox is left alone.

Graph says at once when something arrives (a subscription to the inbox,
renewed before it runs out); the ``notifications`` job (every 15 minutes)
renews it and looks through what arrived since its last look, for a
notification that was missed.
"""

import re
import secrets
import threading
from datetime import timedelta
from urllib.parse import quote

from sqlalchemy.exc import IntegrityError

from ..db_models import ContactReply, MailAccount, db
from .audit import log_audit_event
from .clock import get_now_utc
from .microsoft_mail import (
    MicrosoftError,
    as_utc,
    graph,
    mailbox_path,
    microsoft_accounts,
    ready,
    utc_from_graph,
)

#: Where Graph's notifications arrive (blueprints/microsoft_mail.py).
NOTIFY_PATH = "/webhooks/microsoft-mail"
#: How long a subscription is asked for; Graph allows a little under 7 days for mail.
SUBSCRIPTION_LASTS = timedelta(days=6)
RENEW_BEFORE = timedelta(days=2)
#: The safety net looks a little further back than its last look.
OVERLAP = timedelta(hours=1)
FIRST_LOOK = timedelta(days=2)
#: An incoming reply is kept up to this long; the whole of it stays in the mailbox.
MOST_TEXT = 20000

_IDS = re.compile(r"<[^<>\s]+>")


def watched_account():
    """The account chosen for replies, when it is a Microsoft one and the connection is set up."""
    from .mail_senders import account_for

    key = account_for("replies")
    if not key or not ready():
        return None
    account = db.session.execute(db.select(MailAccount).filter_by(account_key=key)).scalar_one_or_none()
    return account if account is not None and account.is_microsoft and account.from_email else None


def _notification_url():
    from flask import current_app

    base = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}{NOTIFY_PATH}" if base.startswith("https://") else None


def _inbox_resource(account):
    return f"users/{account.from_email}/mailFolders('inbox')/messages"


def unsubscribe(account):
    """End Graph's subscription to the account's inbox. Gone already, or
    Microsoft unreachable: it runs out by itself."""
    if account.inbox_subscription_id:
        try:
            graph("DELETE", f"/subscriptions/{quote(account.inbox_subscription_id, safe='')}")
        except MicrosoftError:
            pass
    account.inbox_subscription_id = account.inbox_client_state = None
    account.inbox_subscription_expires_at = None


def _subscribe(account, now):
    url = _notification_url()
    if url is None:
        return "Replies are looked for every 15 minutes: the portal has no https address Microsoft could notify."
    state = secrets.token_urlsafe(32)
    expires = now + SUBSCRIPTION_LASTS
    _status, made = graph("POST", "/subscriptions", body={
        "changeType": "created", "notificationUrl": url, "resource": _inbox_resource(account),
        "expirationDateTime": f"{expires:%Y-%m-%dT%H:%M:%S}Z", "clientState": state,
    })
    account.inbox_subscription_id = made.get("id")
    account.inbox_client_state = state
    account.inbox_subscription_expires_at = utc_from_graph(made.get("expirationDateTime")) or expires
    return None


def keep_watching(now=None):
    """Subscribed to the replies' inbox, and to no other: made, renewed or
    ended as needed. A note when replies come only by the 15-minute look."""
    now = (now or get_now_utc()).replace(tzinfo=None)
    watched = watched_account()
    for account in microsoft_accounts():
        if account.inbox_subscription_id and account is not watched:
            unsubscribe(account)
    if watched is None:
        return None
    expires = watched.inbox_subscription_expires_at
    if watched.inbox_subscription_id and expires and expires - now > RENEW_BEFORE:
        return None
    try:
        if watched.inbox_subscription_id:
            try:
                new_expiry = now + SUBSCRIPTION_LASTS
                _status, renewed = graph("PATCH", f"/subscriptions/{quote(watched.inbox_subscription_id, safe='')}",
                                         body={"expirationDateTime": f"{new_expiry:%Y-%m-%dT%H:%M:%S}Z"})
                watched.inbox_subscription_expires_at = (utc_from_graph(renewed.get("expirationDateTime"))
                                                         or new_expiry)
                return None
            except MicrosoftError as exc:
                if exc.status != 404:
                    raise
                unsubscribe(watched)
        return _subscribe(watched, now)
    except MicrosoftError as exc:
        return f"Microsoft did not let the portal watch the mailbox: {exc}"


def _headers(item):
    return {(header.get("name") or "").lower(): header.get("value") or ""
            for header in item.get("internetMessageHeaders") or []}


def _thread_of(item):
    """The message an email answers, by the IDs it names; None if it is not a reply to one."""
    headers = _headers(item)
    named = _IDS.findall(f"{headers.get('in-reply-to', '')} {headers.get('references', '')}")
    if not named:
        return None
    found = db.session.execute(
        db.select(ContactReply).where(ContactReply.email_message_id.in_(named))
        .order_by(ContactReply.id.desc())).scalars().first()
    return found.message if found is not None else None


def _text_of(account, graph_id):
    _status, full = graph("GET", f"{mailbox_path(account.from_email)}/messages/{quote(graph_id, safe='')}"
                          "?$select=uniqueBody,body", headers={"Prefer": 'outlook.body-content-type="text"'})
    for part in ("uniqueBody", "body"):
        text = ((full.get(part) or {}).get("content") or "").replace("\r\n", "\n").strip()
        if text:
            return text[:MOST_TEXT]
    return "(No text.)"


def _from_the_portal(item):
    """Sent from one of the portal's own addresses: its answer's copy in the
    mailbox (whatever Message-ID Exchange gave it), or an admin answering in
    Outlook -- never the sender's reply."""
    sender = (((item.get("from") or {}).get("emailAddress") or {}).get("address") or "").strip().lower()
    if not sender:
        return False
    own = db.session.execute(db.select(MailAccount.from_email).where(MailAccount.from_email.isnot(None))).scalars()
    return sender in {address.strip().lower() for address in own}


def _take(account, item):
    """One email from the mailbox: kept as an incoming reply when it answers
    an answer, and not taken already. The reply, or None."""
    internet_id = (item.get("internetMessageId") or "").strip()[:255]
    if not internet_id or _from_the_portal(item) or db.session.execute(
            db.select(ContactReply.id).filter_by(email_message_id=internet_id)).first():
        # Taken already -- or one of the portal's own answers, in the mailbox
        # as its copy.
        return None
    message = _thread_of(item)
    if message is None:
        return None
    arrived = utc_from_graph(item.get("receivedDateTime")) or get_now_utc().replace(tzinfo=None)
    body = _text_of(account, item["id"])
    try:
        # The notification and the 15-minute look can take the same email at
        # the same moment; its Message-ID is unique, so the second one stops.
        with db.session.begin_nested():
            reply = ContactReply(message=message, incoming=True, body=body, account_key=account.account_key,
                                 created_at=arrived, sent_at=arrived, email_message_id=internet_id,
                                 has_attachments=bool(item.get("hasAttachments")))
            db.session.add(reply)
            db.session.flush()
    except IntegrityError:
        return None
    message.done_at = message.done_by_user_id = None
    log_audit_event(category="messages", event_type="message_reply_received",
                    metadata={"message_id": message.id, "reply_id": reply.id, "account": account.account_key})
    return reply


_FIELDS = "id,internetMessageId,internetMessageHeaders,receivedDateTime,hasAttachments,from"


def take_notified(account_id, graph_id):
    """What a notification named: read and, when it is a reply, kept."""
    account = db.session.get(MailAccount, account_id)
    if account is None or not account.is_microsoft:
        return None
    _status, item = graph("GET", f"{mailbox_path(account.from_email)}/messages/{quote(graph_id, safe='')}"
                          f"?$select={_FIELDS}")
    return _take(account, item)


def notified(payload):
    """Graph's notifications, checked: ``[(account id, message id)]`` for those
    with a subscription and secret of an account here; the rest are ignored."""
    accounts = {account.inbox_subscription_id: account for account in microsoft_accounts()
                if account.inbox_subscription_id}
    found = []
    for item in (payload or {}).get("value") or []:
        account = accounts.get(item.get("subscriptionId"))
        if account is None or not secrets.compare_digest(str(item.get("clientState") or ""),
                                                         account.inbox_client_state or ""):
            continue
        graph_id = ((item.get("resourceData") or {}).get("id") or "").strip()
        if graph_id:
            found.append((account.id, graph_id))
    return found


def look_through(account, now=None):
    """The safety net: what arrived since the last look (an hour more, to be
    sure), each taken if it is a reply. How many were."""
    now = (now or get_now_utc()).replace(tzinfo=None)
    since = (account.inbox_checked_at - OVERLAP) if account.inbox_checked_at else now - FIRST_LOOK
    path = (f"{mailbox_path(account.from_email)}/mailFolders/inbox/messages?$select={_FIELDS}"
            f"&$filter=receivedDateTime ge {as_utc(since):%Y-%m-%dT%H:%M:%SZ}&$orderby=receivedDateTime&$top=50")
    taken = 0
    while path:
        _status, page = graph("GET", path.replace(" ", "%20"))
        for item in page.get("value") or []:
            taken += _take(account, item) is not None
        following = page.get("@odata.nextLink") or ""
        path = following.split("/v1.0", 1)[1] if "/v1.0" in following else None
    account.inbox_checked_at = now
    return taken


def run(now=None):
    """The notifications job's step: keep the subscription, look through the
    inbox. How many replies were taken."""
    keep_watching(now)
    db.session.commit()
    watched = watched_account()
    if watched is None:
        return 0
    taken = look_through(watched, now)
    db.session.commit()
    return taken


def state():
    """For System health: ``(ok, message)``, or None when no replies are watched."""
    watched = watched_account()
    if watched is None:
        return None
    if not watched.inbox_subscription_id:
        return False, (f"Replies to {watched.from_email} are looked for every 15 minutes only: Microsoft does "
                       "not notify the portal.")
    return True, f"Replies to {watched.from_email} come into Admin › Messages at once."


# --- At once, beside the request ----------------------------------------------------------


def start_now(app, work, *args):
    """Run ``work(*args)`` in a thread of its own with the app, and commit:
    Graph's notification is answered at once, and making a subscription has
    Graph call this same portal back, which must not wait for that request."""

    def run_it():
        with app.app_context():
            try:
                work(*args)
                db.session.commit()
            except Exception:  # noqa: BLE001 -- logged; the 15-minute job tries again
                db.session.rollback()
                app.logger.exception("Microsoft mail: %s failed.", getattr(work, "__name__", work))
            finally:
                db.session.remove()

    threading.Thread(target=run_it, name="microsoft-mail", daemon=True).start()
