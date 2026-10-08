"""My Account: one answer for the whole page -- both email addresses and
whether they are confirmed, the membership and what to do about it, the
forum, one's teams, the contact details, the name and kind of membership
with a change request waiting, and what deleting would cost -- worked out
here, so the page only draws it. Then one endpoint per thing done there.

The changes live in services/account.py; the rules of the forms in
forms.py, checked through api/_forms.py. Drawn by frontend/src/pages/account/.
"""

from datetime import date
from typing import Literal

from flask import current_app, flash, request, url_for
from flask_login import current_user, logout_user
from pydantic import Field

from ..config import RATELIMIT_ACCOUNT_DELETION, RATELIMIT_EMAIL_RESEND, RATELIMIT_PASSWORD_CHANGE
from ..forms import IdentityChangeRequestForm, MemberProfileForm
from ..member_categories import category_label
from ..services import ConflictError, ValidationError
from ..services import account as account_service
from ..services import teams as teams_service
from ..services import workflows
from ..services.billing import (
    LIVE_SUBSCRIPTION_STATUSES,
    can_rejoin,
    can_resume_payment,
    checkout_completed_but_not_yet_confirmed,
)
from ..services.forum import generate_unique_forum_username
from ..services.membership import (
    format_membership_date_display,
    member_has_active_access,
    sync_member_active_state,
)
from ..services.signup import invoice_payments_allowed
from ..app import format_bytes_human, limiter
from ..blueprints._email_cooldown import remember_sent, sent_just_now
from ..db_models import db
from ._core import Model, endpoint
from ._forms import checked

TAG = "Account"


class NoteOut(Model):
    tone: Literal["plain", "info", "success", "warning", "danger"]
    text: str


class EmailAddressOut(Model):
    address: str
    confirmed: bool


class MembershipCardOut(Model):
    #: The stored payment state: paid, free_period, processing, pending_checkout, failed, unpaid,
    #: cancel_scheduled, canceled.
    status: str
    status_label: str
    tone: Literal["active", "pending", "failed", "neutral"]
    active: bool
    starts_on: date | None
    ends_on: date | None
    #: None when it does not renew.
    renews_on: date | None
    auto_renew: bool
    #: Paid, and Stripe's confirmation not here yet: ask again in a moment.
    activating: bool
    note: NoteOut | None
    may_manage_billing: bool
    may_resume_payment: bool
    may_rejoin: bool
    #: Whether rejoining may be paid by invoice too.
    invoice_payments: bool


class PictureOut(Model):
    """The profile picture: what may be uploaded, and the one waiting for review."""

    #: Whether one may be uploaded now.
    upload: bool
    #: A new picture in place of an approved one, allowed by an admin.
    replacing: bool
    #: The picture waiting for review.
    pending_url: str | None
    #: Why the last one was turned down.
    rejected_reason: str | None
    #: "JPG, PNG, WebP, AVIF".
    formats: str
    max_bytes: int
    max_label: str


class ForumCardOut(Model):
    #: active, needs_avatar, pending_avatar, rejected_avatar, reconnect_waiting, payment_processing,
    #: inactive_membership, account_disabled, disabled.
    status: str
    message: str
    #: None while the old account is waiting to be reconnected, or none is set.
    username: str | None
    reconnect_waiting: bool
    may_open: bool
    open_url: str
    #: The last update of the forum account failed; it is tried again.
    problem: bool
    picture: PictureOut | None


class AccountTeamOut(Model):
    slug: str
    name: str
    logo_url: str | None
    status_label: str | None
    #: Its members see its own page; anybody else what it is about.
    opens: Literal["team", "about"]


class TeamsCardOut(Model):
    plural: str
    mine: list[AccountTeamOut]
    #: Not in or applying to any, and may join one: say what teams are.
    invite: bool


