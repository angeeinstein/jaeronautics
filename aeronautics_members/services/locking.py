"""Row locks for decisions two people can make at the same moment.

Every decision here checked the state first -- "still waiting for review?",
"is there another administrator left?" -- but two requests arriving together
both read the state before either had saved, and both went ahead: a photo
approved and rejected at once, with the member emailed both; a change
request whose new name was written into the profile while the request ended
up rejected; two superadmins each taking the other's rights, leaving none.

A locked read makes the second request wait until the first has committed,
and then read what it decided. The database releases the lock when the
transaction ends, whichever way it ends.

SQLite, which the tests use, has no row locks and leaves the clause out; the
re-read still happens there.
"""

from ..db_models import ROLE_ADMIN, ROLE_SUPERADMIN, Role, db


def locked(select):
    """``select``, with the rows it finds locked until commit and read afresh.

    Afresh matters as much as the lock: a row loaded earlier in the same
    request sits in the session, and without this the locked read would hand
    back that stale copy -- still "pending" -- instead of what was committed
    while this request waited.
    """
    return select.with_for_update().execution_options(populate_existing=True)


def lock_administration():
    """Take the lock every change to who administers the site goes through.

    "Is anybody else left who can administer this?" is a question about all
    accounts, so locking the one account being changed does not help: two
    superadmins revoking each other change two different rows. Every such
    change -- roles, switching an account off, erasing one -- locks the admin
    and superadmin roles instead, so they happen one after another.
    """
    db.session.execute(
        locked(db.select(Role.id).where(Role.slug.in_((ROLE_ADMIN, ROLE_SUPERADMIN))))
    ).all()
