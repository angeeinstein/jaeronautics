"""Who is using the portal right now -- enough to wait with an update while
somebody is in the middle of something (docs/presence.md).

A browser tab somebody is using says so about every half minute (POST
/api/v1/presence, from frontend/src/lib/presence.ts): the page, as the app's
address pattern ("/teams/:slug", never a token or an id), and whether they
typed into a field since. It only speaks while it is in front and somebody
clicked or typed in it lately, so a tab left open overnight, refreshing
itself, counts for nobody.

Kept is one row per tab, with nothing about who: a random id the tab made up,
signed in or not, the page, and when. A row is gone a quarter of an hour after
its tab went quiet. Settings › Updates shows the count (``report``).
"""

import re
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError

from ..db_models import Presence, db
from .clock import get_now_utc

#: Spoke this lately: somebody is there.
ACTIVE = timedelta(minutes=2)
#: Typed this lately: somebody is filling in a form.
TYPING = timedelta(minutes=3)
#: Rows older than this are deleted.
KEPT = timedelta(minutes=15)
#: More tabs than this are not taken in: the table stays small whoever sends.
MOST_TABS = 2000

TAB = re.compile(r"^[A-Za-z0-9-]{8,36}$")
#: An address pattern of the app, or "other": lowercase words and :names.
PAGE = re.compile(r"^(/|(/(:?[a-z][a-zA-Z0-9-]*))+|other)$")
PAGE_LENGTH = 80


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def page_name(page):
    """The page as given, if it is an address pattern; "other" otherwise."""
    return page if page and len(page) <= PAGE_LENGTH and PAGE.match(page) else "other"


def seen(tab, page, *, typed, signed_in, now=None):
    """A tab says somebody is using it. False when it was not taken in."""
    if not tab or not TAB.match(tab):
        return False
    now = now or get_now_utc()
    db.session.query(Presence).filter(Presence.seen_at < now - KEPT).delete(synchronize_session=False)
    row = db.session.get(Presence, tab)
    if row is None:
        if db.session.query(Presence).count() >= MOST_TABS:
            db.session.commit()
            return False
        row = Presence(tab=tab, first_seen_at=now)
        db.session.add(row)
    row.signed_in = bool(signed_in)
    row.page = page_name(page)
    row.seen_at = now
    if typed:
        row.typed_at = now
    try:
        db.session.commit()
    except IntegrityError:
        # The same tab twice at once: the other request took it in.
        db.session.rollback()
    return True


def report(*, besides=None, at=None):
    """Who is there: counts, the pages, the latest moment anybody was.

    ``besides`` leaves out one tab -- the asking admin's own.
    """
    at = at or get_now_utc()
    rows = [row for row in db.session.query(Presence).filter(Presence.seen_at >= at - KEPT).all()
            if row.tab != besides]
    active = [row for row in rows if _aware(row.seen_at) >= at - ACTIVE]
    typing = [row for row in active if row.typed_at and _aware(row.typed_at) >= at - TYPING]
    pages = {}
    for row in active:
        entry = pages.setdefault(row.page, {"page": row.page, "people": 0, "typing": 0})
        entry["people"] += 1
        entry["typing"] += row in typing
    return {
        "active": len(active),
        "signed_in": sum(row.signed_in for row in active),
        "visitors": sum(not row.signed_in for row in active),
        "typing": len(typing),
        "pages": sorted(pages.values(), key=lambda entry: (-entry["typing"], -entry["people"], entry["page"])),
        "last_seen_at": max((_aware(row.seen_at) for row in rows), default=None),
    }
