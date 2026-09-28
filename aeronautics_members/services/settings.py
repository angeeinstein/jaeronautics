"""Runtime settings stored in the database.

Some configuration is editable by administrators at runtime (Stripe keys, forum
credentials, notification channels) rather than fixed in the environment. This
module is the single reader for those values, so a route, a webhook and a
background job all see the same settings.

Values fall back to the environment defaults in ``config.py`` when the database
has no row yet, which is what makes a fresh install work before anyone has
opened the settings page.
"""

from ..config import DEFAULT_STRIPE_SETTINGS, STRIPE_SETTING_KEYS
from ..db_models import Setting, db


def get_settings_map(keys=None):
    query = db.select(Setting)
    if keys:
        query = query.where(Setting.key.in_(list(keys)))
    return {setting.key: setting.value for setting in db.session.execute(query).scalars().all()}


def get_stripe_settings_map():
    values = dict(DEFAULT_STRIPE_SETTINGS)
    values.update(get_settings_map(STRIPE_SETTING_KEYS))
    return values
