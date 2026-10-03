"""Screenshot every page of the portal, and compare two sets of screenshots.

A safety net for changing the look of the site: take a set before, change the
CSS, take a set after, and see exactly which pages changed -- instead of
clicking through every page by hand hoping to spot the one that broke.

    python scripts/visual/snapshot.py shoot  OUT_DIR
    python scripts/visual/snapshot.py compare BEFORE_DIR AFTER_DIR

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
    })
    return app, app_module


def go_offline(app_module):
    """Stripe, the forum and the git remote answer without a network."""
    from aeronautics_members.forum_service import DiscourseConnectProvider, ForumProviderError
    from aeronautics_members.services import system_update

    def offline(self, *args, **kwargs):
        raise ForumProviderError("Offline for screenshots.")

    DiscourseConnectProvider._request = offline
    system_update.get_remote_version = lambda force=False: None

    subscriptions = {}  # email -> the Stripe subscription the page should see

    def refresh(member, **kwargs):
        return False, subscriptions.get(member.email_private), None

    app_module.refresh_member_billing_state = refresh
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

    new = person("new", "Nora", "Neumann", verified=False, work_verified=False,
                 payment_status="pending_checkout", is_active=False,
                 membership_starts_on=None, membership_ends_on=None, renewal_due_on=None)
    photo = person("photo-needed", "Paul", "Photo")
    pending = person("photo-pending", "Petra", "Pending")
    db.session.add(ForumAvatarSubmission(user_id=pending.user.id, member_id=pending.id,
                                         status="pending", public_token="snap-pending"))
    rejected = person("photo-rejected", "Rene", "Rejected")
    db.session.add(ForumAvatarSubmission(user_id=rejected.user.id, member_id=rejected.id,
                                         status="rejected", public_token="snap-rejected",
                                         review_note="Please use a photo of yourself.",
                                         reviewed_at=now))
    active = person("active", "Anna", "Maximilian-Hofstetter-Wallensteiner",
                    title="Dipl.-Ing. (FH)", stripe_customer_id="cus_snap_active",
                    stripe_subscription_id="sub_snap_active")
    db.session.add(ForumAvatarSubmission(user_id=active.user.id, member_id=active.id,
                                         status="approved", public_token="snap-approved",
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
    teams.create_team(None, slug=None, name="Glider Team", **team_fields)
    db.session.add(TeamMembership(team=rocket, user=active.user, status=teams.ACTIVE))
    teams.grant_team_role(None, rocket, active.user, teams.ROLE_LEAD)
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
        {"name": "public--signup-errors", "path": "/join", "submit": "form[action$='process-membership']"},
        {"name": "public--login", "path": "/login"},
        {"name": "public--forgot-password", "path": "/forgot-password"},
        {"name": "public--reset-password", "path": f"/reset-password/{reset_token}"},
        {"name": "public--legal", "path": "/legal"},
        {"name": "public--thank-you", "path": "/thank-you?method=checkout&phase=prorated"},
        {"name": "public--thank-you-free", "path": "/thank-you?method=checkout&phase=free_period"},
        {"name": "public--cancel", "path": "/cancel"},
        {"name": "public--not-found", "path": "/no-such-page"},
        *member_pages("new"),
        *member_pages("photo-needed", {"name": "forum--photo-needed", "path": "/forum"}),
        *member_pages("photo-pending"),
        *member_pages("photo-rejected"),
        *member_pages("active",
                      {"name": "account--change-password", "path": "/change-password"},
                      # Saving the profile: the success message with its tick.
                      {"name": "account--saved", "path": "/account",
                       "submit": "form[action$='/account/profile']"},
                      {"name": "account--delete-confirm", "path": f"/account/delete/{delete_token}"}),
        *member_pages("sepa"),
        *member_pages("ended"),
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
        {"name": "admin--teams", "user": "admin@example.org", "path": "/admin/teams"},
        {"name": "admin--team-detail", "user": "admin@example.org", "path": "/admin/teams/rocket-team"},
        {"name": "admin--team-new", "user": "admin@example.org", "path": "/admin/teams/new"},
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


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "shoot":
        shoot(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "compare":
        compare(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(2)
