"""Who carries Stripe's fees: the settings, kept on their own so that both the
teams' money (services/team_money.py) and selling for credit
(services/credit_sales.py) can read them without needing each other.

The association, unless the board decides otherwise: then a team's share of a
fee its member paid is that payment less Stripe's fee for it, and of a credit
sale the price less its share of the fee of the top-up that paid for it
(services/credit_fees.py). This one counts for every payment and sale, past
and future: switching it changes what every team is owed at once
(docs/money-ledger-plan.md).
"""

from . import ValidationError

SETTING_FEE_PAYER = "team_fees_stripe_fee_payer"
#: Differences between Stripe and the portal emailed to the admins and the
#: treasurer (services/money_checks.py); on unless switched off.
SETTING_DIFFERENCE_EMAILS = "money_differences_emailed"
SETTING_KEYS = (SETTING_FEE_PAYER, SETTING_DIFFERENCE_EMAILS)
FEE_PAYERS = ("association", "team")


def _setting(key):
    from .settings import get_settings_map

    return get_settings_map([key]).get(key)


def teams_bear_fees():
    """Whether the teams carry Stripe's fees: on their fees, and on their credit sales' top-ups."""
    return _setting(SETTING_FEE_PAYER) == "team"


def difference_emails():
    """Whether differences between Stripe and the portal are emailed."""
    return _setting(SETTING_DIFFERENCE_EMAILS) != "False"


def save_money_settings(actor, *, fee_payer, difference_emails=True):
    """Who carries Stripe's fees, and whether differences with Stripe are emailed. The keys
    that changed."""
    from .settings_sections import _write

    if fee_payer not in FEE_PAYERS:
        raise ValidationError("Choose who pays Stripe's fees.", code="money_settings_invalid",
                              details={"fields": {"fee_payer": "Choose who pays Stripe's fees."}})
    return _write(actor, "money", {
        SETTING_FEE_PAYER: "team" if fee_payer == "team" else None,
        SETTING_DIFFERENCE_EMAILS: None if difference_emails else "False",
    })
