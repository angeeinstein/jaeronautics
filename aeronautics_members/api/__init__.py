"""The JSON API under /api/v1, which the new front end is built on.

How an endpoint is declared and answered: _core.py. The description of the
whole API, from which the front end's types are generated: openapi.py
(``flask api-schema``). One module per area; importing it registers its
endpoints on ``api_bp``.
"""

from . import (  # noqa: F401 -- registers their endpoints
    account,
    admin_account,
    admin_backup,
    admin_accounts,
    admin_dashboard,
    admin_legal,
    admin_logs,
    admin_mail,
    admin_money,
    admin_reviews,
    admin_settings,
    admin_system,
    admin_teams,
    form_options,
    session,
    site,
    team_manage,
    teams,
    teams_money,
)
from ._core import ENDPOINTS, api_bp, error, is_api_request

__all__ = ["ENDPOINTS", "api_bp", "error", "is_api_request"]
