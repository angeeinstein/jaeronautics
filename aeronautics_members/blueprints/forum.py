"""The forum: the page (the app's, api/account.py), Discourse's sign-in through
the portal (DiscourseConnect), the sign-out Discourse sends back, and the
profile pictures Discourse fetches.
"""

from flask import Blueprint, current_app

from ..services.clock import (
    get_now_utc,
)
from ..services.forum import (
    log_out_forum_session_if_possible,
    sync_member_forum_state,
)
from ..services.identity import (
    TOKEN_MAX_AGE_FORUM_ENTRY,
    TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN,
    email_verification_claims_match,
    mark_email_verified_from_token,
    read_token,
    send_email_verification_email,
    user_for_email_token,
)
from ..services.membership import (
    member_has_active_access,
)
from ..services.workflows import finish_forum_cleanup_for
from ._email_cooldown import remember_sent, sent_just_now
from flask import (
    abort,
    flash,
    redirect,
    request,
    send_file,
    session,
    url_for,
)
from flask_babel import (
    _,
)
from flask_login import (
    current_user,
    login_user,
    logout_user,
)
from itsdangerous import (
    BadSignature,
    SignatureExpired,
)
from pathlib import (
    Path,
)
from ..db_models import (
    ForumAvatarSubmission,
    ImportedForumProfile,
    db,
)
from ..forum_service import (
    ForumProviderError,
)
from ..app import (
    build_forum_context,
    get_current_member_for_user,
    get_member_portal_target,
    limiter,
)
from ..config import RATELIMIT_FORUM_CONNECT
from .app_shell import app_shell

forum_bp = Blueprint("forum", __name__)


@forum_bp.route("/forum", methods=["GET"])
def forum_entry():
    token = (request.args.get("token") or "").strip()
    token_user = None
    token_verified_email = False
    if token:
        try:
            token_data = read_token(token, "forum-entry", TOKEN_MAX_AGE_FORUM_ENTRY)
            # Follows the link across a reconnect of an old forum account,
            # which retires the row the welcome mail was addressed to.
            token_user = user_for_email_token(token_data)
        except (BadSignature, SignatureExpired, ValueError, TypeError):
            token_data = None
            token_user = None
            flash(_("This forum access link is invalid or has expired."), "warning")

        # Only treat the link as proof of email ownership when it was issued for
        # the address the account currently holds; otherwise it just signs in.
        if token_user is not None and mark_email_verified_from_token(token_data, token_user):
            db.session.commit()
            token_verified_email = True

        if token_user is not None and current_user.is_authenticated and current_user.id != token_user.id:
            flash(
                _("This forum link belongs to a different account. Please log out and sign in with the account that received the email."),
                "warning",
            )
            return redirect(url_for(get_member_portal_target(current_user)))

        if token_user is not None and not current_user.is_authenticated:
            issued_at_raw = token_data.get("issued_at") if isinstance(token_data, dict) else None
            auto_login_allowed = False
            try:
                issued_at = int(issued_at_raw) if issued_at_raw is not None else None
                if issued_at is not None:
                    age_seconds = int(get_now_utc().timestamp()) - issued_at
                    auto_login_allowed = 0 <= age_seconds <= TOKEN_MAX_AGE_FORUM_ENTRY_AUTO_LOGIN
            except (TypeError, ValueError):
                auto_login_allowed = False

            # Signing somebody in on a link alone is only fair while the link
            # still speaks for the account: sent to the address it holds now,
            # with the current nonce. After an email change, a link lying in
            # the old mailbox must not open the account any more.
            if auto_login_allowed and not email_verification_claims_match(token_data, token_user):
                auto_login_allowed = False

            if auto_login_allowed:
                login_user(token_user)
            else:
                flash(
                    _("Your email address has been verified. Please log in to continue to the forum.") if token_verified_email else _("Please log in to continue to the forum."),
                    "success" if token_verified_email else "warning",
                )
                return redirect(url_for("auth.login", next=url_for("forum.forum_entry"), forum_login_source="welcome_email"))

        if token_user is not None and token_verified_email:
            flash(_("Your email address has been verified."), "success")

    if not current_user.is_authenticated:
        flash(_("Please log in to continue to the forum."), "warning")
        return redirect(url_for("auth.login", next=url_for("forum.forum_entry")))

    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("You need a membership for the forum."), "warning")
        return redirect(url_for("account.create_membership_profile"))

    forum_result, service = sync_member_forum_state(member)
    if forum_result and forum_result.changed:
        db.session.commit()

    forum_context = build_forum_context(member)
    if forum_context["can_enter_forum"]:
        # The forum will only send them back to be refused; say why here.
        if not current_user.email_is_verified:
            return _confirm_email_first()
        try:
            return redirect(
                service.build_forum_redirect(destination_path=service.settings.get("forum_onboarding_path")),
                code=303,
            )
        except ForumProviderError as exc:
            current_app.logger.warning("Could not hand off to Discourse for member_id=%s: %s", member.id, exc)
            flash(_("The forum could not be opened right now. Please try again later."), "danger")

    # Where things stand, and the picture: the app's page (api/account.py).
    return app_shell()


