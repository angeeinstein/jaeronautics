"""The JSON API under /api/v1, which the new front end is built on.

How an endpoint is declared and answered: _core.py. The description of the
whole API, from which the front end's types are generated: openapi.py
(``flask api-schema``). One module per area; importing it registers its
endpoints on ``api_bp``.
"""

from . import admin_account, admin_accounts, admin_dashboard, admin_reviews, admin_teams, session  # noqa: F401 -- registers their endpoints
from ._core import ENDPOINTS, api_bp, error, is_api_request

__all__ = ["ENDPOINTS", "api_bp", "error", "is_api_request"]
