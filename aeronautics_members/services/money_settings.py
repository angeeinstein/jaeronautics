"""Who carries Stripe's fees: the settings, kept on their own so that both the
teams' money (services/team_money.py) and selling for credit
(services/credit_sales.py) can read them without needing each other.

The association, unless the board decides otherwise: then a team's share of a
fee its member paid is that payment less Stripe's fee for it, and of a credit
sale the price less its share of the fee of the top-up that paid for it
(services/credit_fees.py). This one counts for every payment and sale, past
and future: switching it changes what every team is owed at once
(docs/money-ledger-plan.md). Apart from that, the association may keep a
share of what a team sells for credit; that share is kept with each sale as
it was then.
"""

from . import ValidationError

SETTING_FEE_PAYER = "team_fees_stripe_fee_payer"
SETTING_CREDIT_SHARE = "team_credit_sales_share_bps"
#: Differences between Stripe and the portal emailed to the admins and the
#: treasurer (services/money_checks.py); on unless switched off.
SETTING_DIFFERENCE_EMAILS = "money_differences_emailed"
SETTING_KEYS = (SETTING_FEE_PAYER, SETTING_CREDIT_SHARE, SETTING_DIFFERENCE_EMAILS)
FEE_PAYERS = ("association", "team")
#: At most 20 %: a share, not a second price.
MOST_CREDIT_SHARE_BPS = 2000


def _setting(key):
    from .settings import get_settings_map

    return get_settings_map([key]).get(key)


def teams_bear_fees():
    """Whether the teams carry Stripe's fees: on their fees, and on their credit sales' top-ups."""
    return _setting(SETTING_FEE_PAYER) == "team"


def credit_share_bps():
    """The association's share of a team's credit sale, in hundredths of a percent."""
    try:
        return max(0, min(int(_setting(SETTING_CREDIT_SHARE) or 0), MOST_CREDIT_SHARE_BPS))
    except ValueError:
        return 0


def difference_emails():
    """Whether differences between Stripe and the portal are emailed."""
    return _setting(SETTING_DIFFERENCE_EMAILS) != "False"


def save_money_settings(actor, *, fee_payer, credit_share_bps, difference_emails=True):
    """Who carries Stripe's fees, the share kept of team credit sales, and whether differences
    with Stripe are emailed. The keys that changed."""
    from .settings_sections import _write

    if fee_payer not in FEE_PAYERS:
        raise ValidationError("Choose who pays Stripe's fees.", code="money_settings_invalid",
                              details={"fields": {"fee_payer": "Choose who pays Stripe's fees."}})
    if not 0 <= int(credit_share_bps) <= MOST_CREDIT_SHARE_BPS:
        message = "Between 0 % and 20 %."
        raise ValidationError(message, code="money_settings_invalid",
                              details={"fields": {"credit_share_bps": message}})
    return _write(actor, "money", {
        SETTING_FEE_PAYER: "team" if fee_payer == "team" else None,
        SETTING_CREDIT_SHARE: str(int(credit_share_bps)) if int(credit_share_bps) else None,
        SETTING_DIFFERENCE_EMAILS: None if difference_emails else "False",
    })