class ContactOut(Model):
    street: str | None
    house_number: str | None
    postal_code: str | None
    city: str | None
    country: str | None
    phone_private: str | None
    email_private: str | None
    phone_work: str | None
    email_work: str | None
    #: The company a partner member joins for.
    company_name: str | None


class IdentityOut(Model):
    salutation: str | None
    title: str | None
    first_name: str | None
    last_name: str | None
    member_category: str | None
    member_category_label: str
    year_group: str | None


class ChangeRequestOut(Model):
    id: int
    salutation: str | None
    title: str | None
    first_name: str | None
    last_name: str | None
    member_category: str | None
    member_category_label: str
    year_group: str | None
    note: str | None
    #: The forum username the new name would give, when it differs.
    username_could_become: str | None


class MemberAccountOut(Model):
    work_email: EmailAddressOut | None
    membership: MembershipCardOut
    forum: ForumCardOut
    #: None while teams are switched off.
    teams: TeamsCardOut | None
    contact: ContactOut
    identity: IdentityOut
    change_request: ChangeRequestOut | None
    #: What deleting would cost, said before the button.
    deletion_note: NoteOut | None
    #: Cancelling, on Stripe's billing page, would keep the time paid for.
    may_cancel_instead: bool


class MyAccountOut(Model):
    email: EmailAddressOut
    #: What is to be done first: the addresses waiting to be confirmed.
    to_confirm: NoteOut | None
    #: None for an account without a membership, such as one used only to run the portal.
    member: MemberAccountOut | None
    #: Everything held about this person, as a JSON file.
    export_url: str


# --- Reading -----------------------------------------------------------------------------

STATUS_LABELS = {
    "paid": ("Paid", "active"),
    "free_period": ("Free until year end", "active"),
    "cancel_scheduled": ("Renewal cancelled", "pending"),
    "canceled": ("Ended", "neutral"),
    "pending_checkout": ("Payment not finished", "pending"),
    "processing": ("Payment processing", "pending"),
    "failed": ("Payment failed", "failed"),
    "unpaid": ("Unpaid", "failed"),
}


def _day(value):
    return format_membership_date_display(value)


def _refresh(member):
    """The membership as Stripe has it now: the page used to be the moment
    a returning member's state caught up."""
    subscription = None
    if member.stripe_customer_id or member.stripe_subscription_id:
        try:
            changed, subscription, _forum = workflows.refresh_member_billing_state(
                member, force_stripe_sync=True, sync_forum=False)
            if changed:
                db.session.commit()
        except Exception as exc:  # noqa: BLE001 -- Stripe unreachable: show what we have
            db.session.rollback()
            current_app.logger.warning("Could not refresh Stripe billing state for member_id=%s: %s", member.id, exc)
    elif sync_member_active_state(member):
        db.session.commit()
    return subscription


def _membership(member, subscription):
    activating = checkout_completed_but_not_yet_confirmed(member)
    active = member_has_active_access(member)
    # A failed payment on a subscription Stripe still runs -- a renewal debit
    # that bounced: a new card or account under Manage billing helps, and
    # "Rejoin" would only be refused.
    needs_attention = (member.payment_status == "failed"
                       and (subscription or {}).get("status") in LIVE_SUBSCRIPTION_STATUSES)
    may_rejoin = can_rejoin(member) and not needs_attention
    may_resume = can_resume_payment(member) and not activating
    label, tone = STATUS_LABELS.get(member.payment_status,
                                    ((member.payment_status or "").replace("_", " ").capitalize(), "neutral"))
    if activating:
        label, tone = "Payment received", "pending"

    note = None
    if member.cancel_at_period_end:
        note = NoteOut(tone="warning", text=(
            f"Your membership stays active until {_day(member.membership_ends_on)}, but it will not renew."
            if member.membership_ends_on else "Your membership stays active, but it will not renew."))
    elif needs_attention:
        note = NoteOut(tone="warning", text="Your last payment failed. Please update your payment method under "
                                            "Manage billing; Stripe will then try again.")
    elif may_rejoin:
        note = NoteOut(tone="plain", text="Your membership has ended. You can rejoin at any time.")
    elif member.payment_status == "processing" and not active:
        note = NoteOut(tone="info", text="Your payment is being processed. SEPA Direct Debit takes a few days; "
                                         "we will email you.")
    elif activating:
        note = NoteOut(tone="info", text="Payment received. Your membership is being activated, which usually "
                                         "takes a few seconds.")
    elif may_resume:
        note = NoteOut(tone="info", text="Your account is ready, but the membership payment is not finished yet.")

    return MembershipCardOut(
        status=member.payment_status or "", status_label=label, tone=tone, active=active,
        starts_on=member.membership_starts_on, ends_on=member.membership_ends_on,
        renews_on=None if member.cancel_at_period_end else member.renewal_due_on,
        auto_renew=not member.cancel_at_period_end, activating=activating, note=note,
        may_manage_billing=bool(member.stripe_customer_id), may_resume_payment=may_resume, may_rejoin=may_rejoin,
        invoice_payments=invoice_payments_allowed(),
    )


