"""Account blueprint.

Route handlers moved verbatim out of app.py (dedented; @app.route ->
@account_bp.route; app.logger -> current_app.logger). Helpers are imported
from the app module, which is fully initialized before this is imported.
"""

from flask import Blueprint, current_app

from ..app import (
    render_account_dashboard,
    ADMIN_GENERAL_CHANNEL,
    CreateMembershipProfileForm,
    DIRECT_MEMBER_PROFILE_FIELDS,
    IDENTITY_MEMBER_FIELDS,
    IdentityChangeRequestForm,
    Member,
    MemberProfileChangeRequest,
    MemberProfileForm,
    Setting,
    _,
    apply_member_profile,
    can_resume_payment,
    create_checkout_session_for_member,
    create_identity_change_request,
    create_invoice_membership_for_member,
    current_user,
    datetime,
    db,
    flash,
    flush_marked_notification_channels,
    generate_unique_forum_username,
    get_current_member_for_user,
    get_now_utc,
    get_portal_session,
    has_identity_changes,
    log_audit_event,
    login_required,
    member_has_active_access,
    normalize_optional_member_value,
    queue_curated_admin_notification,
    redirect,
    render_template,
    request,
    send_email_verification_email,
    send_member_welcome_email,
    session,
    snapshot_member_for_audit,
    snapshot_user_for_audit,
    stripe,
    sync_member_forum_state,
    sync_member_primary_email,
    timezone,
    url_for,
)

account_bp = Blueprint("account", __name__)


@account_bp.route("/account", methods=["GET"])
@login_required
def account():
    if not request.args.get("rt"):
        redirect_args = {"rt": str(int(datetime.now(timezone.utc).timestamp() * 1000))}
        if request.args.get("refresh_billing") == "1":
            redirect_args["refresh_billing"] = "1"
        return redirect(url_for("account.account", **redirect_args))
    return render_account_dashboard()


@account_bp.route("/account/create-membership", methods=["GET", "POST"])
@login_required
def create_membership_profile():
    if current_user.member is not None:
        return redirect(url_for("account.account"))

    form = CreateMembershipProfileForm()
    if request.method == "GET":
        form.email_private.data = current_user.email

    if form.validate_on_submit():
        settings = {s.key: s.value for s in Setting.query.all()}
        form_data = form.data
        form_data.pop("csrf_token", None)
        form_data.pop("submit", None)

        payment_method = form_data.pop("payment_method", "checkout")
        if settings.get("invoice_payments_enabled") != "True":
            payment_method = "checkout"

        member_email = (current_user.email or "").strip().lower()
        existing_member = db.session.execute(db.select(Member).filter_by(email_private=member_email)).scalar_one_or_none()
        if existing_member is not None:
            if existing_member.user_id == current_user.id:
                return redirect(url_for("account.account"))
            flash(_("A membership profile with this email address already exists. Please contact the club so we can resolve it."), "warning")
            return redirect(url_for("admin.admin_dashboard" if current_user.has_role("admin") else "public.index"))

        member = Member(
            created_at=get_now_utc(),
            payment_status="pending_checkout",
            is_active=False,
            pending_checkout_started_at=get_now_utc(),
        )
        apply_member_profile(member, {**form_data, "email_private": member_email, "terms_accepted": True})
        member.user = current_user
        current_user.email = member_email
        if not current_user.forum_username:
            current_user.forum_username = generate_unique_forum_username(
                member.first_name,
                member.last_name,
                member.year_group,
                exclude_user_id=current_user.id,
            )

        before_user = snapshot_user_for_audit(current_user)
        db.session.add(member)
        db.session.flush()
        log_audit_event(
            category="membership",
            event_type="linked_membership_created",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before={"user": before_user, "member": None},
            after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member)},
            metadata={"payment_method": payment_method},
        )
        db.session.commit()

        try:
            if not current_user.email_is_verified:
                send_email_verification_email(current_app._get_current_object(), current_user)
        except Exception as email_exc:
            current_app.logger.warning("Could not send verification email for linked membership user_id=%s: %s", current_user.id, email_exc)

        try:
            if payment_method == "checkout":
                session, _cycle = create_checkout_session_for_member(member)
                db.session.commit()
                return redirect(session.url, code=303)

            if payment_method == "invoice":
                _subscription, cycle = create_invoice_membership_for_member(member)
                forum_result = None
                if cycle["free_period"]:
                    forum_result, _forum_service = sync_member_forum_state(member)
                db.session.commit()
                if cycle["free_period"]:
                    send_member_welcome_email(current_app._get_current_object(), member)
                    if forum_result and forum_result.error:
                        current_app.logger.warning("Forum sync reported an issue after invoice activation for member_id=%s: %s", member.id, forum_result.error)
                return redirect(
                    url_for(
                        "public.thank_you",
                        method="invoice",
                        phase=cycle["thank_you_phase"],
                    )
                )
        except stripe.StripeError as exc:
            error_body = getattr(exc, "json_body", {}) or {}
            error_details = error_body.get("error", {}) if isinstance(error_body, dict) else {}
            current_app.logger.error(
                "Stripe Error during linked membership signup: type=%s message=%s user_message=%s code=%s param=%s request_id=%s http_status=%s payment_method=%s email=%s member_id=%s user_id=%s",
                type(exc).__name__,
                str(exc),
                error_details.get("message"),
                error_details.get("code"),
                error_details.get("param"),
                getattr(exc, "request_id", None),
                getattr(exc, "http_status", None),
                payment_method,
                member_email,
                member.id,
                current_user.id,
            )
            flash(_("Your membership profile was created, but payment could not be started. You can resume it from your account page."), "warning")
        except Exception:
            current_app.logger.exception(
                "Unexpected error during linked membership signup for user_id=%s email=%s payment_method=%s member_id=%s",
                current_user.id,
                member_email,
                payment_method,
                member.id,
            )
            flash(_("Your membership profile was created, but billing could not be started right now. You can resume it from your account page."), "warning")

        return redirect(url_for("account.account"))

    return render_template("account/create_membership.html", form=form)


