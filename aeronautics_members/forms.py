from flask_babel import _, lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    StopValidation,
    ValidationError,
)

from .member_categories import (
    DEFAULT_CATEGORY,
    category_choices,
    checks_institutional_domain,
    requires_institutional_email,
    requires_year_group,
    shows_year_group,
)
from .services.institutional_email import is_institutional_email, normalize_email

COUNTRIES = [
    ("", _l("-- Select a Country --")),
    ("Afghanistan", "Afghanistan"), ("Albania", "Albania"), ("Algeria", "Algeria"),
    ("Andorra", "Andorra"), ("Angola", "Angola"), ("Antigua and Barbuda", "Antigua and Barbuda"),
    ("Argentina", "Argentina"), ("Armenia", "Armenia"), ("Australia", "Australia"),
    ("Austria", "Austria"), ("Azerbaijan", "Azerbaijan"), ("Bahamas", "Bahamas"),
    ("Bahrain", "Bahrain"), ("Bangladesh", "Bangladesh"), ("Barbados", "Barbados"),
    ("Belarus", "Belarus"), ("Belgium", "Belgium"), ("Belize", "Belize"),
    ("Benin", "Benin"), ("Bhutan", "Bhutan"), ("Bolivia", "Bolivia"),
    ("Bosnia and Herzegovina", "Bosnia and Herzegovina"), ("Botswana", "Botswana"),
    ("Brazil", "Brazil"), ("Brunei", "Brunei"), ("Bulgaria", "Bulgaria"),
    ("Burkina Faso", "Burkina Faso"), ("Burundi", "Burundi"), ("Cabo Verde", "Cabo Verde"),
    ("Cambodia", "Cambodia"), ("Cameroon", "Cameroon"), ("Canada", "Canada"),
    ("Central African Republic", "Central African Republic"), ("Chad", "Chad"),
    ("Chile", "Chile"), ("China", "China"), ("Colombia", "Colombia"),
    ("Comoros", "Comoros"), ("Congo, Democratic Republic of the", "Congo, Democratic Republic of the"),
    ("Congo, Republic of the", "Congo, Republic of the"), ("Costa Rica", "Costa Rica"),
    ("Cote d'Ivoire", "Cote d'Ivoire"), ("Croatia", "Croatia"), ("Cuba", "Cuba"),
    ("Cyprus", "Cyprus"), ("Czech Republic", "Czech Republic"), ("Denmark", "Denmark"),
    ("Djibouti", "Djibouti"), ("Dominica", "Dominica"), ("Dominican Republic", "Dominican Republic"),
    ("Ecuador", "Ecuador"), ("Egypt", "Egypt"), ("El Salvador", "El Salvador"),
    ("Equatorial Guinea", "Equatorial Guinea"), ("Eritrea", "Eritrea"), ("Estonia", "Estonia"),
    ("Eswatini", "Eswatini"), ("Ethiopia", "Ethiopia"), ("Fiji", "Fiji"),
    ("Finland", "Finland"), ("France", "France"), ("Gabon", "Gabon"),
    ("Gambia", "Gambia"), ("Georgia", "Georgia"), ("Germany", "Germany"),
    ("Ghana", "Ghana"), ("Greece", "Greece"), ("Grenada", "Grenada"),
    ("Guatemala", "Guatemala"), ("Guinea", "Guinea"), ("Guinea-Bissau", "Guinea-Bissau"),
    ("Guyana", "Guyana"), ("Haiti", "Haiti"), ("Honduras", "Honduras"),
    ("Hungary", "Hungary"), ("Iceland", "Iceland"), ("India", "India"),
    ("Indonesia", "Indonesia"), ("Iran", "Iran"), ("Iraq", "Iraq"),
    ("Ireland", "Ireland"), ("Israel", "Israel"), ("Italy", "Italy"),
    ("Jamaica", "Jamaica"), ("Japan", "Japan"), ("Jordan", "Jordan"),
    ("Kazakhstan", "Kazakhstan"), ("Kenya", "Kenya"), ("Kiribati", "Kiribati"),
    ("Kosovo", "Kosovo"), ("Kuwait", "Kuwait"), ("Kyrgyzstan", "Kyrgyzstan"),
    ("Laos", "Laos"), ("Latvia", "Latvia"), ("Lebanon", "Lebanon"),
    ("Lesotho", "Lesotho"), ("Liberia", "Liberia"), ("Libya", "Libya"),
    ("Liechtenstein", "Liechtenstein"), ("Lithuania", "Lithuania"), ("Luxembourg", "Luxembourg"),
    ("Madagascar", "Madagascar"), ("Malawi", "Malawi"), ("Malaysia", "Malaysia"),
    ("Maldives", "Maldives"), ("Mali", "Mali"), ("Malta", "Malta"),
    ("Marshall Islands", "Marshall Islands"), ("Mauritania", "Mauritania"),
    ("Mauritius", "Mauritius"), ("Mexico", "Mexico"), ("Micronesia", "Micronesia"),
    ("Moldova", "Moldova"), ("Monaco", "Monaco"), ("Mongolia", "Mongolia"),
    ("Montenegro", "Montenegro"), ("Morocco", "Morocco"), ("Mozambique", "Mozambique"),
    ("Myanmar (Burma)", "Myanmar (Burma)"), ("Namibia", "Namibia"), ("Nauru", "Nauru"),
    ("Nepal", "Nepal"), ("Netherlands", "Netherlands"), ("New Zealand", "New Zealand"),
    ("Nicaragua", "Nicaragua"), ("Niger", "Niger"), ("Nigeria", "Nigeria"),
    ("North Korea", "North Korea"), ("North Macedonia", "North Macedonia"), ("Norway", "Norway"),
    ("Oman", "Oman"), ("Pakistan", "Pakistan"), ("Palau", "Palau"),
    ("Palestine", "Palestine"), ("Panama", "Panama"), ("Papua New Guinea", "Papua New Guinea"),
    ("Paraguay", "Paraguay"), ("Peru", "Peru"), ("Philippines", "Philippines"),
    ("Poland", "Poland"), ("Portugal", "Portugal"), ("Qatar", "Qatar"),
    ("Romania", "Romania"), ("Russia", "Russia"), ("Rwanda", "Rwanda"),
    ("Saint Kitts and Nevis", "Saint Kitts and Nevis"), ("Saint Lucia", "Saint Lucia"),
    ("Saint Vincent and the Grenadines", "Saint Vincent and the Grenadines"), ("Samoa", "Samoa"),
    ("San Marino", "San Marino"), ("Sao Tome and Principe", "Sao Tome and Principe"),
    ("Saudi Arabia", "Saudi Arabia"), ("Senegal", "Senegal"), ("Serbia", "Serbia"),
    ("Seychelles", "Seychelles"), ("Sierra Leone", "Sierra Leone"), ("Singapore", "Singapore"),
    ("Slovakia", "Slovakia"), ("Slovenia", "Slovenia"), ("Solomon Islands", "Solomon Islands"),
    ("Somalia", "Somalia"), ("South Africa", "South Africa"), ("South Korea", "South Korea"),
    ("South Sudan", "South Sudan"), ("Spain", "Spain"), ("Sri Lanka", "Sri Lanka"),
    ("Sudan", "Sudan"), ("Suriname", "Suriname"), ("Sweden", "Sweden"),
    ("Switzerland", "Switzerland"), ("Syria", "Syria"), ("Taiwan", "Taiwan"),
    ("Tajikistan", "Tajikistan"), ("Tanzania", "Tanzania"), ("Thailand", "Thailand"),
    ("Timor-Leste", "Timor-Leste"), ("Togo", "Togo"), ("Tonga", "Tonga"),
    ("Trinidad and Tobago", "Trinidad and Tobago"), ("Tunisia", "Tunisia"), ("Turkey", "Turkey"),
    ("Turkmenistan", "Turkmenistan"), ("Tuvalu", "Tuvalu"), ("Uganda", "Uganda"),
    ("Ukraine", "Ukraine"), ("United Arab Emirates", "United Arab Emirates"),
    ("United Kingdom", "United Kingdom"), ("United States", "United States"),
    ("Uruguay", "Uruguay"), ("Uzbekistan", "Uzbekistan"), ("Vanuatu", "Vanuatu"),
    ("Vatican City", "Vatican City"), ("Venezuela", "Venezuela"), ("Vietnam", "Vietnam"),
    ("Yemen", "Yemen"), ("Zambia", "Zambia"), ("Zimbabwe", "Zimbabwe"),
]