def forum_card(member):
    """The forum: where things stand, and the picture. Also for the forum's own page."""
    from ..app import build_forum_context

    context = build_forum_context(member)
    status = context["status_key"]
    latest = context["latest_submission"]
    rejected = latest.review_note if latest is not None and latest.status == "rejected" else None
    pending = context["pending_submission"]
    asks_picture = status in ("needs_avatar", "pending_avatar", "rejected_avatar")
    replacing = status == "active" and context["replacement_allowed"]
    picture = None
    if asks_picture or replacing:
        picture = PictureOut(
            upload=context["can_upload_avatar"], replacing=replacing,
            pending_url=(url_for("forum.forum_avatar_public_file", token=pending.public_token)
                         if pending is not None and pending.public_token else None),
            rejected_reason=rejected if status == "rejected_avatar" or replacing else None,
            formats=context["avatar_input_formats"], max_bytes=context["avatar_upload_request_limit"],
            max_label=context["avatar_upload_request_limit_display"],
        )
    return ForumCardOut(
        status=status, message=str(context["status_message"]),
        username=None if context["reconnect_waiting"] else current_user.forum_username,
        reconnect_waiting=context["reconnect_waiting"], may_open=context["can_enter_forum"],
        open_url=context["entry_url"], problem=bool(context["forum_error"]), picture=picture,
    )


def _teams(member):
    if not teams_service.teams_enabled():
        return None
    from .teams import _card, _latest

    latest = _latest()
    is_member = teams_service.is_active_association_member(current_user)
    led = {team.id for team in teams_service.teams_led_by(current_user)}
    mine = []
    for team in teams_service.all_teams(include_archived=False):
        card = _card(team, latest, False, is_member)
        if card.membership.ongoing or team.id in led:
            mine.append(AccountTeamOut(slug=team.slug, name=team.name, logo_url=card.logo_url,
                                       status_label=card.membership.status_label, opens=card.opens))
    _singular, plural = teams_service.team_labels()
    return TeamsCardOut(plural=plural, mine=mine, invite=teams_service.invite_to_teams(member.user))


def _change_request(member):
    record = member.open_identity_change_request
    if record is None:
        return None
    could_become = generate_unique_forum_username(
        record.requested_first_name, record.requested_last_name, record.requested_year_group,
        exclude_user_id=current_user.id)
    return ChangeRequestOut(
        id=record.id, salutation=record.requested_salutation, title=record.requested_title,
        first_name=record.requested_first_name, last_name=record.requested_last_name,
        member_category=record.requested_member_category,
        member_category_label=str(category_label(record.requested_member_category)),
        year_group=record.requested_year_group, note=record.member_note,
        username_could_become=(could_become if current_user.forum_username
                               and could_become != current_user.forum_username else None),
    )


