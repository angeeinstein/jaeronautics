"""What may be done, and which roles may do it.

Access checks used to name roles directly -- ``has_role("superadmin")`` at each
route and in each template. That works until a second kind of privileged user
appears: adding a forum moderator would have meant finding every route a
moderator should reach and editing its check, which is exactly the kind of edit
that gets one route wrong and nobody notices for a year.

So nothing outside this module asks *who* somebody is. Routes declare the
capability they need, this table says which roles carry it, and a role is a
bundle of capabilities with a name. Adding a role is a new entry in
``ROLE_PERMISSIONS`` -- no route, template or decorator changes -- and the new
role's holders can immediately do exactly what the entry lists.

Two deliberate consequences:

* **There is no role implication.** ``superadmin`` is not "admin plus extra" by
  inheritance; its bundle simply contains the admin bundle. One mechanism
  instead of two, and the table shows the whole truth at a glance.
* **Lockout prevention still counts people, not capabilities in the abstract.**
  ``roles_with(...)`` exists so the guards can ask "who else could still install
  an update" rather than "who else is a superadmin", which keeps working when a
  future role also carries it.
"""


class Permission:
    """Capability names. Referenced from routes, templates and guards."""

    # Seeing the admin workspace at all. Every privileged role needs it, or the
    # navigation and the dashboard are unreachable.
    ADMIN_ACCESS = "admin.access"

    # Member administration.
    ACCOUNTS_VIEW = "accounts.view"
    ACCOUNTS_BILLING = "accounts.billing"
    ACCOUNTS_PRIVACY = "accounts.privacy"  # export another member's data, erase an account
    APPROVALS_REVIEW = "approvals.review"
    FORUM_MODERATE = "forum.moderate"
    LOGS_VIEW = "logs.view"
    NOTIFICATIONS_MANAGE = "notifications.manage"

    # Configuration. The split is "settings anyone administering the site may
    # change" against "settings that hand over the association's credentials".
    SETTINGS_GENERAL = "settings.general"
    SETTINGS_CREDENTIALS = "settings.credentials"

    # Taking over the installation, or deciding who else may.
    SYSTEM_UPDATE = "system.update"
    ROLES_MANAGE = "roles.manage"


# Roles, and what each one may do. This is the whole access model.
#
# To add a role: add an entry, grant it to somebody, done. seed_default_roles()
# creates the row from this table on the next start, and every existing check
# starts honouring it without being touched.
ROLE_PERMISSIONS = {
    "admin": frozenset({
        Permission.ADMIN_ACCESS,
        Permission.ACCOUNTS_VIEW,
        Permission.ACCOUNTS_BILLING,
        Permission.ACCOUNTS_PRIVACY,
        Permission.APPROVALS_REVIEW,
        Permission.FORUM_MODERATE,
        Permission.LOGS_VIEW,
        Permission.NOTIFICATIONS_MANAGE,
        Permission.SETTINGS_GENERAL,
    }),
    "superadmin": frozenset({
        Permission.ADMIN_ACCESS,
        Permission.ACCOUNTS_VIEW,
        Permission.ACCOUNTS_BILLING,
        Permission.ACCOUNTS_PRIVACY,
        Permission.APPROVALS_REVIEW,
        Permission.FORUM_MODERATE,
        Permission.LOGS_VIEW,
        Permission.NOTIFICATIONS_MANAGE,
        Permission.SETTINGS_GENERAL,
        Permission.SETTINGS_CREDENTIALS,
        Permission.SYSTEM_UPDATE,
        Permission.ROLES_MANAGE,
    }),
}

ROLE_LABELS = {
    "admin": ("Admin", "Can administer members, approvals, the forum and general settings."),
    "superadmin": (
        "Super Admin",
        "Can install updates, manage credentials and grant administrator access.",
    ),
}

# Roles whose holders must never all disappear, and what is lost with the last
# one. The guards read this rather than hard-coding "admin" and "superadmin",
# so a role added above is protected by naming it here and nowhere else.
PROTECTED_PERMISSIONS = {
    Permission.ADMIN_ACCESS: "nobody would be able to administer the site",
    Permission.SYSTEM_UPDATE: "nobody would be able to install an update",
    Permission.ROLES_MANAGE: "nobody would be able to grant access to anyone else",
}


def permissions_for(role_slugs):
    """Every capability carried by this set of role slugs."""
    granted = set()
    for slug in role_slugs:
        granted |= ROLE_PERMISSIONS.get(slug, frozenset())
    return granted


def roles_with(permission):
    """The role slugs that carry ``permission``.

    Used to count who still holds a capability, which is how the lockout guards
    stay correct when a new role is given one of the protected permissions.
    """
    return {
        slug for slug, granted in ROLE_PERMISSIONS.items() if permission in granted
    }


def role_label(slug):
    return ROLE_LABELS.get(slug, (slug.replace("_", " ").title(), None))[0]


def role_description(slug):
    return ROLE_LABELS.get(slug, (None, None))[1]
