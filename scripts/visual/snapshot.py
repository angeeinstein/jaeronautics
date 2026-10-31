"""Screenshot every page of the portal, and compare two sets of screenshots.

A safety net for changing the look of the site: take a set before, change the
CSS, take a set after, and see exactly which pages changed -- instead of
clicking through every page by hand hoping to spot the one that broke.

    python scripts/visual/snapshot.py shoot  OUT_DIR
    python scripts/visual/snapshot.py compare BEFORE_DIR AFTER_DIR
    python scripts/visual/snapshot.py serve

`serve` only starts that seeded portal (on SNAPSHOT_PORT, 8777) and keeps it
running: for the front end's end-to-end tests (frontend/e2e/), or to click
through the sample data by hand. Sign in as admin@example.org with the
password below.

`shoot` starts the portal on a throwaway SQLite database seeded with members
in the states the pages show differently (new, photo needed, SEPA pending,
ended, returning student, ...) and an admin, with Stripe, the forum and the
update check replaced by offline stand-ins. It then has Playwright (Node, with
the preinstalled Chromium) visit every page at each screen size and
report what it can see is wrong: pages that scroll sideways, elements lying on
top of each other, content sticking out of its box.

Every page is shot at five sizes -- large monitor, 13-inch laptop, tablet
upright and sideways, phone -- with the device set to light mode; the laptop
shots are also taken in dark mode, and the two must be identical -- the portal is dark
whatever the visitor's device prefers.

`compare` writes OUT/report.html with the pages that changed, side by side.

Needs: Pillow, Node with the `playwright` package, and Chromium. Nothing here is
part of the running portal.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("SNAPSHOT_PORT", "8777"))
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "snapshot-password"


# --------------------------------------------------------------------------- app

def build_app(db_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("SECRET_KEY", "snapshot-secret")  # links are signed with it
    from aeronautics_members import app as app_module

    app = app_module.create_app(config_overrides={
        "TESTING": False,
        "SECRET_KEY": "snapshot-secret",
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "SESSION_COOKIE_SECURE": False,
        "PUBLIC_BASE_URL": BASE,
        # Beside the throwaway database, never the repository's storage.
        "TEAM_LOGO_DIR": str(Path(db_path).parent / "team_logos"),
        # The made-up profile pictures lie beside the database: served only from there.
        "FORUM_AVATAR_DIR": str(Path(db_path).parent),
        "FIRMWARE_DIR": str(Path(db_path).parent / "firmware"),
        "DEVICE_CRASH_DIR": str(Path(db_path).parent / "crashes"),
        "BACKUP_STORAGE_DIR": str(Path(db_path).parent / "storage"),
        # In this process, against the throwaway database -- not a second one.
        "BACKUP_RUN_INLINE": True,
        # No forum or Stripe to reach: queued work stays queued.
        "OUTBOX_START_AT_ONCE": False,
    })
    # Who is using the portal (Settings › Updates) is whoever the screenshots
    # themselves happen to be: one fixed answer, so both colour schemes and
    # every run show the same panel -- one visitor typing on the join page.
    from aeronautics_members.services import presence

    presence.report = lambda **_kwargs: {
        "active": 1, "signed_in": 0, "visitors": 1, "typing": 1,
        "pages": [{"page": "/join", "people": 1, "typing": 1}], "last_seen_at": None,
    }
    return app, app_module


UPDATE_LOG = """[INFO] Requested update for branch main.
[STEP] Pausing background jobs for the update
[OK] Background jobs paused; they resume when the update is done.
[STEP] Re-running installer from the latest repository copy
From https://github.com/example/portal
   1a2b3c4..5d6e7f8  main       -> origin/main
[STEP] Fetching the front end built by CI
[INFO] Built from 5d6e7f80.
[WARN] CI is still running for this revision; waiting up to 25 minutes.
[OK] Front end downloaded and checked.
[STEP] Putting the front end in place
[STEP] Installing Python dependencies
Requirement already satisfied: Flask==3.1.1 in ./venv/lib/python3.12/site-packages
Requirement already satisfied: SQLAlchemy==2.0.41 in ./venv/lib/python3.12/site-packages
[STEP] Writing application environment file
[STEP] Initializing database schema
INFO  [alembic.runtime.migration] Running upgrade a7d3e9f1c5b2 -> b8e4f2a6c1d9, team photos
[STEP] Writing systemd service
[STEP] Installing admin-page update runner
[STEP] Writing nginx configuration
[STEP] Reloading system services
[STEP] Verifying deployment
[OK] The portal answers on http://127.0.0.1:8000.
[OK] Update complete.
"""


def _an_update_that_ran(system_update):
    """The Updates page as after an update: its steps, and the whole output to unfold."""
    state_dir = Path(os.environ["DATABASE_URL"].removeprefix("sqlite:///")).parent / "updates"
    state_dir.mkdir(exist_ok=True)
    (state_dir / system_update.LOG_FILENAME).write_text(UPDATE_LOG)
    (state_dir / system_update.STATUS_FILENAME).write_text(json.dumps({
        "state": "completed", "action": "update", "exit_code": 0,
        "started_at": "2026-10-01T09:58:00+02:00", "finished_at": "2026-10-01T10:04:00+02:00",
        "revision_after": "5d6e7f8090a1b2c3", "steps_expected": UPDATE_LOG.count("[STEP]"),
        "log_tail": "\n".join(UPDATE_LOG.splitlines()[-40:]),
    }))
    system_update.UPDATE_STATE_DIR = state_dir


class _MailServer:
    """A mail server that takes every email and sends none."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):  # ehlo, starttls, login, sendmail, quit
        return lambda *args, **kwargs: None


def go_offline(app_module):
    """Stripe, the forum, the git remote and the mail server answer without a network."""
    import types

    from aeronautics_members import mail_utils
    from aeronautics_members.forum_service import DiscourseConnectProvider, ForumProviderError
    from aeronautics_members.services import billing, system_update, workflows

    mail_utils.smtplib = types.SimpleNamespace(**{
        **{name: getattr(mail_utils.smtplib, name) for name in dir(mail_utils.smtplib) if name.startswith("SMTP")},
        "SMTP": _MailServer, "SMTP_SSL": _MailServer,
    })

    def offline(self, *args, **kwargs):
        raise ForumProviderError("Offline for screenshots.")

    DiscourseConnectProvider._request = offline
    # Microsoft 365: signing in works, Graph takes every email and sends none.
    from aeronautics_members.services import microsoft_mail

    def microsoft_offline(method, url, *, data=None, headers=None):
        if urlsplit(url).hostname == "login.microsoftonline.com":
            return 200, {}, b'{"access_token": "offline", "expires_in": 3600}'
        return 202, {}, b"{}"

    microsoft_mail.http = microsoft_offline
    # What joining costs, as Stripe would say it; paying itself stays offline.
    billing._price_shown_cached = lambda: {"id": "price_example", "currency": "eur", "unit_amount": 4000,
                                           "interval": "year", "interval_count": 1}
    system_update.get_remote_version = lambda force=False: None
    _an_update_that_ran(system_update)

    subscriptions = {}  # email -> the Stripe subscription the page should see

    def refresh(member, **kwargs):
        return False, subscriptions.get(member.email_private), None

    app_module.refresh_member_billing_state = refresh
    workflows.refresh_member_billing_state = refresh
    return subscriptions