def _deletion(member):
    """Leaving and deleting are two decisions, and in the wrong order deleting
    costs the rest of a year already paid for: said before the button."""
    if member.stripe_subscription_id and not member.cancel_at_period_end:
        paid = (f" You have already paid until {_day(member.membership_ends_on)}."
                if member.membership_ends_on else "")
        return NoteOut(tone="warning", text=(
            f"You have an active membership.{paid} Deleting ends it now, without a refund. Cancel your "
            "membership instead to keep it until it runs out.")), bool(member.stripe_customer_id)
    if member.cancel_at_period_end and member.membership_ends_on:
        return NoteOut(tone="plain", text=(
            f"Your membership is already set to end on {_day(member.membership_ends_on)}. If you delete your "
            "account before then, you lose the remaining time without a refund.")), False
    return None, False


def _to_confirm(member):
    """Only the addresses actually waiting: many give no university address at all."""
    private = not current_user.email_is_verified
    work = member is not None and bool(member.email_work) and not member.email_work_is_verified
    if private and work:
        text = "Both email addresses need confirming. We send a separate email to each."
    elif private:
        text = "Please confirm your email address. We sent you a link."
    elif work:
        text = "Please confirm your university email address. We sent a link to it."
    else:
        return None
    if work:
        text += " This also restores your old forum account, if you had one."
    return NoteOut(tone="warning", text=text)


def _account():
    member = current_user.member
    member_out = None
    if member is not None:
        subscription = _refresh(member)
        deletion_note, cancel_instead = _deletion(member)
        member_out = MemberAccountOut(
            work_email=(EmailAddressOut(address=member.email_work, confirmed=bool(member.email_work_is_verified))
                        if member.email_work else None),
            membership=_membership(member, subscription),
            forum=forum_card(member),
            teams=_teams(member),
            contact=ContactOut(**{name: getattr(member, name) for name in ContactOut.model_fields}),
            identity=IdentityOut(
                salutation=member.salutation, title=member.title, first_name=member.first_name,
                last_name=member.last_name, member_category=member.member_category,
                member_category_label=str(category_label(member.member_category)), year_group=member.year_group),
            change_request=_change_request(member),
            deletion_note=deletion_note,
            may_cancel_instead=cancel_instead,
        )
    return MyAccountOut(
        email=EmailAddressOut(address=current_user.email, confirmed=bool(current_user.email_is_verified)),
        to_confirm=_to_confirm(member),
        member=member_out,
        export_url=url_for("account.export_my_data"),
    )


@endpoint("GET", "/account", response=MyAccountOut, tag=TAG)
def account():
    """My Account: everything on the page, with what to do next."""
    return _account()


# --- Changing -----------------------------------------------------------------------------


def _member():
    member = current_user.member
    if member is None:
        raise ConflictError("This account has no membership.", code="no_membership")
    return member


class MessageOut(Model):
    tone: Literal["info", "success", "warning"]
    text: str


class AccountSavedOut(Model):
    messages: list[MessageOut]
    account: MyAccountOut


def _saved(*messages):
    return AccountSavedOut(messages=[MessageOut(tone=tone, text=text) for tone, text in messages],
                           account=_account())


class ContactIn(Model):
    street: str = Field(max_length=255)
    house_number: str = Field(max_length=20)
    postal_code: str = Field(max_length=20)
    city: str = Field(max_length=100)
    country: str = Field(max_length=100)
    phone_private: str = Field(max_length=50)
    email_private: str = Field(max_length=255)
    phone_work: str | None = Field(None, max_length=50)
    email_work: str | None = Field(None, max_length=255)
    #: Left out, the one saved stays.
    company_name: str | None = Field(None, max_length=255)


@endpoint("PUT", "/account/contact", response=AccountSavedOut, body=ContactIn, tag=TAG)
def account_contact(body):
    """Address, phones and both email addresses: saved at once. A new address waits to be confirmed."""
    member = _member()
    values = checked(MemberProfileForm, body.model_dump(), member_category_value=member.member_category)
    if "company_name" not in body.model_fields_set:
        values["company_name"] = member.company_name
    return _saved(*account_service.save_contact_details(current_user, member, values))


