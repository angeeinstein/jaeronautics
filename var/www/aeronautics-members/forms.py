from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, BooleanField, SubmitField, PasswordField, RadioField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, Optional, InputRequired
from flask_babel import _, lazy_gettext as _l

# Comprehensive list of countries for the dropdown
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

class MembershipForm(FlaskForm):
    """Form for new member registration with validation."""

    salutation = SelectField(
        _l('Salutation'),
        choices=[('', _l('-- Select --')), ('Mr', _l('Mr')), ('Ms', _l('Ms')), ('Diverse', _l('Diverse'))],
        validators=[DataRequired()]
    )
    title = StringField(
        _l('Title'),
        validators=[Optional()]
    )
    first_name = StringField(
        _l('First Name'),
        validators=[DataRequired()]
    )
    last_name = StringField(
        _l('Last Name'),
        validators=[DataRequired()]
    )
    street = StringField(
        _l('Street'),
        validators=[DataRequired(), Length(max=255)]
    )
    house_number = StringField(
        _l('House Number'),
        validators=[DataRequired()]
    )
    postal_code = StringField(
        _l('Postal Code'),
        validators=[DataRequired()]
    )
    city = StringField(
        _l('City'),
        validators=[DataRequired()]
    )
    country = SelectField(
        _l('Country'),
        choices=COUNTRIES,
        validators=[DataRequired()]
    )
    phone_private = StringField(
        _l('Private Phone'),
        validators=[
            DataRequired(),
            Regexp(r'^\+?[0-9\s\-\(\)]*$', message=_l("Invalid phone number format"))
        ]
    )
    email_private = StringField(
        _l('Private Email'),
        validators=[DataRequired(), Email()]
    )
    phone_work = StringField(
        _l('Work Phone'),
        validators=[
            Optional(),
            Regexp(r'^\+?[0-9\s\-\(\)]*$', message=_l("Invalid phone number format"))
        ]
    )
    email_work = StringField(
        _l('Work Email'),
        validators=[Optional(), Email()]
    )
    year_group = StringField(
        _l('Year Group'),
        validators=[
            DataRequired(),
            Length(max=50),
            Regexp(r'^[A-Z]+[0-9]{2}$', message=_l("Invalid format. Please use uppercase letters followed by two numbers, like LAV25."))
        ]
    )
    payment_method = RadioField(
        _l('Payment Method'),
        choices=[('checkout', _l('Card or SEPA Direct Debit')), ('invoice', _l('Invoice'))],
        validators=[Optional()],
        default='checkout'
    )
    terms_accepted = BooleanField(
        _l('I accept the terms and conditions'),
        validators=[DataRequired(message=_l("You must accept the terms and conditions."))]
    )
    terms_accepted = BooleanField(_('I consent to the processing of my data as described in the privacy policy.'), validators=[InputRequired()])
    submit = SubmitField(_('Proceed to Payment'))

class RegistrationForm(FlaskForm):
    email = StringField(_('Email'), validators=[DataRequired(), Email()])
    password = PasswordField(_('Password'), validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(_('Confirm Password'), validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField(_('Register'))

class LoginForm(FlaskForm):
    email = StringField(_('Email'), validators=[DataRequired(), Email()])
    password = PasswordField(_('Password'), validators=[DataRequired()])
    submit = SubmitField(_('Login'))

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(_('Current Password'), validators=[DataRequired()])
    new_password = PasswordField(_('New Password'), validators=[DataRequired(), Length(min=8)])
    confirm_new_password = PasswordField(_('Confirm New Password'), validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField(_('Change Password'))

class TestEmailForm(FlaskForm):
    sender = SelectField(_l('Sender'), validators=[DataRequired()])
    recipient = StringField(_('Recipient Email'), validators=[DataRequired(), Email()])
    template = SelectField(_l('Template'), validators=[DataRequired()])
    submit = SubmitField(_('Send Test Email'))
