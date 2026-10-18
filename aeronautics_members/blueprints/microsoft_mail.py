"""Microsoft Graph's notifications: something arrived in the inbox of the
mail account chosen for replies (services/mail_replies.py,
docs/microsoft-mail-plan.md). Exempt from CSRF (create_app); a notification
counts only with the subscription's own secret, which only Microsoft and this
portal know.

Answered at once, as Graph asks (within a few seconds, or it gives up on the
portal); the message is read in a thread of its own.
"""

from flask import Blueprint, current_app, request

from ..services import mail_replies

microsoft_mail_bp = Blueprint("microsoft_mail", __name__)


@microsoft_mail_bp.route(mail_replies.NOTIFY_PATH, methods=["POST"])
def notification():
    # Making the subscription: Graph checks the address answers with its token.
    token = request.args.get("validationToken")
    if token is not None:
        return current_app.response_class(token[:1024], mimetype="text/plain")
    found = mail_replies.notified(request.get_json(silent=True))
    app = current_app._get_current_object()
    for account_id, graph_id in found:
        mail_replies.start_now(app, mail_replies.take_notified, account_id, graph_id)
    return "", 202