@forum_bp.route("/forum/avatar/public/<token>", methods=["GET"])
def forum_avatar_public_file(token):
    submission = db.session.execute(
        db.select(ForumAvatarSubmission).where(ForumAvatarSubmission.public_token == token)
    ).scalar_one_or_none()
    if submission is None or not submission.storage_path:
        abort(404)

    storage_path = Path(submission.storage_path)
    if not storage_path.exists():
        abort(404)

    return send_file(storage_path, mimetype=submission.content_type or "application/octet-stream", conditional=True)


@forum_bp.route("/forum/avatar/imported/<token>", methods=["GET"])
def forum_imported_avatar_public_file(token):
    """Serves an imported person's old avatar to the forum.

    Public because Discourse fetches the image itself, from its own server and
    without any of our cookies, so the admin-only route these files are
    otherwise served through would hand it a login page.

    Guarded by an unguessable token rather than by the filename: the staging
    directory also holds avatars waiting for admin review, and a public route
    that took a path would make those reachable by guessing. A token is minted
    only for profiles actually being published.
    """
    profile = db.session.execute(
        db.select(ImportedForumProfile).where(
            ImportedForumProfile.avatar_public_token == token
        )
    ).scalar_one_or_none()
    if profile is None or not profile.avatar_path or profile.user.deleted_at is not None:
        abort(404)

    path = Path(profile.avatar_path)
    if not path.exists():
        abort(404)
    return send_file(path, conditional=True)


@forum_bp.route("/forum/discourse/connect", methods=["GET"])
@limiter.limit(RATELIMIT_FORUM_CONNECT)
def forum_discourse_connect():
    if not current_user.is_authenticated:
        next_url = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(url_for("auth.login", next=next_url))

    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("You need a membership for the forum."), "warning")
        return redirect(url_for("account.create_membership_profile"))

    if not member_has_active_access(member):
        flash(_("Forum access needs an active membership."), "warning")
        return redirect(url_for("forum.forum_entry"))

    # DiscourseConnect asserts this address to the forum, which associates forum
    # accounts by email. Never vouch for an address we have not verified.
    if not current_user.email_is_verified:
        return _confirm_email_first()

    # A reconnect leaves the old forum account holding this address until it
    # is dealt with, and Discourse refuses the sign-in until then.
    if not finish_forum_cleanup_for(current_user):
        flash(
            _("Your forum account is still being set up. Please try again in a minute."),
            "info",
        )
        return redirect(url_for("account.account"))

    forum_result, service = sync_member_forum_state(member)
    if forum_result and forum_result.changed:
        db.session.commit()

    try:
        redirect_url = service.handle_provider_request(request.args, current_user, member)
        return redirect(redirect_url, code=303)
    except ForumProviderError as exc:
        current_app.logger.warning("DiscourseConnect handoff failed for member_id=%s: %s", member.id, exc)
        flash(_("The forum sign-in could not be completed right now. Please try again later."), "danger")
        # Not back to /forum: that forwards to the forum, which sends them
        # straight back here -- round and round until the browser gives up.
        return redirect(url_for("account.account"))


def _confirm_email_first():
    """Stop, send a fresh confirmation link, and say so -- on the account page.

    Somebody can pay, upload a photo and have it approved without ever opening
    the first confirmation mail, so this is an ordinary way to arrive here. It
    used to send them to /forum, which forwards to the forum, which sends them
    back here: a loop the browser ended with "too many redirects". And it said
    a link had been sent when none had.
    """
    if sent_just_now("verify-email", current_user.email):
        flash(_("Please confirm your email address first. We sent you a link a moment ago."), "warning")
        return redirect(url_for("account.account"))
    try:
        sent = send_email_verification_email(current_app._get_current_object(), current_user)
        db.session.commit()
        if sent:
            remember_sent("verify-email", current_user.email)
    except Exception as exc:  # noqa: BLE001 -- the reason belongs in the log
        db.session.rollback()
        sent = False
        current_app.logger.warning(
            "Could not send a confirmation link before forum sign-in for user_id=%s: %s",
            current_user.id, exc,
        )
    if sent:
        flash(
            _("Please confirm your email address first. We have just sent a new link to %(address)s.",
              address=current_user.email),
            "warning",
        )
    else:
        flash(_("Please confirm your email address first."), "warning")
    return redirect(url_for("account.account"))


@forum_bp.route("/forum/logout", methods=["GET"])
def forum_logout():
    forum_logout_error = None
    forum_logout_attempted = False
    if current_user.is_authenticated:
        user = current_user._get_current_object()
        forum_logout_attempted, forum_logout_error = log_out_forum_session_if_possible(user)
        logout_user()
        session.pop("login_next", None)
        session.pop("login_source", None)
        if forum_logout_error:
            flash(_("You have been logged out here, but the forum session could not be ended automatically."), "warning")
        elif forum_logout_attempted:
            flash(_("You have been logged out from the forum and this website. Sign in again if you want to continue with a different account."), "info")
        else:
            flash(_("You have been logged out. Sign in again if you want to continue."), "info")
    else:
        flash(_("You have been logged out from the forum. Sign in again if you want to continue."), "info")
    return redirect(url_for("auth.login", next=url_for("forum.forum_entry")))