class ChangeRequestIn(Model):
    salutation: str = Field(max_length=20)
    title: str | None = Field(None, max_length=50)
    first_name: str = Field(max_length=100)
    last_name: str = Field(max_length=100)
    member_category: str = Field(max_length=20)
    year_group: str | None = Field(None, max_length=50)
    note: str | None = Field(None, max_length=1000)


@endpoint("POST", "/account/change-request", response=AccountSavedOut, body=ChangeRequestIn, status=201, tag=TAG)
def account_change_request(body):
    """Ask for a new name, salutation, kind of membership or year group; an admin decides."""
    member = _member()
    data = body.model_dump(exclude={"note"})
    values = checked(IdentityChangeRequestForm, {**data, "member_note": body.note})
    values.pop("member_note", None)
    account_service.request_identity_change(current_user, member, values, body.note)
    return _saved(("success", "Sent. An admin will look at it."))


@endpoint("DELETE", "/account/change-request/<int:request_id>", response=AccountSavedOut, tag=TAG)
def account_change_request_cancel(request_id):
    """Take back a change request not decided yet."""
    account_service.cancel_identity_change(current_user, _member(), request_id)
    return _saved(("success", "Request withdrawn."))


def _send_confirmation(kind, address, send):
    # A double click, or an impatient second try: the first email is on its way.
    if address and sent_just_now(kind, address):
        return MessageOut(tone="info", text="We sent it a moment ago. Please check your inbox and your spam folder.")
    send()
    remember_sent(kind, address)
    return MessageOut(tone="success", text=f"We sent a new link to {address}. Not in your inbox? Please check "
                                           "your spam folder.")


@endpoint("POST", "/account/emails/private/confirmation", response=MessageOut, tag=TAG)
@limiter.limit(RATELIMIT_EMAIL_RESEND)
def account_private_email_confirmation():
    """Send the link confirming the private address -- the login -- again."""
    return _send_confirmation("verify-email", current_user.email,
                              lambda: account_service.send_private_confirmation(current_user))


@endpoint("POST", "/account/emails/work/confirmation", response=MessageOut, tag=TAG)
@limiter.limit(RATELIMIT_EMAIL_RESEND)
def account_work_email_confirmation():
    """Send the link confirming the university or company address again."""
    member = _member()
    return _send_confirmation("verify-work-email", member.email_work,
                              lambda: account_service.send_work_confirmation(member))


class GoToOut(Model):
    #: Where the browser goes next: Stripe's page, or the thank-you page.
    url: str


@endpoint("POST", "/account/billing", response=GoToOut, tag=TAG)
def account_billing():
    """Stripe's billing page: payment method, invoices, cancelling. It comes back here."""
    return GoToOut(url=account_service.billing_url(_member()))


@endpoint("POST", "/account/payment", response=GoToOut, tag=TAG)
def account_payment():
    """Stripe's payment page again, for a payment not finished."""
    return GoToOut(url=account_service.resume_payment_url(_member()))


class RejoinIn(Model):
    payment_method: Literal["checkout", "invoice"] = "checkout"


class RejoinOut(Model):
    #: Where to pay; None when Stripe still runs the membership after all.
    url: str | None
    message: str | None


@endpoint("POST", "/account/rejoin", response=RejoinOut, body=RejoinIn, tag=TAG)
def account_rejoin(body):
    """Start the membership again after it ended: a new subscription, on the same Stripe customer."""
    url, message = account_service.rejoin(current_user, _member(), body.payment_method, sent=remember_sent)
    return RejoinOut(url=url, message=message)


@endpoint("POST", "/account/deletion", response=MessageOut, tag=TAG)
@limiter.limit(RATELIMIT_ACCOUNT_DELETION)
def account_deletion():
    """Email the link that deletes the account; nothing is deleted until it is opened."""
    account_service.request_deletion(current_user)
    return MessageOut(tone="info", text=f"We sent a confirmation link to {current_user.email}. Your account is "
                                        "deleted once you open it. The link is valid for one hour.")



