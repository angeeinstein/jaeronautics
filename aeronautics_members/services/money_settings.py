"""Who carries Stripe's fees: the settings, kept on their own so that both the
teams' money (services/team_money.py) and selling for credit
(services/credit_sales.py) can read them without needing each other.

The association, unless the board decides otherwise: then a team's share of a
fee its member paid is that payment less Stripe's fee for it, and the team
is passed on less. For credit there is no one fee per sale (a top-up's fee
is spread over whatever it buys, from whichever seller), so instead the
association may keep a share of what a team sells. Both settings count from
the moment they are set: each payment and each sale keeps how it was then.
"""

from . import ValidationError

SETTING_FEE_PAYER = "team_fees_stripe_fee_payer"
SETTING_CREDIT_SHARE = "team_credit_sales_share_bps"
SETTING_KEYS = (SETTING_FEE_PAYER, SETTING_CREDIT_SHARE)
FEE_PAYERS = ("association", "team")
#: At most 20 %: a share, not a second price.
MOST_CREDIT_SHARE_BPS = 2000


def _setting(key):
    from .settings import get_settings_map

    return get_settings_map([key]).get(key)


def teams_bear_fees():
    """Whether a team fee paid now carries its own Stripe fee."""
    return _setting(SETTING_FEE_PAYER) == "team"


def credit_share_bps():
    """The association's share of a team's credit sale, in hundredths of a percent."""
    try:
        return max(0, min(int(_setting(SETTING_CREDIT_SHARE) or 0), MOST_CREDIT_SHARE_BPS))
    except ValueError:
        return 0


def save_money_settings(actor, *, fee_payer, credit_share_bps):
    """Who carries Stripe's fees on team fees, and the share kept of team credit sales. The keys that changed."""
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
    })