SALUTATION_CHOICES = [("", _l("-- Select --")), ("Mr", _l("Mr")), ("Ms", _l("Ms")), ("Diverse", _l("Diverse"))]


def resolve_member_category(form):
    """Which category's rules this form is being validated under.

    Most forms carry the choice as a field. The profile form does not -- it
    edits an address and a phone number, not what kind of member somebody is --
    so its route sets ``form.member_category_value`` from the membership being
    edited. Without that fallback a student could move their university address
    to a private one through the profile page and walk straight past the check
    the signup form applies.
    """
    field = getattr(form, "member_category", None)
    if field is not None and hasattr(field, "data"):
        return field.data
    return getattr(form, "member_category_value", None)


YEAR_GROUP_VALIDATOR = Regexp(
    r"^[A-Z]+[0-9]{2}$",
    message=_l("Invalid format. Please use uppercase letters followed by two numbers, like LAV25."),
)


class YearGroupRequirement:
    """Applies the year group rules for whichever member category was chosen.

    Runs first in the chain and decides whether the rest of it should run at
    all, because ``Optional()`` cannot be used here: it raises StopValidation
    on an empty field, which would skip an inline ``validate_year_group`` too,
    and the requirement depends on another field's value rather than this
    one's.

    Which categories are asked, and which must answer, is not decided here --
    see member_categories.py.
    """

    def __call__(self, form, field):
        category = resolve_member_category(form)
        value = (field.data or "").strip()

        if not shows_year_group(category):
            # Not asked of this category. Anything in the box is a leftover
            # from before the choice was switched, not something the member is
            # claiming, so drop it rather than refusing the form over a field
            # they cannot even see.
            field.data = None
            raise StopValidation()

        if not value:
            if requires_year_group(category):
                raise ValidationError(_("Please enter your year group, for example LAV25."))
            # Offered but not required -- an alumnus who does not remember.
            field.data = None
            raise StopValidation()

        return  # let Length and the format check run


