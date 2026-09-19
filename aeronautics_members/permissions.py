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


# Short descriptions for showing what an account can actually do, which is the
# only question a role list is really being asked.
PERMISSION_LABELS = {
    Permission.ADMIN_ACCESS: "Open the admin workspace",
    Permission.ACCOUNTS_VIEW: "See member accounts",
    Permission.ACCOUNTS_BILLING: "Re-sync a member's billing with Stripe",
    Permission.ACCOUNTS_PRIVACY: "Export a member's data and erase accounts",
    Permission.APPROVALS_REVIEW: "Approve profile change requests",
    Permission.FORUM_MODERATE: "Moderate the forum and avatars",
    Permission.LOGS_VIEW: "Read the audit log",
    Permission.NOTIFICATIONS_MANAGE: "Send test email and resolve undelivered email",
    Permission.SETTINGS_GENERAL: "Change general and notification settings",
    Permission.SETTINGS_CREDENTIALS: "Read and change the Stripe, Discourse and SMTP credentials",
    Permission.SYSTEM_UPDATE: "Install a new version and roll one back",
    Permission.ROLES_MANAGE: "Grant and revoke access for other people",
}


def permissions_for(role_slugs):
    """Every capability carried by this set of role slugs."""
    granted = set()
    for slug in role_slugs:
        granted |= ROLE_PERMISSIONS.get(slug, frozenset())
    return granted


def covering_role(slug, selected_slugs):
    """A selected role that already grants everything ``slug`` does, if any.

    "Admin and Super Admin" and "Super Admin alone" describe an identical
    account, because the second bundle contains the first. Offering both as
    distinct choices asks a question with no answer, so the redundant one is
    named here -- shown as already included, and dropped when the change is
    saved so the stored set has one spelling.
    """
    mine = ROLE_PERMISSIONS.get(slug, frozenset())
    if not mine:
        return None
    for other in sorted(selected_slugs):
        if other == slug:
            continue
        theirs = ROLE_PERMISSIONS.get(other, frozenset())
        if not mine <= theirs:
            continue
        # Two roles granting exactly the same thing would otherwise cover each
        # other and both be dropped; keep the first by name.
        if mine == theirs and other > slug:
            continue
        return other
    return None


def minimal_roles(slugs):
    """``slugs`` with anything another of them already covers removed."""
    selected = set(slugs)
    return {slug for slug in selected if covering_role(slug, selected) is None}


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