@account_bp.route("/account/profile", methods=["POST"])
@login_required
def save_member_profile():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    profile_form = MemberProfileForm(prefix="profile")
    identity_form = IdentityChangeRequestForm(prefix="identity")
    if profile_form.validate_on_submit():
        before_user = snapshot_user_for_audit(current_user)
        before_member = snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)
        try:
            email_changed = sync_member_primary_email(member, profile_form.email_private.data)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

        for field_name in DIRECT_MEMBER_PROFILE_FIELDS:
            if field_name == "email_private":
                continue
            setattr(member, field_name, normalize_optional_member_value(field_name, getattr(profile_form, field_name).data))

        log_audit_event(
            category="profile",
            event_type="contact_details_updated",
            actor_user=current_user,
            target_user=current_user,
            target_member=member,
            before={"user": before_user, "member": before_member},
            after={"user": snapshot_user_for_audit(current_user), "member": snapshot_member_for_audit(member, fields=DIRECT_MEMBER_PROFILE_FIELDS)},
            metadata={"email_changed": email_changed},
        )
        forum_result = None
        if member.user is not None and (member.user.forum_account is not None or member_has_active_access(member)):
            forum_result, _forum_service = sync_member_forum_state(member)
        db.session.commit()
        if email_changed:
            try:
                send_email_verification_email(current_app._get_current_object(), current_user)
                flash(_("Your profile was updated. Please verify your new email address using the link we sent you."), "success")
            except Exception as exc:
                current_app.logger.warning("Could not send verification email after profile update for user_id=%s: %s", current_user.id, exc)
                flash(_("Your profile was updated."), "success")
        else:
            flash(_("Your profile was updated."), "success")
        if forum_result and forum_result.error:
            flash(_("Your forum profile could not be synchronized right now. Please try again later."), "warning")
        return redirect(url_for("account.account"))

    flash(_("Please correct the profile form and try again."), "danger")
    return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)