YEAR_GROUP_FIELD_VALIDATORS = [YearGroupRequirement(), Length(max=50), YEAR_GROUP_VALIDATOR]


class InstitutionalEmailRequirement:
    """The university or company address, per member category.

    A student must give one, and it must be on a domain the association
    recognises -- that address is the only thing here that says they are a
    student *now*. Nobody else is required to, and nobody else's domain is
    checked: a partner's company address is one no list could anticipate.

    Runs first in the chain for the same reason as the year group: the rule
    depends on another field, and ``Optional()`` would stop an inline check.
    """

    def __call__(self, form, field):
        category = resolve_member_category(form)
        value = normalize_email(field.data)

        if not value:
            if requires_institutional_email(category):
                raise ValidationError(
                    _("Please enter your university email address. We use it to "
                      "confirm that you currently study here.")
                )
            field.data = None
            raise StopValidation()

        field.data = value
        if checks_institutional_domain(category) and not is_institutional_email(value):
            raise ValidationError(
                _("Please use your university address, for example "
                  "name@edu.fh-joanneum.at. Your private address goes in the "
                  "field above.")
            )


class PrivateEmailRequirement:
    """The login must not be an address one of our institutions owns.

    A university or company address is tied to a role that ends. The whole
    reason two addresses are collected is that the association still needs to
    reach somebody after theirs stops working -- so using one as the login is
    the single mistake on this form that locks a member out of their own
    account, and it is the one the form cannot otherwise notice: a university
    address here is perfectly well-formed, just wrong.

    This exists so the explanation can be an error rather than a hint. Nothing
    is printed under the field for the many people who get it right; the one
    who does not gets told exactly what is wrong, at the moment it matters.
    """

    def __call__(self, form, field):
        if not normalize_email(field.data):
            return  # DataRequired has already said what to do about empty
        if is_institutional_email(field.data):
            raise ValidationError(
                _("This looks like a university or company address. Please use a "
                  "private one here: it is your login, and it has to keep working "
                  "after you leave. The university address goes in the field below.")
            )


PRIVATE_EMAIL_FIELD_VALIDATORS = [DataRequired(), Email(), PrivateEmailRequirement()]