def seed(app, app_module, subscriptions):
    """Members in the states the pages show differently. Returns the page list."""
    from aeronautics_members.db_models import (
        ForumAvatarSubmission, Member, MemberProfileChangeRequest, Setting, User, db,
    )
    from aeronautics_members.services.forum_import import import_forum_people
    from aeronautics_members.services.identity import build_password_reset_token
    from aeronautics_members.services.privacy import build_account_deletion_token

    now = datetime.now(timezone.utc)
    today = date.today()
    year_end = date(today.year, 12, 31)

    for key, value in {
        "forum_integration_enabled": "True",
        "forum_base_url": "https://forum.example.org",
        "discourse_api_key": "offline",
        "discourse_api_username": "system",
        "discourse_connect_secret": "offline",
    }.items():
        db.session.add(Setting(key=key, value=value))

    def person(key, first, last, *, verified=True, work_verified=True, **fields):
        member_fields = dict(
            salutation="Ms", first_name=first, last_name=last, street="Alte Poststraße",
            house_number="149", postal_code="8020", city="Graz", country="Austria",
            phone_private="+43 664 1234567", email_private=f"{key}@example.org",
            email_work=f"{first.lower()}.{last.lower()}@edu.fh-joanneum.at",
            member_category="student", year_group="LAV25", terms_accepted=True,
            payment_status="paid", is_active=True,
            membership_starts_on=date(today.year, 1, 1), membership_ends_on=year_end,
            renewal_due_on=date(today.year + 1, 1, 1),
        )
        member_fields.update(fields)
        member = Member(**member_fields)
        user = User(email=f"{key}@example.org", forum_username=f"{last}{first[0]}_L25")
        user.set_password(PASSWORD)
        if verified:
            user.email_verified_at = now
        if work_verified and member.email_work:
            member.email_work_verified_at = now
        member.user = user
        db.session.add_all([user, member])
        db.session.flush()
        return member

    admin = User(email="admin@example.org", forum_username="AdminA")
    admin.set_password(PASSWORD)
    admin.email_verified_at = now
    db.session.add(admin)
    db.session.flush()
    admin.grant_role(app_module.get_role("superadmin"))
    admin.grant_role(app_module.get_role("admin"))
    # The association's treasurer: the money and nothing else of the admin area.
    treasurer = User(email="treasurer@example.org", forum_username="TreasurerT")
    treasurer.set_password(PASSWORD)
    treasurer.email_verified_at = now
    db.session.add(treasurer)
    db.session.flush()
    treasurer.grant_role(app_module.get_role("treasurer"))

    # The pictures the submissions point at, so the pages show a picture
    # rather than a broken image: a plain square in a colour per person, kept
    # beside the throwaway database.
    from PIL import Image

    pictures = Path(db.engine.url.database).parent

    def picture_file(name, colour):
        path = pictures / f"{name}.png"
        Image.new("RGB", (240, 240), colour).save(path)
        return dict(storage_path=str(path), content_type="image/png")

    new = person("new", "Nora", "Neumann", verified=False, work_verified=False,
                 payment_status="pending_checkout", is_active=False,
                 membership_starts_on=None, membership_ends_on=None, renewal_due_on=None)
    photo = person("photo-needed", "Paul", "Photo")
    pending = person("photo-pending", "Petra", "Pending")
    db.session.add(ForumAvatarSubmission(user_id=pending.user.id, member_id=pending.id,
                                         status="pending", public_token="snap-pending",
                                         **picture_file("snap-pending", (70, 140, 170))))
    rejected = person("photo-rejected", "Rene", "Rejected")
    db.session.add(ForumAvatarSubmission(user_id=rejected.user.id, member_id=rejected.id,
                                         status="rejected", public_token="snap-rejected",
                                         **picture_file("snap-rejected", (170, 90, 70)),
                                         review_note="Please use a photo of yourself.",
                                         reviewed_at=now))
    active = person("active", "Anna", "Maximilian-Hofstetter-Wallensteiner",
                    title="Dipl.-Ing. (FH)", stripe_customer_id="cus_snap_active",
                    stripe_subscription_id="sub_snap_active")
    db.session.add(ForumAvatarSubmission(user_id=active.user.id, member_id=active.id,
                                         status="approved", public_token="snap-approved",
                                         **picture_file("snap-approved", (90, 160, 100)),
                                         reviewed_at=now))
    person("sepa", "Sepp", "Sepa", payment_status="processing", is_active=False,
           stripe_customer_id="cus_snap_sepa")
    person("ended", "Elke", "Ended", payment_status="canceled", is_active=False,
           membership_starts_on=date(today.year - 1, 3, 1),
           membership_ends_on=date(today.year - 1, 12, 31),
           stripe_customer_id="cus_snap_ended", stripe_subscription_id="sub_snap_ended")
    person("failed", "Fritz", "Failed", payment_status="failed", is_active=False,
           membership_ends_on=date(today.year - 1, 12, 31),
           stripe_customer_id="cus_snap_failed", stripe_subscription_id="sub_snap_failed")
    subscriptions["failed@example.org"] = {"id": "sub_snap_failed", "status": "past_due"}
    person("cancelling", "Carla", "Cancel", cancel_at_period_end=True,
           payment_status="cancel_scheduled",
           stripe_customer_id="cus_snap_cancel", stripe_subscription_id="sub_snap_cancel")

    import_forum_people([{"source_user_id": "9001", "source_username": "BackB_L21",
                          "source_email": "bernd.back@edu.fh-joanneum.at", "year_group": "LAV21"}])
    person("returning", "Bernd", "Back", work_verified=False)

    db.session.add(MemberProfileChangeRequest(
        member_id=photo.id, requested_by_user_id=photo.user.id, requested_salutation="Mr",
        requested_first_name="Paul", requested_last_name="Photograph",
        requested_member_category="student", requested_year_group="LAV24",
        member_note="Typo in my last name.",
    ))
    db.session.commit()

    # A team with a lead who is in it, and one whose lead has not joined yet.
    from aeronautics_members.db_models import TeamMembership
    from aeronautics_members.services import teams

    team_fields = dict(description="We build and fly sounding rockets.", admission_mode="approval",
                       applications_open=True, application_prompt="Why do you want to join?",
                       max_members=None, forum_group=None)
    rocket = teams.create_team(None, slug=None, name="Rocket Team", **team_fields)
    glider = teams.create_team(None, slug=None, name="Glider Team", **team_fields)
    # Both charge: set directly, as checking the price would ask Stripe.
    for charging in (rocket, glider):
        charging.payment_mode, charging.stripe_price_id = "subscription", "price_example"
        charging.period_starts, charging.fee_display = "01.04, 01.10", "€10.00 every 6 months"
    teams.save_team_settings(None, enabled=True, label_singular="", label_plural="")
    # Made-up logos in the shapes real ones come in: one round with a line of
    # text under it, one a tall narrow shield (like a real one that once ran
    # over its team's name) -- neither square, both to be shown whole.
    from io import BytesIO

    from PIL import ImageDraw

    def png(image):
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    round_logo = Image.new("RGBA", (400, 440), (0, 0, 0, 0))
    draw = ImageDraw.Draw(round_logo)
    draw.ellipse([(30, 10), (370, 350)], fill=(16, 24, 30, 255), outline=(0, 223, 255, 255), width=14)
    draw.polygon([(120, 270), (200, 70), (280, 270)], fill=(0, 223, 255, 255))
    draw.rectangle([(70, 380), (330, 420)], fill=(235, 240, 245, 255))
    teams.set_team_logo(None, rocket, png(round_logo))
    shield = Image.new("RGBA", (220, 460), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shield)
    draw.polygon([(10, 10), (210, 10), (210, 300), (110, 450), (10, 300)], fill=(30, 70, 140, 255),
                 outline=(235, 240, 245, 255))
    draw.polygon([(60, 260), (110, 110), (160, 260)], fill=(235, 240, 245, 255))
    teams.set_team_logo(None, glider, png(shield))
    # The team's page: a cover, a formatted text, photos and rules to accept. The
    # pictures are drawn: a sky and a rocket on it, each in other light.
    def scene(size, sky, ground, rocket_at):
        width, height = size
        picture = Image.new("RGB", size)
        paint = ImageDraw.Draw(picture)
        for y in range(height):
            mix = y / height
            paint.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * mix) for a, b in zip(sky, ground)))
        paint.rectangle([(0, int(height * 0.82)), (width, height)], fill=tuple(int(c * 0.45) for c in ground))
        x, top = int(width * rocket_at), int(height * 0.18)
        body = width // 28
        paint.polygon([(x, top), (x - body, top + body * 2), (x + body, top + body * 2)], fill=(235, 240, 245))
        paint.rectangle([(x - body, top + body * 2), (x + body, int(height * 0.82))], fill=(225, 230, 238))
        paint.polygon([(x - body, int(height * 0.7)), (x - body * 2, int(height * 0.82)), (x - body, int(height * 0.82))],
                      fill=(0, 223, 255))
        paint.polygon([(x + body, int(height * 0.7)), (x + body * 2, int(height * 0.82)), (x + body, int(height * 0.82))],
                      fill=(0, 223, 255))
        out = BytesIO()
        picture.save(out, format="JPEG", quality=88)
        return out.getvalue()

    teams.set_team_picture(None, rocket, scene((1800, 900), (18, 32, 58), (64, 96, 128), 0.72))
    for size, sky, ground, at, caption in [
        ((1600, 1200), (30, 50, 90), (210, 140, 90), 0.5, "Launch day at the European Rocketry Challenge"),
        ((1200, 900), (70, 110, 160), (160, 190, 210), 0.35, "Integration in the workshop"),
        ((1200, 900), (12, 18, 36), (40, 60, 110), 0.6, None),
        ((1200, 900), (120, 80, 120), (230, 170, 120), 0.45, "Recovery test"),
        ((1200, 900), (20, 70, 80), (90, 160, 150), 0.55, "Avionics bench"),
        ((1200, 900), (50, 50, 60), (150, 150, 160), 0.4, "Static fire"),
    ]:
        teams.add_team_photo(None, rocket, scene(size, sky, ground, at), caption)
    teams.update_team_page(
        None, rocket,
        about="We design, build and fly **sounding rockets**, and take part in the "
              "[European Rocketry Challenge](https://euroc.pt).\n\n"
              "# What members do\n\n"
              "New members start in one of the sub-teams:\n\n"
              "- **Structures**: airframe, fins and the nose cone\n"
              "- **Propulsion**: the motor and its tests\n"
              "- **Avionics**: flight computer and telemetry\n"
              "- **Recovery**: parachutes, and finding the rocket again\n\n"
              "# When we meet\n\n"
              "Every Tuesday at 18:00 in the workshop. Come along before you apply.",
    )
    db.session.add(TeamMembership(team=rocket, user=active.user, status=teams.ACTIVE, started_at=now,
                                  payment_mode="subscription", stripe_subscription_id="sub_example_1",
                                  paid_until=date(2027, 3, 31), payment_state="paid"))
    teams.grant_team_role(None, rocket, active.user, teams.ROLE_LEAD)
    carla = Member.query.filter_by(email_private="cancelling@example.org").one().user
    db.session.add(TeamMembership(team=rocket, user=carla, status=teams.ACTIVE, started_at=now,
                                  payment_mode="subscription", stripe_subscription_id="sub_example_2",
                                  paid_until=date(2027, 3, 31), payment_state="paid", ends_on=date(2027, 3, 31)))
    bernd = Member.query.filter_by(email_private="returning@example.org").one().user
    # Approved by the glider team, the fee not paid yet.
    db.session.add(TeamMembership(team=glider, user=bernd, status=teams.APPROVED, applied_at=now,
                                  approved_at=now, payment_mode="subscription"))
    application = TeamMembership(team=rocket, user=bernd, status=teams.APPLIED, applied_at=now,
                                 application_text="I built model rockets at school\nand would love to help.")
    db.session.add(application)
    db.session.flush()
    teams.add_note(active.user, rocket, bernd, "Met him at the open day. Knows CATIA.")
    teams.set_access_list_enabled(None, rocket, True)
    teams.update_access_list(None, rocket, recipients="facility@uni.example\nporter@uni.example",
                             dates="15.10, 15.03", auto_send=True)
    # As if a list went out in March: Carla has joined since, Dieter has left.
    from aeronautics_members.db_models import TeamAccessListSend

    db.session.add(TeamAccessListSend(team=rocket, sent_on=date(2026, 3, 15), automatic=True, entries=[
        {"user_id": active.user.id, "name": "Anna Maximilian-Hofstetter-Wallensteiner",
         "email": "anna.maximilian-hofstetter-wallensteiner@edu.fh-joanneum.at"},
        {"user_id": 99999, "name": "Dieter Departed", "email": "dieter.departed@edu.fh-joanneum.at"},
    ]))
    # Money: two payments, one partly refunded, one transfer, and where it goes.
    from aeronautics_members.db_models import Payment, TeamPayout

    rocket.bank_account_holder, rocket.bank_iban, rocket.bank_bic = (
        "Joanneum Aeronautics Rocket Team", "AT611904300234573201", "BKAUATWW")
    for index, (payer, refunded) in enumerate(((active.user, 0), (carla, 500))):
        db.session.add(Payment(purpose="team", team_id=rocket.id, user_id=payer.id, amount_cents=1000,
                               currency="eur", covers_until=date(2027, 3, 31), refunded_cents=refunded,
                               stripe_invoice_id=f"in_example_{index}"))
    db.session.add(TeamPayout(team_id=rocket.id, amount_cents=500, paid_on=date(2026, 10, 1),
                              reference="Teambeiträge Rocket Team bis 01.10.2026",
                              account_holder=rocket.bank_account_holder, iban=rocket.bank_iban))
    db.session.commit()
    # Credit: on, with a top-up through Stripe, cash handed over and a coffee.
    from aeronautics_members.services import credit
    from aeronautics_members.services.settings import set_setting_value

    set_setting_value(credit.SETTING_ENABLED, "True")
    set_setting_value(credit.SETTING_PRODUCT, "prod_example_credit")
    top_up = Payment(purpose="credit", user_id=active.user.id, amount_cents=1000, currency="eur",
                     stripe_payment_intent_id="pi_example_credit", stripe_invoice_id="in_example_credit")
    db.session.add(top_up)
    db.session.flush()
    credit._book(credit._account(active.user.id), credit.TOP_UP, 1000, "Top-up", payment=top_up)
    admin_user = User.query.filter_by(email="admin@example.org").one()
    credit.book_cash(admin_user, active.user, 500, note="Handed over at the office")
    from aeronautics_members.services import credit_sales

    coffee = credit_sales.add_item(admin_user, name="Coffee", price_cents=120, icon="coffee")
    credit_sales.add_item(admin_user, name="Tea", price_cents=100, icon="tea")
    beer = credit_sales.add_item(active.user, team=rocket, name="Beer", price_cents=200, icon="beer")
    credit_sales.add_item(active.user, team=rocket, name="Mate", price_cents=250, icon="mate")
    credit_sales.sell(active.user, coffee, by=admin_user)
    credit.book_cash(admin_user, carla, 1500)
    carla_beer = credit_sales.sell(carla, beer, by=admin_user)
    db.session.commit()

    # Stripe's book, as fetched (services/stripe_books.py): the payments above,
    # a membership, the webshop, Stripe's own fee -- and one difference.
    from aeronautics_members.db_models import MoneyDifference, StripeBooking, StripeBooksState

    fixed = datetime(2026, 10, 6, 7, 58, tzinfo=timezone.utc)  # the same in every run
    books = [("membership", 6000, 115, None), ("other", 2500, 63, None), ("credit", 1000, 30, top_up.id)]
    books += [("team", payment.amount_cents, 35, payment.id) for payment in Payment.query.filter_by(purpose="team")]
    for index, (product, amount, fee, payment_id) in enumerate(books):
        db.session.add(StripeBooking(stripe_id=f"txn_example_{index}", type="charge", amount_cents=amount,
                                     fee_cents=fee, net_cents=amount - fee, currency="eur",
                                     booked_at=fixed - timedelta(days=index + 1), charge_id=f"ch_example_{index}",
                                     product=product, payment_id=payment_id, fetched_at=fixed))
        if payment_id is not None:
            db.session.get(Payment, payment_id).fee_cents = fee
    db.session.add(StripeBooking(stripe_id="txn_example_fee", type="stripe_fee", amount_cents=-20, fee_cents=0,
                                 net_cents=-20, currency="eur", booked_at=fixed - timedelta(days=2),
                                 fetched_at=fixed))
    balance = sum(amount - fee for _product, amount, fee, _payment in books) - 20
    db.session.add(StripeBooksState(id=1, fetched_through=fixed, fetched_at=fixed, stripe_balance_cents=balance,
                                    books_balance_cents=balance, checked_at=fixed))
    db.session.add(MoneyDifference(key="team:missing:ch_example_gone", product="team",
                                   what="Paid in Stripe, missing in the portal", reference="in_example_gone",
                                   stripe_amount_cents=1000, who="payer@example.org",
                                   first_seen_at=fixed - timedelta(hours=3), last_seen_at=fixed))
    db.session.commit()

    # Messages: two to the admins (one answered), one to the Rocket Team's
    # leads; an announcement half sent, one team mailing sent.
    from aeronautics_members.db_models import ContactMessage, MailingRecipient
    from aeronautics_members.services import mailings

    now = datetime.now(timezone.utc)
    db.session.add_all([
        ContactMessage(topic="membership", sender_name="Vera Visitor", sender_email="vera@example.net",
                       subject="Can alumni join?", body="I finished in 2019. Can I still become a member?\n\nVera",
                       context={"page": "/join"}, delivered_at=now, created_at=now - timedelta(hours=3)),
        ContactMessage(topic="portal", sender_user_id=active.user.id, sender_name="Anna Active",
                       sender_email="active@example.org", subject="Forum password",
                       body="The forum asks for a password I never set.",
                       context={"account_id": active.user.id, "membership": "active", "page": "/account/forum"},
                       delivered_at=now, created_at=now - timedelta(days=2), done_at=now - timedelta(days=1),
                       done_by_user_id=admin_user.id),
        ContactMessage(team_id=rocket.id, sender_user_id=carla.id, sender_name="Carla Member",
                       sender_email=carla.email, subject="Workshop on Monday?",
                       body="Is the workshop open on Monday evening? I would like to see the engine test.",
                       context={"account_id": carla.id, "membership": "active"}, delivered_at=now,
                       created_at=now - timedelta(hours=5)),
    ])
    db.session.commit()
    # The answered one was answered from the portal (Admin › Messages, Reply),
    # from the support address, with a copy to it (Settings › Mailings). The
    # mail server is a stand-in (go_offline): emails "go out" and reach nobody.
    from aeronautics_members.db_models import ContactReply, MailAccount

    db.session.add_all([
        MailAccount(account_key="office", host="smtp.example.org", port=465, username="office@example.org",
                    password="offline", from_name="Joanneum Aeronautics"),
        MailAccount(account_key="support", host="smtp.example.org", port=465, username="u-4711",
                    password="offline", from_email="support@example.org", from_name="Joanneum Aeronautics"),
        # A Microsoft 365 mailbox beside them, through the portal's app and certificate.
        MailAccount(account_key="noreply", kind=MailAccount.MICROSOFT, from_email="noreply@example.org",
                    from_name="Joanneum Aeronautics"),
    ])
    from aeronautics_members.services import microsoft_mail

    microsoft_mail.save_app(None, tenant_id="example.onmicrosoft.com",
                            client_id="11111111-2222-3333-4444-555555555555")
    microsoft_mail.make_certificate(None)
    db.session.add_all([Setting(key="contact_reply_sender", value="support"),
                        Setting(key="contact_reply_copy", value="1")])

    answered = ContactMessage.query.filter_by(subject="Forum password").one()
    db.session.add(ContactReply(message=answered, author_user_id=admin_user.id, account_key="support",
                                body="Use *Forgot password* on the forum's sign-in page, with your private address."
                                     "\n\nThe link comes within a minute.",
                                created_at=now - timedelta(days=1), sent_at=now - timedelta(days=1)))
    db.session.commit()
    announcement = mailings.announce(admin_user, kind="news", audience={"scope": "all"}, subject="Summer party",
                                     body="We meet on **Friday** at the institute.\n\n- Food\n- Drinks")
    for recipient in announcement.recipients[: max(1, len(announcement.recipients) // 2)]:
        recipient.status, recipient.sent_at, recipient.attempts = "sent", now, 1
    team_mailing = mailings.write_to_team(active.user, rocket, subject="Engine test on Saturday",
                                          body="We test the new engine on Saturday at 10:00. Bring gloves.")
    for recipient in team_mailing.recipients:
        recipient.status, recipient.sent_at, recipient.attempts = "sent", now, 1
    team_mailing.status, team_mailing.finished_at = "sent", now
    db.session.commit()
    assert MailingRecipient.query.count()

    # Devices: one that spoke a moment ago, one never seen, one switched off.
    from aeronautics_members.services import devices as devices_service

    fridge, _key = devices_service.add(admin_user, name="Fridge reader", type="test", place="Workshop, beside the fridge")
    devices_service.seen(fridge, address="10.0.0.21", firmware="0.3.1", now=now)
    devices_service.report(fridge, event_id="boot-1", name="ping", data={"n": 1})
    for path, status, code in (("/api/v1/device", 200, None), ("/api/v1/device/resources/hello", 200, None),
                               ("/api/v1/device/resources/items", 404, "device_resource_unknown")):
        devices_service.record_request(fridge, method="GET", path=path, status=status, code=code,
                                       address="10.0.0.21", now=now)
    devices_service.add(admin_user, name="Coffee machine", type="test")
    door, _key = devices_service.add(admin_user, name="Tool cabinet", type="test", place="Hangar")
    devices_service.change(admin_user, door, enabled=False)

    # Cards and a sales point (docs/cards-plan.md): the member's student card,
    # last used at the fridge, and a key fob switched off; Carla's beer paid
    # with her student card there, and an old card of hers blocked.
    from aeronautics_members.services import cards as cards_service

    sales_point, _key = devices_service.add(admin_user, name="Rocket fridge", type="sales_point", owner=rocket,
                                            place="Workshop", settings={"items": [beer.id]})
    devices_service.seen(sales_point, address="10.0.0.22", firmware="1.0.0", hardware="24:6F:28:AA:BB:CC",
                         now=now)
    devices_service.add(admin_user, type="sales_point", owner=rocket)
    # A card tapped there that nobody has linked yet, its QR code opened on a
    # phone: the page it leads to. Lasts the whole run, not its 10 minutes.
    from aeronautics_members.services import card_links

    qr_link = card_links.offer(sales_point, "04C0FFEE0000A1")
    qr_link.expires_at = now + timedelta(days=1)
    _opened, qr_claim = card_links.open_link(qr_link.token)
    student_card = cards_service.link(active.user, "04A1B2C3D4E5F6", name="Student card", by=active.user,
                                      how=cards_service.BY_PHONE)
    fob = cards_service.link(active.user, "04112233445566", name="Key fob", by=admin_user,
                             how=cards_service.BY_ADMIN_PHONE)
    cards_service.disable(active.user, fob)
    carlas = cards_service.link(carla, "04998877665544", name="Student card", by=carla,
                                how=cards_service.BY_PHONE)
    old_card = cards_service.link(carla, "0499887766554433AABB", name="Old card", by=carla,
                                  how=cards_service.BY_PHONE)
    cards_service.block(admin_user, old_card)
    carla_beer.channel, carla_beer.booked_by, carla_beer.card, carla_beer.device = (
        credit_sales.READER, None, carlas, sales_point)
    for card in (student_card, carlas):
        card.last_used_at, card.last_used_device_id = now, sales_point.id

    # One device for everything (docs/devices-plan.md): what the boards say they
    # are, a firmware version tried on the fridge first -- the Fridge reader
    # could not install it -- and a device waiting to be paired.
    from aeronautics_members.services import device_firmware, device_pairing, firmware_image

    devices_service.seen(sales_point, board="jae-terminal-s3", types=["sales_point", "test"], outputs=2, now=now)
    # The fridge's lock opens for five seconds after any sale; output 2 rings a bell for a beer.
    devices_service.change(admin_user, sales_point, settings={"actions": {"on": True, "lines": [
        {"item": None, "output": 1, "seconds": 5}, {"item": beer.id, "output": 2, "seconds": 0.5}]}})
    devices_service.seen(fridge, board="jae-terminal-s3", types=["sales_point", "test"],
                         hardware="98:A3:16:E5:06:3C", now=now)
    release = device_firmware.upload(admin_user, firmware_image.example("1.1.0"), file_name="jae-terminal.bin",
                                     notes="Shows the balance after each sale")
    device_firmware.choose_for_device(admin_user, sales_point, release)
    device_firmware.choose_for_device(admin_user, fridge, release)
    device_firmware.reported(fridge, result="failed", version="1.1.0", detail="Not enough space for the update")
    # What a fresh board is flashed with (the Firmware page's New device tab).
    from aeronautics_members.services import device_factory

    device_factory.upload(admin_user, firmware_image.factory_example("1.0.0"), file_name="jae-terminal-factory.bin")
    # A crash the fridge reported: one with its dump, by the version tried on it; one older, of a build never uploaded.
    import hashlib

    from aeronautics_members.services import device_crashes

    for seed, build, reason, days in ((b"a", release.elf_sha256[:16], "panic", 0),
                                      (b"b", "0123456789abcdef", "task_watchdog", 3)):
        dump = b"\x7fELF\x01\x01\x01" + b"\0" * 9 + seed * 41000
        crash, _upload = device_crashes.report(
            fridge, crash_id=hashlib.sha256(dump).hexdigest(), build=build, task="portal", pc="0x4209F87C",
            backtrace=["0x4209F879", "0x4209FB05", "0x4209D2EA", "0x42008809", "0x4037D5A1"],
            backtrace_corrupted=False, reset_reason=reason, size=len(dump), firmware="1.0.0")
        crash.received_at = now - timedelta(days=days, hours=2)
        if days == 0:
            device_crashes.store_file(fridge, crash.crash_id, dump)
    pairing, _secret = device_pairing.start(hardware="24:6F:28:12:34:56", firmware="1.0.0", board="jae-terminal-s3",
                                            types=["sales_point", "test"], address="10.0.0.40", now=now)
    # Waiting all day: the screenshots of its page come later than ten minutes.
    pairing.expires_at = now + timedelta(days=1)

    # Attendance (docs/attendance-plan.md): on for the Rocket Team, with its
    # two readers; Anna's stays this month -- one not checked out, one
    # corrected by hand -- and Carla checked in now. One stay of Anna's at the
    # association's reader, kept while the association's attendance is off.
    from aeronautics_members.db_models import AttendanceStay

    rocket.attendance_enabled = True
    hangar, _key = devices_service.add(admin_user, name="Hangar door", type="attendance", owner=rocket,
                                       place="Hangar")
    workshop, _key = devices_service.add(admin_user, name="Workshop", type="attendance", owner=rocket,
                                         place="Workshop")
    clubroom, _key = devices_service.add(admin_user, name="Club room", type="attendance", place="Club room")
    morning = now.replace(hour=7, minute=12, second=41, microsecond=0)

    def stay(user, team, days, hours, minutes=0, *, at=hangar, out=workshop, closed=False, corrected=False,
             open_=False):
        started = morning - timedelta(days=days)
        record = AttendanceStay(user_id=user.id, team_id=team.id if team else None, started_at=started,
                                in_device_id=at.id, created_at=started)
        if closed:
            record.closed_at = min(started + timedelta(hours=20), now)
        elif not open_:
            record.ended_at = started + timedelta(hours=hours, minutes=minutes, seconds=17)
            record.out_device_id = out.id if not corrected else None
        if corrected:
            record.corrected_by_user_id, record.corrected_at = active.user.id, now - timedelta(days=1)
        db.session.add(record)

    for days, hours, minutes in ((1, 3, 25), (3, 5, 2), (6, 2, 48), (8, 4, 0)):
        stay(active.user, rocket, days, hours, minutes)
    # Not checked out on the day of her newest stay, so the two are always in the same month.
    stay(active.user, rocket, 1 - 5 / 24, 0, closed=True)
    stay(active.user, rocket, 5, 6, 30, corrected=True)
    stay(carla, rocket, 2, 4, 12, at=workshop, out=hangar)
    stay(carla, rocket, 4, 1, 50)
    # Checked in today, never before the day began: the night would close it.
    from aeronautics_members.services import attendance

    since = max(now - timedelta(hours=2),
                attendance.day_start(attendance.local(now).date()).replace(tzinfo=timezone.utc) + timedelta(minutes=1))
    db.session.add(AttendanceStay(user_id=carla.id, team_id=rocket.id, started_at=since, in_device_id=workshop.id,
                                  created_at=since))
    stay(active.user, None, 9, 2, 15, at=clubroom, out=clubroom)
    db.session.commit()

    reset_token = build_password_reset_token(new.user)
    delete_token = build_account_deletion_token(active.user)
    db.session.commit()

    def member_pages(key, *extra):
        return [{"name": f"account--{key}", "user": f"{key}@example.org", "path": "/account"},
                *[{**page, "user": f"{key}@example.org"} for page in extra]]

    return [
        {"name": "public--landing", "path": "/"},
        {"name": "public--signup", "path": "/join"},
        {"name": "public--signup-errors", "path": "/join", "submit": "button[type=submit]"},
        # The steps after the first, as each kind of member gets them.
        {"name": "public--signup-student", "path": "/join", "open": "[role=radio]:has-text('Student')"},
        {"name": "public--signup-student-errors", "path": "/join", "open": "[role=radio]:has-text('Student')",
         "submit": "button[type=submit]"},
        {"name": "public--signup-alumni", "path": "/join", "open": "[role=radio]:has-text('Alumni')"},
        {"name": "public--signup-company", "path": "/join", "open": "[role=radio]:has-text('Company')"},
        {"name": "public--signup-staff", "path": "/join", "open": "[role=radio]:has-text('Staff')"},
        {"name": "public--login", "path": "/login"},
        {"name": "public--forgot-password", "path": "/forgot-password"},
        {"name": "public--reset-password", "path": f"/reset-password/{reset_token}"},
        {"name": "public--legal", "path": "/legal"},
        {"name": "public--legal-statutes", "path": "/legal/statutes"},
        {"name": "public--thank-you", "path": "/thank-you?method=checkout&phase=prorated"},
        {"name": "public--thank-you-free", "path": "/thank-you?method=checkout&phase=free_period"},
        {"name": "public--cancel", "path": "/cancel"},
        {"name": "public--not-found", "path": "/no-such-page"},
        *member_pages("new", {"name": "account--profile-unconfirmed", "path": "/account/profile"}),
        *member_pages("photo-needed", {"name": "forum--photo-needed", "path": "/forum"}),
        *member_pages("photo-pending"),
        *member_pages("photo-rejected"),
        *member_pages("active",
                      {"name": "account--change-password", "path": "/change-password"},
                      {"name": "account--profile", "path": "/account/profile"},
                      {"name": "account--profile-edit", "path": "/account/profile",
                       "open": "section[aria-label='Contact details'] button:has-text('Edit')"},
                      {"name": "account--membership", "path": "/account/membership"},
                      {"name": "account--forum", "path": "/account/forum"},
                      {"name": "account--data", "path": "/account/data"},
                      {"name": "account--credit", "path": "/account/credit"},
                      {"name": "account--cards", "path": "/account/cards"},
                      {"name": "account--attendance", "path": "/account/attendance"},
                      {"name": "account--attendance-ask", "path": "/account/attendance",
                       "click": "button:has-text('Ask for a correction')"},
                      # Saving the profile: the success message with its tick.
                      {"name": "account--saved", "path": "/account/profile",
                       "open": "section[aria-label='Contact details'] button:has-text('Edit')",
                       "submit": "section[aria-label='Contact details'] button[type=submit]"},
                      {"name": "account--delete-confirm", "path": f"/account/delete/{delete_token}"}),
        *member_pages("sepa", {"name": "account--membership-sepa", "path": "/account/membership"}),
        *member_pages("ended", {"name": "account--membership-ended", "path": "/account/membership"}),
        *member_pages("failed"),
        *member_pages("cancelling"),
        *member_pages("returning"),
        {"name": "account--create-profile", "user": "admin@example.org", "path": "/account/create-membership"},
        {"name": "account--staff-no-membership", "user": "admin@example.org", "path": "/account"},
        {"name": "admin--dashboard", "user": "admin@example.org", "path": "/admin"},
        {"name": "admin--accounts", "user": "admin@example.org", "path": "/admin/accounts"},
        {"name": "admin--account-detail", "user": "admin@example.org", "path": f"/admin/accounts/{active.user.id}"},
        {"name": "admin--account-detail-ended", "user": "admin@example.org",
         "path": f"/admin/accounts/{Member.query.filter_by(email_private='ended@example.org').one().user_id}"},
        {"name": "admin--reviews", "user": "admin@example.org", "path": "/admin/reviews"},
        {"name": "admin--logs", "user": "admin@example.org", "path": "/admin/logs"},
        {"name": "admin--settings", "user": "admin@example.org", "path": "/admin/settings"},
        {"name": "admin--settings-health", "user": "admin@example.org", "path": "/admin/settings/health"},
        {"name": "admin--settings-updates", "user": "admin@example.org", "path": "/admin/settings/updates"},
        {"name": "admin--settings-updates-history", "user": "admin@example.org",
         "path": "/admin/settings/updates#history"},
        {"name": "admin--teams", "user": "admin@example.org", "path": "/admin/teams"},
        {"name": "admin--team-detail", "user": "admin@example.org", "path": "/teams/rocket-team/manage/details"},
        {"name": "admin--team-fee", "user": "admin@example.org", "path": "/teams/rocket-team/manage/fee"},
        {"name": "admin--team-roles", "user": "admin@example.org", "path": "/teams/rocket-team/manage/roles"},
        {"name": "admin--team-new", "user": "admin@example.org", "path": "/admin/teams/new"},
        {"name": "teams--home", "user": "active@example.org", "path": "/teams"},
        {"name": "teams--home-applicant", "user": "returning@example.org", "path": "/teams"},
        # A team's fee asks card or SEPA before Stripe's page (a membership does too, but not in
        # the year's free months, which the screenshots' date may fall in).
        {"name": "teams--pay-method", "user": "returning@example.org", "path": "/teams/glider-team/about",
         "open": "button:has-text('Pay and join')"},
        {"name": "teams--team-page", "user": "active@example.org", "path": "/teams/rocket-team"},
        {"name": "teams--about", "user": "photo-needed@example.org", "path": "/teams/rocket-team/about"},
        {"name": "teams--leave", "user": "active@example.org", "path": "/teams/rocket-team/leave"},
        {"name": "teams--manage", "user": "active@example.org", "path": "/teams/rocket-team/manage"},
        {"name": "teams--manage-page", "user": "active@example.org", "path": "/teams/rocket-team/manage#manage-page"},
        {"name": "teams--manage-applying", "user": "active@example.org", "path": "/teams/rocket-team/manage#manage-applying"},
        {"name": "teams--manage-access-list", "user": "active@example.org", "path": "/teams/rocket-team/manage#manage-access-list"},
        {"name": "teams--manage-roles", "user": "active@example.org", "path": "/teams/rocket-team/manage#manage-roles"},
        {"name": "teams--person", "user": "active@example.org", "path": f"/teams/rocket-team/manage/people/{bernd.id}"},
        {"name": "teams--access-list", "user": "active@example.org", "path": "/teams/rocket-team/manage/access-list"},
        {"name": "teams--money", "user": "active@example.org", "path": "/teams/rocket-team/money"},
        {"name": "teams--money-treasurer", "user": "admin@example.org", "path": "/teams/rocket-team/money"},
        {"name": "admin--money", "user": "admin@example.org", "path": "/admin/money"},
        {"name": "admin--money-team", "user": "admin@example.org", "path": "/admin/money/rocket-team"},
        {"name": "admin--money-team-history", "user": "admin@example.org", "path": "/admin/money/rocket-team#history"},
        {"name": "admin--dashboard-treasurer", "user": "treasurer@example.org", "path": "/admin"},
        {"name": "admin--credit", "user": "admin@example.org", "path": "/admin/credit"},
        {"name": "admin--credit-holder", "user": "treasurer@example.org", "path": f"/admin/credit/{active.user.id}"},
        {"name": "admin--settings-credit", "user": "admin@example.org", "path": "/admin/settings/credit"},
        {"name": "teams--sales", "user": "active@example.org", "path": "/teams/rocket-team/manage/sales"},
        {"name": "teams--sales-points", "user": "active@example.org",
         "path": "/teams/rocket-team/manage/sales#sales-points"},
        # What a sale switches: behind the reader's Actions button.
        {"name": "teams--sales-actions", "user": "active@example.org",
         "path": "/teams/rocket-team/manage/sales#sales-points", "open": "button:has-text('Actions')"},
        {"name": "admin--sales", "user": "admin@example.org", "path": "/admin/sales"},
        {"name": "public--contact", "path": "/contact"},
        {"name": "public--contact-signed-in", "user": "active@example.org", "path": "/contact"},
        {"name": "public--unsubscribe", "path": f"/unsubscribe/{mailings.unsubscribe_token(active.user)}"},
        # The phone that opened the reader's QR code keeps its claim in the browser.
        {"name": "public--link-card", "path": f"/link/{qr_link.token}",
         "storage": {f"card-link:{qr_link.token}": qr_claim}},
        {"name": "public--link-card-signed-in", "user": "active@example.org", "path": f"/link/{qr_link.token}",
         "storage": {f"card-link:{qr_link.token}": qr_claim}},
        {"name": "admin--messages", "user": "admin@example.org", "path": "/admin/messages"},
        {"name": "admin--messages-reply", "user": "admin@example.org", "path": "/admin/messages",
         "click": "button:has-text('Reply')"},
        {"name": "admin--messages-all", "user": "admin@example.org", "path": "/admin/messages",
         "click": "label:has-text('All')"},
        {"name": "admin--announcements", "user": "admin@example.org", "path": "/admin/announcements"},
        {"name": "admin--announcement", "user": "admin@example.org",
         "path": f"/admin/announcements/{announcement.id}"},
        {"name": "admin--settings-general", "user": "admin@example.org", "path": "/admin/settings/general"},
        {"name": "admin--settings-email", "user": "admin@example.org", "path": "/admin/settings/email"},
        {"name": "admin--settings-email-sending", "user": "admin@example.org",
         "path": "/admin/settings/email#sending"},
        {"name": "admin--settings-email-accounts", "user": "admin@example.org",
         "path": "/admin/settings/email#accounts"},
        {"name": "admin--settings-email-test", "user": "admin@example.org", "path": "/admin/settings/email#test"},
        {"name": "admin--settings-devices", "user": "admin@example.org", "path": "/admin/settings/devices"},
        {"name": "admin--settings-device", "user": "admin@example.org",
         "path": f"/admin/settings/devices/{fridge.id}"},
        {"name": "admin--settings-device-activity", "user": "admin@example.org",
         "path": f"/admin/settings/devices/{fridge.id}#activity"},
        {"name": "admin--settings-device-crashes", "user": "admin@example.org",
         "path": f"/admin/settings/devices/{fridge.id}#crashes"},
        {"name": "admin--settings-sales-point", "user": "admin@example.org",
         "path": f"/admin/settings/devices/{sales_point.id}"},
        {"name": "admin--settings-device-pair", "user": "admin@example.org",
         "path": f"/admin/settings/devices/pair?code={device_pairing.shown(pairing.code)}"},
        {"name": "admin--settings-firmware", "user": "admin@example.org", "path": "/admin/settings/devices/firmware"},
        {"name": "admin--settings-firmware-new", "user": "admin@example.org",
         "path": "/admin/settings/devices/firmware#new"},
        {"name": "admin--cards", "user": "admin@example.org", "path": "/admin/cards"},
        {"name": "admin--attendance", "user": "admin@example.org", "path": "/admin/attendance"},
        {"name": "teams--devices", "user": "active@example.org", "path": "/teams/rocket-team/manage/devices"},
        {"name": "teams--attendance", "user": "active@example.org", "path": "/teams/rocket-team/manage/attendance"},
        {"name": "teams--attendance-person", "user": "active@example.org",
         "path": "/teams/rocket-team/manage/attendance", "click": "table[aria-label='Hours per person'] button:has-text('Anna')"},
        {"name": "admin--account-cards", "user": "admin@example.org",
         "path": f"/admin/accounts/{active.user.id}#cards"},
        {"name": "teams--messages", "user": "active@example.org", "path": "/teams/rocket-team/manage/messages"},
    ]


def serve(app):
    import logging

    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    server = make_server("127.0.0.1", PORT, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ------------------------------------------------------------------------ shoot

def shoot(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        app, app_module = build_app(Path(tmp) / "snapshot.db")
        with app.app_context():
            from aeronautics_members.db_models import db

            db.create_all()
            subscriptions = go_offline(app_module)
            pages = seed(app, app_module, subscriptions)
        server = serve(app)
        try:
            manifest = out_dir / "pages.json"
            manifest.write_text(json.dumps({"base": BASE, "password": PASSWORD, "pages": pages}, indent=2))
            env = {**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "/opt/node22/lib/node_modules")}
            subprocess.run(["node", str(HERE / "shoot.js"), str(manifest), str(out_dir)], check=True, env=env)
        finally:
            server.shutdown()
    compare_color_schemes(out_dir)
    print_findings(out_dir)


def compare_color_schemes(out_dir):
    """The device's light/dark preference must not change a single pixel."""
    from PIL import Image, ImageChops

    differing = []
    for dark in sorted(Path(out_dir).glob("*-dark.png")):
        light = dark.with_name(dark.name.replace("-dark.png", ".png"))
        if not light.exists():
            continue
        a, b = Image.open(light).convert("RGB"), Image.open(dark).convert("RGB")
        if a.size != b.size or ImageChops.difference(a, b).getbbox():
            differing.append(light.name.split("__")[0])
    findings_path = Path(out_dir) / "findings.json"
    findings = json.loads(findings_path.read_text())
    findings["_color_scheme_differs"] = differing
    findings_path.write_text(json.dumps(findings, indent=2))


def print_findings(out_dir):
    findings = json.loads((Path(out_dir) / "findings.json").read_text())
    differs = findings.pop("_color_scheme_differs", [])
    print(f"\nPages that look different with the device in dark mode: {differs or 'none'}")
    for shot, issues in sorted(findings.items()):
        if not any(issues.values()):
            continue
        print(f"\n{shot}")
        for kind, items in issues.items():
            for item in items[:8]:
                print(f"  {kind}: {item}")
            if len(items) > 8:
                print(f"  {kind}: ... and {len(items) - 8} more")


# ---------------------------------------------------------------------- compare

def compare(before_dir, after_dir):
    from PIL import Image, ImageChops

    before_dir, after_dir = Path(before_dir), Path(after_dir)
    rows = []
    for after in sorted(after_dir.glob("*.png")):
        before = before_dir / after.name
        if not before.exists():
            rows.append((after.name, "new", None))
            continue
        a, b = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
        if a.size == b.size and not ImageChops.difference(a, b).getbbox():
            continue
        rows.append((after.name, "changed", before))
    report = after_dir / "report.html"
    cells = []
    for name, state, before in rows:
        before_img = f'<img src="{before.resolve()}">' if before else ""
        after_img = f'<img src="{(after_dir / name).resolve()}">'
        cells.append(
            f"<h3>{name} ({state})</h3><div class=row>"
            f"<div><p>before</p>{before_img}</div><div><p>after</p>{after_img}</div></div>"
        )
    cells = "\n".join(cells)
    report.write_text(
        "<!doctype html><meta charset=utf-8><title>Changed pages</title>"
        "<style>body{font-family:sans-serif;background:#222;color:#eee}"
        ".row{display:flex;gap:12px}.row div{flex:1}img{width:100%;border:1px solid #555}</style>"
        f"<h1>{len(rows)} changed</h1>{cells or '<p>Nothing changed.</p>'}"
    )
    print(f"{len(rows)} screenshots changed; see {report}")
    for name, state, _ in rows:
        print(f"  {state}: {name}")


def serve_forever():
    import time

    with tempfile.TemporaryDirectory() as tmp:
        app, app_module = build_app(Path(tmp) / "snapshot.db")
        with app.app_context():
            from aeronautics_members.db_models import db

            db.create_all()
            subscriptions = go_offline(app_module)
            seed(app, app_module, subscriptions)
        server = serve(app)
        print(f"Serving the seeded portal on {BASE} (admin@example.org / {PASSWORD}).", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        serve_forever()
    elif len(sys.argv) >= 3 and sys.argv[1] == "shoot":
        shoot(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(2)
