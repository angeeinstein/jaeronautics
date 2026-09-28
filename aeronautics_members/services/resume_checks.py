"""The checklist shown after background jobs are resumed.

Resuming says "this is the real portal now". The checklist then shows, line by
line, that it can actually do its job: the database is at the right version,
Stripe, the forum and the mail accounts accept their credentials, every
background timer is firing, and the queue that built up while paused drains.

The three checks that talk to another service run one at a time, one per poll
of the page, so each line turns from a spinner to a result as it really
finishes. Their results are kept, so reloading the page does not repeat them.
"""

import json
from datetime import datetime, timezone

from ..db_models import MailAccount, Setting, db
from . import background_jobs
from .diagnostics import get_queue_summary, get_schema_revision

RESULT_PREFIX = "resume_check."

# (key, label) in the order they are run and shown.
SERVICE_CHECKS = (
    ("stripe", "Payments (Stripe)"),
    ("forum", "Forum connection"),
    ("mail", "Email accounts"),
)

# States: ok, failed, running (being checked, or waiting to be), waiting (for a
# timer to fire), scheduled (runs later on its own), skipped (switched off).
DONE_STATES = ("ok", "scheduled", "skipped")


def reset():
    """Forget earlier results, so the checks run again."""
    db.session.execute(db.delete(Setting).where(Setting.key.like(f"{RESULT_PREFIX}%")))


def _stored(key):
    setting = db.session.get(Setting, f"{RESULT_PREFIX}{key}")
    if setting is None:
        return None
    try:
        return json.loads(setting.value)
    except ValueError:
        return None


def _store(key, state, detail):
    # Setting values are 255 characters; the detail is cut to fit.
    value = json.dumps({"state": state, "detail": detail[:180]})
    setting = db.session.get(Setting, f"{RESULT_PREFIX}{key}")
    if setting is None:
        db.session.add(Setting(key=f"{RESULT_PREFIX}{key}", value=value))
    else:
        setting.value = value


def _check_stripe():
    import stripe

    from .billing import apply_runtime_stripe_config

    settings = apply_runtime_stripe_config()
    if not stripe.api_key:
        return "failed", "No Stripe secret key is set. Enter it under Settings > Billing."
    try:
        price_id = settings.get("stripe_price_id")
        if price_id:
            stripe.Price.retrieve(price_id)
            return "ok", f"Stripe accepts the key and knows the membership price ({price_id})."
        stripe.Balance.retrieve()
        return "ok", "Stripe accepts the key. No membership price is set yet."
    except stripe.error.AuthenticationError:
        return "failed", "Stripe refused the secret key. Check it under Settings > Billing."
    except stripe.error.InvalidRequestError as exc:
        return "failed", f"Stripe accepts the key but not the price: {exc.user_message or exc}"
    except Exception as exc:  # noqa: BLE001 -- any failure is the answer here
        return "failed", f"Could not reach Stripe: {exc}"


def _check_forum():
    from ..forum_service import ForumProviderError
    from .forum import get_forum_service

    service = get_forum_service()
    if not service.is_enabled():
        return "skipped", "Forum integration is switched off."
    try:
        success, message = service.test_connection()
    except ForumProviderError as exc:
        return "failed", str(exc)
    except Exception as exc:  # noqa: BLE001
        return "failed", f"Could not reach the forum: {exc}"
    return ("ok" if success else "failed"), message


def _check_mail():
    from ..mail_utils import probe_mail_account_connection

    accounts = db.session.execute(db.select(MailAccount).order_by(MailAccount.account_key)).scalars().all()
    if not accounts:
        return "failed", "No mail account is set up, so no email can be sent. Add one under Mail Accounts."
    failing = []
    for account in accounts:
        success, _message = probe_mail_account_connection(account.to_config())
        if not success:
            failing.append(account.account_key)
    if failing:
        return "failed", f"Could not sign in to: {', '.join(failing)}. Check them under Mail Accounts."
    return "ok", f"Signed in to all {len(accounts)} mail account(s)."


_RUNNERS = {"stripe": _check_stripe, "forum": _check_forum, "mail": _check_mail}


def _parse(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def checklist(run_next=True):
    """Every line of the checklist, running at most one service check first.

    The caller commits.
    """
    items = []

    schema = get_schema_revision()
    items.append({
        "key": "database", "label": "Database",
        "state": "ok" if schema["up_to_date"] else "failed",
        "detail": "At the current version." if schema["up_to_date"]
        else f"At version {schema['applied']}, expected {schema['expected']}. Run the installer again.",
    })

    ran_one = not run_next
    for key, label in SERVICE_CHECKS:
        result = _stored(key)
        if result is None and not ran_one:
            state, detail = _RUNNERS[key]()
            _store(key, state, detail)
            result = {"state": state, "detail": detail}
            ran_one = True
        if result is None:
            items.append({"key": key, "label": label, "state": "running", "detail": "Waiting to be checked."})
        else:
            items.append({"key": key, "label": label, **result})

    resumed = background_jobs.resumed_state() or {}
    resumed_at = _parse(resumed.get("at"))
    for job in background_jobs.JOBS:
        state, detail = background_jobs.job_status(job, resumed_at=resumed_at)
        if background_jobs.is_paused() and state == "waiting":
            state = "scheduled"
        items.append({"key": f"job-{job.name}", "label": job.label, "state": state, "detail": detail})

    queues = get_queue_summary()
    pending, failed = queues["external_work_pending"], queues["external_work_failed"]
    if pending:
        queue_state, queue_detail = "waiting", f"{pending} task(s) still waiting to be sent to the forum."
    elif failed:
        queue_state, queue_detail = "failed", f"{failed} task(s) failed. See Background work above."
    else:
        queue_state, queue_detail = "ok", "Nothing waiting."
    items.append({"key": "queue", "label": "Queued work", "state": queue_state, "detail": queue_detail})
    return items


def all_done(items):
    return all(item["state"] in DONE_STATES for item in items)