INSTITUTIONAL_EMAIL_FIELD_VALIDATORS = [
    InstitutionalEmailRequirement(), Email(), Length(max=255),
]
PHONE_VALIDATOR = Regexp(r"^\+?[0-9\s\-\(\)]*$", message=_l("Invalid phone number format"))


class MembershipForm(FlaskForm):
    salutation = SelectField(_l("Salutation"), choices=SALUTATION_CHOICES, validators=[DataRequired()])
    title = StringField(_l("Title"), validators=[Optional()])
    first_name = StringField(_l("First Name"), validators=[DataRequired()])
    last_name = StringField(_l("Last Name"), validators=[DataRequired()])
    street = StringField(_l("Street"), validators=[DataRequired(), Length(max=255)])
    house_number = StringField(_l("House Number"), validators=[DataRequired()])
    postal_code = StringField(_l("Postal Code"), validators=[DataRequired()])
    city = StringField(_l("City"), validators=[DataRequired()])
    country = SelectField(_l("Country"), choices=COUNTRIES, validators=[DataRequired()])
    phone_private = StringField(_l("Private Phone"), validators=[DataRequired(), PHONE_VALIDATOR])
    email_private = StringField(
        _l("Private Email"), validators=PRIVATE_EMAIL_FIELD_VALIDATORS
    )
    phone_work = StringField(_l("Work Phone"), validators=[Optional(), PHONE_VALIDATOR])
    email_work = StringField(
        _l("University or Company Email"), validators=INSTITUTIONAL_EMAIL_FIELD_VALIDATORS
    )
    member_category = SelectField(
        _l("Membership"), choices=category_choices(), default=DEFAULT_CATEGORY,
        validators=[DataRequired()],
    )
    year_group = StringField(_l("Year Group"), validators=YEAR_GROUP_FIELD_VALIDATORS)
    password = PasswordField(_l("Password"), validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(_l("Confirm Password"), validators=[DataRequired(), EqualTo("password")])
    payment_method = RadioField(
        _l("Payment Method"),
        choices=[("checkout", _l("Card or SEPA Direct Debit")), ("invoice", _l("Invoice"))],
        validators=[Optional()],
        default="checkout",
    )
    terms_accepted = BooleanField(
        _("I consent to the processing of my data as described in the privacy policy."),
        validators=[InputRequired(message=_l("You must accept the privacy policy to continue."))],
    )
    submit = SubmitField(_("Proceed to Payment"))


class CreateMembershipProfileForm(FlaskForm):
    salutation = SelectField(_l("Salutation"), choices=SALUTATION_CHOICES, validators=[DataRequired()])
    title = StringField(_l("Title"), validators=[Optional()])
    first_name = StringField(_l("First Name"), validators=[DataRequired()])
    last_name = StringField(_l("Last Name"), validators=[DataRequired()])
    street = StringField(_l("Street"), validators=[DataRequired(), Length(max=255)])
    house_number = StringField(_l("House Number"), validators=[DataRequired()])
    postal_code = StringField(_l("Postal Code"), validators=[DataRequired()])
    city = StringField(_l("City"), validators=[DataRequired()])
    country = SelectField(_l("Country"), choices=COUNTRIES, validators=[DataRequired()])
    phone_private = StringField(_l("Private Phone"), validators=[DataRequired(), PHONE_VALIDATOR])
    email_private = StringField(
        _l("Private Email"), validators=PRIVATE_EMAIL_FIELD_VALIDATORS
    )
    phone_work = StringField(_l("Work Phone"), validators=[Optional(), PHONE_VALIDATOR])
    email_work = StringField(
        _l("University or Company Email"), validators=INSTITUTIONAL_EMAIL_FIELD_VALIDATORS
    )
    member_category = SelectField(
        _l("Membership"), choices=category_choices(), default=DEFAULT_CATEGORY,
        validators=[DataRequired()],
    )
    year_group = StringField(_l("Year Group"), validators=YEAR_GROUP_FIELD_VALIDATORS)
    payment_method = RadioField(
        _l("Payment Method"),
        choices=[("checkout", _l("Card or SEPA Direct Debit")), ("invoice", _l("Invoice"))],
        validators=[Optional()],
        default="checkout",
    )
    terms_accepted = BooleanField(
        _("I consent to the processing of my data as described in the privacy policy."),
        validators=[InputRequired(message=_l("You must accept the privacy policy to continue."))],
    )
    submit = SubmitField(_("Create Membership and Proceed to Payment"))


class RegistrationForm(FlaskForm):
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(_("Password"), validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(_("Confirm Password"), validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField(_("Register"))


class LoginForm(FlaskForm):
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(_("Password"), validators=[DataRequired()])
    submit = SubmitField(_("Login"))


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(_("Current Password"), validators=[DataRequired()])
    new_password = PasswordField(_("New Password"), validators=[DataRequired(), Length(min=8)])
    confirm_new_password = PasswordField(_("Confirm New Password"), validators=[DataRequired(), EqualTo("new_password")])
    submit = SubmitField(_("Change Password"))


class EmailRequestForm(FlaskForm):
    email = StringField(_("Email"), validators=[DataRequired(), Email()])
    submit = SubmitField(_("Send Link"))


class SetPasswordForm(FlaskForm):
    password = PasswordField(_("Password"), validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(_("Confirm Password"), validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField(_("Save Password"))


class MemberProfileForm(FlaskForm):
    street = StringField(_l("Street"), validators=[DataRequired(), Length(max=255)])
    house_number = StringField(_l("House Number"), validators=[DataRequired()])
    postal_code = StringField(_l("Postal Code"), validators=[DataRequired()])
    city = StringField(_l("City"), validators=[DataRequired()])
    country = SelectField(_l("Country"), choices=COUNTRIES, validators=[DataRequired()])
    phone_private = StringField(_l("Private Phone"), validators=[DataRequired(), PHONE_VALIDATOR])
    email_private = StringField(
        _l("Private Email"), validators=PRIVATE_EMAIL_FIELD_VALIDATORS
    )
    phone_work = StringField(_l("Work Phone"), validators=[Optional(), PHONE_VALIDATOR])
    email_work = StringField(
        _l("University or Company Email"), validators=INSTITUTIONAL_EMAIL_FIELD_VALIDATORS
    )
    submit = SubmitField(_("Save Profile Changes"))


class IdentityChangeRequestForm(FlaskForm):
    salutation = SelectField(_l("Salutation"), choices=SALUTATION_CHOICES, validators=[DataRequired()])
    title = StringField(_l("Title"), validators=[Optional()])
    first_name = StringField(_l("First Name"), validators=[DataRequired()])
    last_name = StringField(_l("Last Name"), validators=[DataRequired()])
    member_category = SelectField(
        _l("Membership"), choices=category_choices(), default=DEFAULT_CATEGORY,
        validators=[DataRequired()],
    )
    year_group = StringField(_l("Year Group"), validators=YEAR_GROUP_FIELD_VALIDATORS)
    member_note = TextAreaField(_l("Why should this be changed?"), validators=[Optional(), Length(max=1000)])
    submit = SubmitField(_("Submit Change Request"))


class TestEmailForm(FlaskForm):
    sender = SelectField(_l("Sender"), validators=[DataRequired()])
    recipient = StringField(_("Recipient Email"), validators=[DataRequired(), Email()])
    template = SelectField(_l("Template"), validators=[DataRequired()])
    submit = SubmitField(_("Send Test Email"))


class MailAccountForm(FlaskForm):
    mail_account_id = HiddenField()
    account_key = StringField(
        _l("Account Key"),
        validators=[
            DataRequired(),
            Length(max=80),
            Regexp(r"^[a-zA-Z0-9_-]+$", message=_l("Use only letters, numbers, dashes, and underscores.")),
        ],
    )
    host = StringField(_l("SMTP Host"), validators=[DataRequired(), Length(max=255)])
    port = IntegerField(_l("SMTP Port"), validators=[DataRequired(), NumberRange(min=1, max=65535)])
    username = StringField(_l("SMTP Username"), validators=[DataRequired(), Length(max=255)])
    password = PasswordField(_l("SMTP Password"), validators=[Optional(), Length(max=255)])
    starttls = BooleanField(_l("Use STARTTLS"))
    submit = SubmitField(_l("Save Mail Account"))