# --- The forum, and its picture -----------------------------------------------------------


@endpoint("GET", "/account/forum", response=ForumCardOut, tag=TAG)
def account_forum():
    """The forum: where things stand, the username, the picture. For the forum's own page."""
    return forum_card(_member())


class CropQuery(Model):
    """The square chosen, as fractions of the picture (lib/crop.ts on the page)."""

    zoom: float = Field(1.0, ge=1.0, le=4.0)
    x: float = Field(0.5, ge=0.0, le=1.0)
    y: float = Field(0.5, ge=0.0, le=1.0)


@endpoint("POST", "/account/picture", response=ForumCardOut, query=CropQuery, uploads={"image": True},
          status=201, tag=TAG)
def account_picture(query, files):
    """Upload a profile picture for the forum, cropped to the square chosen. An admin reviews it."""
    member = _member()
    limit = account_service.forum.get_forum_service().get_upload_request_limit()
    if request.content_length and request.content_length > limit:
        raise ValidationError(f"The picture is too large. Please keep it below {format_bytes_human(limit)}.",
                              details={"fields": {"image": f"Please keep it below {format_bytes_human(limit)}."}})
    account_service.upload_picture(current_user, member, files["image"], zoom=query.zoom, x=query.x, y=query.y)
    return forum_card(member)


# --- Password -------------------------------------------------------------------------------


class PasswordIn(Model):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


@endpoint("PUT", "/account/password", response=MessageOut, body=PasswordIn, tag=TAG)
@limiter.limit(RATELIMIT_PASSWORD_CHANGE)
def account_password(body):
    """Change the password; the current one is asked for first."""
    if len(body.new_password) < 8:
        raise ValidationError("Please check the form.",
                              details={"fields": {"new_password": "At least 8 characters, please."}})
    account_service.change_password(current_user, body.current_password, body.new_password)
    return MessageOut(tone="success", text="Your password has been changed.")


# --- Deleting the account, from the emailed link ---------------------------------------------


class DeletionOut(Model):
    """What deleting does to this account, said before the button."""

    has_forum_account: bool
    has_stripe_customer: bool
    #: Running and not cancelled: deleting ends it now, without a refund.
    subscription_active: bool
    #: Paid until then; the rest is lost.
    paid_until: date | None
    #: Nobody else could run the portal: it cannot be deleted.
    is_last_admin: bool
    export_url: str


def _deletion_out(impact):
    return DeletionOut(
        has_forum_account=impact["has_forum_account"], has_stripe_customer=impact["has_stripe_customer"],
        subscription_active=impact["subscription_active"], paid_until=impact["coverage_end"],
        is_last_admin=impact["is_last_admin"], export_url=url_for("account.export_my_data"),
    )


@endpoint("GET", "/account/deletion/<token>", response=DeletionOut, tag=TAG)
def account_deletion_impact(token):
    """What deleting would do. Opening the link changes nothing: mail clients and scanners open links."""
    return _deletion_out(account_service.deletion_impact(current_user, token))


class DeleteIn(Model):
    #: Sent by the button that says what it does.
    confirm: bool


@endpoint("POST", "/account/deletion/<token>", response=MessageOut, body=DeleteIn, tag=TAG)
@limiter.limit(RATELIMIT_ACCOUNT_DELETION)
def account_delete(token, body):
    """Delete the account now, and sign out. The start page then says it is done."""
    if not body.confirm:
        raise ValidationError("Please confirm.", code="not_confirmed")
    account_service.delete_account(current_user, token)
    text = "Your account and personal data have been deleted. Thank you for having been a member."
    logout_user()
    # Said again on the start page, which is Flask's.
    flash(text, "success")
    return MessageOut(tone="success", text=text)
