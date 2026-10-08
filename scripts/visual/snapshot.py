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
from datetime import date, datetime, timezone
from pathlib import Path

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
        "BACKUP_STORAGE_DIR": str(Path(db_path).parent / "storage"),
        # In this process, against the throwaway database -- not a second one.
        "BACKUP_RUN_INLINE": True,
    })
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


def go_offline(app_module):
    """Stripe, the forum and the git remote answer without a network."""
    from aeronautics_members.forum_service import DiscourseConnectProvider, ForumProviderError
    from aeronautics_members.services import system_update, workflows

    def offline(self, *args, **kwargs):
        raise ForumProviderError("Offline for screenshots.")

    DiscourseConnectProvider._request = offline
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
    # text under it, one a shield -- neither quite square, both to be shown whole.
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
    shield = Image.new("RGBA", (360, 420), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shield)
    draw.polygon([(20, 20), (340, 20), (340, 220), (180, 400), (20, 220)], fill=(30, 70, 140, 255),
                 outline=(235, 240, 245, 255))
    draw.polygon([(110, 230), (180, 90), (250, 230)], fill=(235, 240, 245, 255))
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
        {"name": "public--login", "path": "/login"},
        {"name": "public--forgot-password", "path": "/forgot-password"},
        {"name": "public--reset-password", "path": f"/reset-password/{reset_token}"},
        {"name": "public--legal", "path": "/legal"},
        {"name": "public--legal-statutes", "path": "/legal/statutes"},
        {"name": "public--signup-legal-dialog", "path": "/join", "click": "label a[href='/legal/statutes']"},
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
        {"name": "admin--settings-updates", "user": "admin@example.org", "path": "/admin/settings/updates"},
        {"name": "admin--teams", "user": "admin@example.org", "path": "/admin/teams"},
        {"name": "admin--team-detail", "user": "admin@example.org", "path": "/admin/teams/rocket-team"},
        {"name": "admin--team-new", "user": "admin@example.org", "path": "/admin/teams/new"},
        {"name": "teams--home", "user": "active@example.org", "path": "/teams"},
        {"name": "teams--home-applicant", "user": "returning@example.org", "path": "/teams"},
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
        {"name": "admin--dashboard-treasurer", "user": "treasurer@example.org", "path": "/admin"},
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
