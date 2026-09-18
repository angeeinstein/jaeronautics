"""Finding and describing members.

Lookups live here rather than in the route layer because several callers need
the same resolution order -- a webhook knows a Stripe id, an admin page knows a
user id, a signup recovery knows only an email address -- and they must all land
on the same member.

``IDENTITY_MEMBER_FIELDS`` are the parts of a profile that identify a person;
changing them goes through an approval request rather than taking effect
immediately, which is why they are listed separately from the contact fields a
member may edit freely.
"""

from ..db_models import Member, db

DIRECT_MEMBER_PROFILE_FIELDS = (
    "street",
    "house_number",
    "postal_code",
    "city",
    "country",
    "phone_private",
    "email_private",
    "phone_work",
    "email_work",
)

IDENTITY_MEMBER_FIELDS = (
    "salutation",
    "title",
    "first_name",
    "last_name",
    "year_group",
)

MEMBER_PROFILE_FIELDS = IDENTITY_MEMBER_FIELDS + DIRECT_MEMBER_PROFILE_FIELDS


def normalize_optional_member_value(field_name, value):
    if value == "" and field_name in {"title", "phone_work", "email_work"}:
        return None
    return value


def apply_member_profile(member, form_data, fields=MEMBER_PROFILE_FIELDS):
    for field_name in fields:
        value = normalize_optional_member_value(field_name, form_data.get(field_name))
        setattr(member, field_name, value)
    if "terms_accepted" in form_data:
        member.terms_accepted = bool(form_data.get("terms_accepted"))


def build_member_payload(member):
    payload = {field_name: getattr(member, field_name) for field_name in MEMBER_PROFILE_FIELDS}
    payload["terms_accepted"] = True
    return payload


def get_member_by_email(email):
    if not email:
        return None
    normalized_email = str(email).strip()
    if not normalized_email:
        return None
    return Member.query.filter_by(email_private=normalized_email).first()


def get_member_by_stripe_reference(customer_id=None, subscription_id=None, member_id=None, user_id=None):
    """Resolve a member from whichever identifier the caller happens to hold.

    Ordered most to least specific, so a webhook carrying both a member id and a
    customer id resolves by the id we assigned rather than by a Stripe reference
    that could have been reassigned.
    """
    if member_id:
        member = db.session.get(Member, int(member_id))
        if member is not None:
            return member
    if user_id:
        member = db.session.execute(db.select(Member).filter_by(user_id=int(user_id))).scalar_one_or_none()
        if member is not None:
            return member
    if subscription_id:
        member = Member.query.filter_by(stripe_subscription_id=subscription_id).first()
        if member is not None:
            return member
    if customer_id:
        return Member.query.filter_by(stripe_customer_id=customer_id).first()
    return None
