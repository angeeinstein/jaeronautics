"""Permissions as rows with levels, for a person's *Access* tab and a team member's page
(permissions.PermissionRow; docs/permissions-plan.md, "The page with levels").

Each row says what every level holds, what it needs of other rows, the level held now, the
level the person's roles give, and the highest level the person looking may set. The page
sends back the permissions of the levels chosen; the server decides (services/access.py,
services/teams.py).
"""

from ._core import Model


class PermissionLevelOut(Model):
    label: str
    #: Only shows, changes nothing: drawn tinted rather than filled.
    sees_only: bool
    #: Everything held at this level, the lower levels' too.
    permissions: list[str]
    #: Other rows this level needs, at least at these levels: ``{row key: level}``.
    needs: dict[str, int]


class PermissionRowOut(Model):
    key: str
    area: str
    label: str
    description: str
    #: Could be used to gain more rights, or cannot be undone.
    sensitive: bool
    #: The levels above None, lowest first.
    levels: list[PermissionLevelOut]
    #: Held now (0: none).
    level: int
    #: What the roles held give; a level above is added, one below taken away.
    from_roles: int
    #: The highest level the person looking may set. A row held above it is not theirs to change.
    may_reach: int


def rows_out(rows, row_needs, *, held, from_roles, mine, row_model=PermissionRowOut,
             level_model=PermissionLevelOut):
    """``rows`` as the page shows them: ``held`` by the person, ``from_roles`` what their roles
    give, ``mine`` what the person looking may give or take."""
    return [
        row_model(
            key=row.key, area=row.area, label=row.label, description=row.description, sensitive=row.sensitive,
            levels=[level_model(label=step.label, sees_only=step.sees_only, permissions=sorted(row.at(index)),
                                needs=row_needs[row.key][index - 1])
                    for index, step in enumerate(row.levels, start=1)],
            level=row.level_of(held),
            from_roles=row.level_of(from_roles),
            may_reach=row.level_of(mine),
        )
        for row in rows
    ]