@account_bp.route("/account/identity-request", methods=["POST"])
@login_required
def submit_identity_change_request():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    identity_form = IdentityChangeRequestForm(prefix="identity")
    profile_form = MemberProfileForm(prefix="profile")
    if identity_form.validate_on_submit():
        form_data = identity_form.data
        form_data.pop("csrf_token", None)
        form_data.pop("submit", None)

        if not has_identity_changes(member, form_data):
            flash(_("There are no identity changes to request."), "warning")
            return redirect(url_for("account.account"))

        try:
            request_record = create_identity_change_request(member, current_user, form_data)
            db.session.flush()
            log_audit_event(
                category="profile_change_request",
                event_type="identity_request_submitted",
                actor_user=current_user,
                target_user=current_user,
                target_member=member,
                before=snapshot_member_for_audit(member, fields=IDENTITY_MEMBER_FIELDS),
                after={
                    "requested_salutation": request_record.requested_salutation,
                    "requested_title": request_record.requested_title,
                    "requested_first_name": request_record.requested_first_name,
                    "requested_last_name": request_record.requested_last_name,
                    "requested_year_group": request_record.requested_year_group,
                },
                metadata={"request_id": request_record.id, "member_note": request_record.member_note},
            )
            queue_curated_admin_notification(
                ADMIN_GENERAL_CHANNEL,
                "identity_change_request_created",
                _("A new identity change request was submitted by %(email)s.", email=member.email_private),
                payload={
                    "member_email": member.email_private,
                    "request_id": request_record.id,
                    "member_note": request_record.member_note,
                },
                target_user=current_user,
                target_member=member,
                object_type="member_profile_change_request",
                object_id=request_record.id,
            )
            db.session.commit()
            flush_marked_notification_channels()
            flash(_("Your identity change request has been submitted for admin review."), "success")
            return redirect(url_for("account.account"))
        except ValueError as exc:
            flash(str(exc), "warning")
            return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)

    flash(_("Please correct the identity change form and try again."), "danger")
    return render_account_dashboard(profile_form=profile_form, identity_form=identity_form)


@account_bp.route("/account/identity-request/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_identity_change_request(request_id):
    member = get_current_member_for_user(current_user)
    request_record = db.session.get(MemberProfileChangeRequest, request_id)
    if request_record is None or member is None or request_record.member_id != member.id or request_record.status != "pending":
        flash(_("The selected change request could not be canceled."), "warning")
        return redirect(url_for("account.account"))

    request_record.status = "canceled"
    request_record.reviewed_by = current_user
    request_record.reviewed_at = get_now_utc()
    log_audit_event(
        category="profile_change_request",
        event_type="identity_request_canceled",
        actor_user=current_user,
        target_user=current_user,
        target_member=member,
        before={"request_id": request_record.id, "status": "pending"},
        after={"request_id": request_record.id, "status": request_record.status},
        metadata={"member_note": request_record.member_note},
    )
    db.session.commit()
    flash(_("Your pending identity change request was canceled."), "success")
    return redirect(url_for("account.account"))


@account_bp.route("/account/billing", methods=["POST"])
@login_required
def manage_member_billing():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))

    try:
        portal_session = get_portal_session(member)
        return redirect(portal_session.url, code=303)
    except ValueError as exc:
        flash(str(exc), "warning")
    except stripe.StripeError as exc:
        current_app.logger.error("Could not create Stripe portal session for member_id=%s: %s", member.id, exc)
        flash(_("Could not open the Stripe customer portal right now. Please try again later."), "danger")
    return redirect(url_for("account.account"))


@account_bp.route("/account/resume-payment", methods=["POST"])
@login_required
def resume_member_payment():
    member = get_current_member_for_user(current_user)
    if member is None:
        flash(_("No membership profile is linked to this account yet."), "warning")
        return redirect(url_for("public.index"))
    if not can_resume_payment(member):
        flash(_("This membership cannot be resumed from here. Use billing management instead if a Stripe customer already exists."), "warning")
        return redirect(url_for("account.account"))

    try:
        session, _cycle = create_checkout_session_for_member(member)
        db.session.commit()
        return redirect(session.url, code=303)
    except stripe.StripeError as exc:
        current_app.logger.error("Could not resume Checkout for member_id=%s: %s", member.id, exc)
        flash(_("Could not restart the Stripe Checkout session right now. Please try again later."), "danger")
    except Exception:
        current_app.logger.exception("Unexpected error while resuming payment for member_id=%s", member.id)
        flash(_("Could not restart the membership payment right now."), "danger")
    return redirect(url_for("account.account"))


@account_bp.route("/account/resend-verification", methods=["POST"])
@login_required
def resend_verification_email():
    if current_user.email_is_verified:
        flash(_("Your email address is already verified."), "info")
        return redirect(url_for("account.account"))

    try:
        if send_email_verification_email(current_app._get_current_object(), current_user):
            flash(_("We sent you a new verification email."), "success")
        else:
            flash(_("We could not send a verification email because no sender account is configured yet."), "warning")
    except Exception as exc:
        current_app.logger.warning("Could not resend verification email for user_id=%s: %s", current_user.id, exc)
        flash(_("We could not send a verification email right now."), "danger")
    return redirect(url_for("account.account"))
