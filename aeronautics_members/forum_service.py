import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, quote_plus, urlencode
from urllib.request import Request, urlopen

from flask import current_app
from werkzeug.utils import secure_filename

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - optional until dependencies are installed
    Image = None
    ImageOps = None

    class UnidentifiedImageError(Exception):
        pass

try:
    from .db_models import ForumAccount, ForumAvatarSubmission, Member, User, db
    from .member_categories import CATEGORY_ORDER
    from .permissions import Permission
    from .security_utils import build_public_url
    from .services.membership import member_has_active_access
except ImportError:
    from db_models import ForumAccount, ForumAvatarSubmission, Member, User, db
    from member_categories import CATEGORY_ORDER
    from permissions import Permission
    from security_utils import build_public_url
    from services.membership import member_has_active_access

def member_category_groups(settings):
    """``{member category: forum group}`` from the setting, as ``{}`` when unset.

    Two axes, not one. What somebody has paid for and what kind of person they
    are are different questions with different answers -- a lecturer is not a
    student whose membership is in a different state -- and a category that
    wants only one of them should be able to say so without the other being
    dragged in. So membership drives one group and the member category drives
    another, and a category in Discourse grants whichever of them it means.

    Written as lines because the alternative is a setting per kind of member,
    and adding a kind would then mean adding a setting, a form field and a
    template row before anybody could use it.

        student = students
        staff   = lecturers
        partner = companies

    Anything not named here is simply not sorted: a kind of member with no line
    is in no group of this sort, which is what "we have not decided about them
    yet" should look like.
    """
    found = {}
    for line in str(settings.get("forum_category_groups") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        category, sep, group = line.partition("=")
        if not sep:
            continue
        category, group = category.strip().lower(), group.strip()
        if category in CATEGORY_ORDER and group:
            found[category] = group
    return found


FORUM_STATE_INACTIVE = "inactive"
FORUM_STATE_ONBOARDING = "onboarding"
FORUM_STATE_ACTIVE = "active"
FORUM_STATE_SYNC_ERROR = "sync_error"
# Terminal: the account was erased and the remote identity anonymised. Nothing
# should sync into this state again, which is why it is distinct from inactive.
FORUM_STATE_ANONYMISED = "anonymised"

FORUM_AVATAR_STATUS_PENDING = "pending"
FORUM_AVATAR_STATUS_APPROVED = "approved"
FORUM_AVATAR_STATUS_REJECTED = "rejected"
FORUM_AVATAR_STATUS_SUPERSEDED = "superseded"

FORUM_SETTING_DEFAULTS = {
    "forum_integration_enabled": "False",
    "forum_provider": "discourse",
    "forum_auth_strategy": "discourse_connect",
    "forum_base_url": "",
    "discourse_api_key": "",
    "discourse_api_username": "",
    "discourse_connect_secret": "",
    # Named for what is true of the people in them, because these names end up
    # on category permissions in Discourse and somebody reading one there has
    # only the name to go on. "member-onboarding" read as "has not paid yet"
    # and meant "has paid, and has not had a photograph approved yet", which
    # are opposite answers to the only question the name gets asked.
    "forum_onboarding_group": "members-awaiting-photo",
    "forum_member_group": "members",
    # Covers a membership that ran out, one that was never paid for, and an
    # account that was switched off -- so not "expired", which is only the
    # commonest of the three. Given a name at all so that these people can be
    # shown how to put it right, rather than an empty forum with no
    # explanation.
    "forum_inactive_group": "membership-inactive",
    # A Discourse group for the people who run the association, driven from the
    # portal's own roles rather than maintained by hand on the forum. Empty
    # means the forum does not have one and nothing is sent about it.
    "forum_staff_group": "",
    # Which forum group each kind of member belongs to, one "category = group"
    # per line. Empty means the forum does not sort people by kind at all.
    "forum_category_groups": "",
    # Who may read the lecture material, and who may read the archive of it.
    # No default on purpose: "everybody who has paid" is the obvious answer and
    # the wrong one, because lecturers and company representatives are paying
    # members too and the material is about the lectures they give.
    "forum_lecture_groups": "",
    "forum_archive_groups": "",
    "forum_onboarding_path": "/",
    "forum_avatar_max_bytes": str(5 * 1024 * 1024),
    "forum_avatar_allowed_types": "jpg,jpeg,png,webp",
}
FORUM_SETTING_KEYS = tuple(FORUM_SETTING_DEFAULTS.keys())

# How often bulk work waits out a rate limit before giving up on one call.
# Discourse allows roughly sixty admin calls a minute and publishing the old
# forum's register is around fourteen hundred, so being told to wait is the
# normal course of that job rather than a fault.
BULK_RATE_LIMIT_RETRIES = 10

# Every request to Discourse identifies itself with this, and it is shared
# rather than written out per client on purpose: the forum sits behind
# Cloudflare, which blocks the default urllib signature outright (error 1010,
# "browser signature banned"). A second HTTP client that forgot to send it
# failed every call while the first one worked, which reads like a Discourse
# problem and is not one. Kept verbatim, because Cloudflare is configured to
# let this exact string through.
DISCOURSE_USER_AGENT = (
    "JoanneumAeronauticsForumSync/1.0 (+https://testmembers.joanneum-aeronautics.at)"
)

# Where Discourse keeps its settings; the path has moved between versions, so
# the one that answers is the one used.
SITE_SETTINGS_PATHS = (
    "/admin/site_settings.json",
    "/admin/config/site_settings.json",
    "/admin/site_settings/category/all_results.json",
)

# The setting that decides whether an avatar sent over Connect is used at all.
# With it off, Discourse fetches the picture from the portal and then shows the
# letter it drew instead, and says nothing about having done so -- which is
# equally true of an approved member avatar and of the imported archive.
AVATAR_OVERRIDE_SETTINGS = (
    "discourse_connect_overrides_avatar",
    "sso_overrides_avatar",
)

def _record_the_name_the_forum_gave(user, remote_user):
    """Keep the portal's idea of somebody's forum name equal to the forum's.

    Discourse does not have to accept the username it is handed. It caps them
    at ``max_username_length`` -- twenty by default -- and adjusts anything
    longer as it creates the account, and it appends to one that is taken. It
    says nothing about having done either.

    The portal's scheme is surname, initial, year group, so a student called
    Niedergrottenthaler needs twenty-four characters. Left alone, the portal
    shows a username the forum has never heard of, and anything that addresses
    that person by name -- as the archive import does, to post as them -- fails
    against a name that does not exist.

    Nothing is changed when the name already matches, which is almost always.
    """
    actual = (remote_user or {}).get("username")
    actual = actual.strip() if isinstance(actual, str) else ""
    if not actual or user is None or user.forum_username == actual:
        return False

    taken = db.session.execute(
        db.select(User.id).filter(User.forum_username == actual, User.id != user.id)
    ).first()
    if taken:
        # Two portal accounts cannot hold one forum name, and guessing which
        # of them is wrong is not this function's business. Said, not fixed.
        current_app.logger.warning(
            "The forum calls user_id=%s %r, which user_id=%s already holds here.",
            user.id, actual, taken[0],
        )
        return False

    current_app.logger.info(
        "The forum stored user_id=%s as %r rather than %r; following it.",
        user.id, actual, user.forum_username,
    )
    user.forum_username = actual
    return True


def _is_named_in(message, username):
    """Whether Discourse's complaint names this particular person.

    Word-boundary rather than ``in``: the board has both ``KlampflS_L10`` and
    ``KlampflL_L12``, and a plain substring test on a message naming one would
    quietly drop the other from the retry -- leaving somebody out of their
    cohort for a reason nobody would ever find.
    """
    return re.search(rf"(?<![\w.-]){re.escape(username)}(?![\w.-])", message,
                     re.IGNORECASE) is not None


_ALLOWED_IMAGE_TYPE_TO_EXTENSION = {
    "jpeg": "jpg",
    "png": "png",
    "webp": "webp",
}

_AVATAR_REQUEST_LIMIT_MULTIPLIER = 3
_AVATAR_REQUEST_LIMIT_MIN_BYTES = 10 * 1024 * 1024
_AVATAR_MAX_DIMENSION = 2048
_AVATAR_RESIZE_STEP = 0.85
_AVATAR_QUALITY_STEPS = (92, 86, 80, 74, 68, 60, 52)


def format_bytes_human(num_bytes):
    value = float(max(int(num_bytes or 0), 0))
    units = ["bytes", "KB", "MB", "GB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def get_avatar_request_limit(max_output_bytes):
    normalized_max = max(int(max_output_bytes or 0), 1)
    return max(_AVATAR_REQUEST_LIMIT_MIN_BYTES, normalized_max * _AVATAR_REQUEST_LIMIT_MULTIPLIER)


def _normalize_allowed_extensions(allowed_extensions):
    normalized = []
    for extension in allowed_extensions or []:
        lowered = str(extension).strip().lower()
        if lowered == "jpeg":
            lowered = "jpg"
        if lowered in {"jpg", "png", "webp"} and lowered not in normalized:
            normalized.append(lowered)
    return normalized or ["jpg", "png", "webp"]


# An image file can be small on disk and enormous once decoded -- a "decompression
# bomb". A few hundred kilobytes can expand to tens of gigabytes of pixels, which
# takes the server down before any size check on the decoded image could run.
MAX_AVATAR_PIXELS = 25_000_000

if Image is not None:
    # Make Pillow itself refuse, as a backstop for any path that opens an image
    # without going through the check below.
    Image.MAX_IMAGE_PIXELS = MAX_AVATAR_PIXELS


def _load_image_for_processing(raw_bytes):
    if Image is None or ImageOps is None:
        raise ForumProviderError("Avatar processing is unavailable because Pillow is not installed on the server yet.")

    too_large = ForumProviderError(
        "The uploaded image is too large to process safely. Please choose a smaller image."
    )
    try:
        with Image.open(BytesIO(raw_bytes)) as image:
            # Image.open only parses the header, so the dimensions are known
            # before any pixel data is decoded. Checking here is the difference
            # between rejecting a bomb and being flattened by one: the previous
            # check ran after load(), by which point the memory was already gone.
            width, height = image.size
            if width * height > MAX_AVATAR_PIXELS:
                raise too_large

            processed = ImageOps.exif_transpose(image)
            processed.load()
            return processed
    except Image.DecompressionBombError as exc:
        raise too_large from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ForumProviderError("Please upload a valid JPG, PNG, or WebP image.") from exc


def _clamp_float(value, default, minimum, maximum):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _apply_avatar_crop(image, crop_options):
    crop_mode = (crop_options or {}).get("crop_mode") or ""
    if crop_mode != "square":
        return image

    width, height = image.size
    if width <= 0 or height <= 0:
        return image

    zoom = _clamp_float((crop_options or {}).get("crop_zoom"), 1.0, 1.0, 4.0)
    center_x_ratio = _clamp_float((crop_options or {}).get("crop_center_x"), 0.5, 0.0, 1.0)
    center_y_ratio = _clamp_float((crop_options or {}).get("crop_center_y"), 0.5, 0.0, 1.0)

    crop_side = max(1, int(min(width, height) / zoom))
    center_x = int(round(center_x_ratio * width))
    center_y = int(round(center_y_ratio * height))

    left = center_x - crop_side // 2
    top = center_y - crop_side // 2
    left = max(0, min(left, width - crop_side))
    top = max(0, min(top, height - crop_side))
    right = left + crop_side
    bottom = top + crop_side
    return image.crop((left, top, right, bottom))


def _image_has_alpha(image):
    if image.mode in {"RGBA", "LA"}:
        return True
    return bool(image.info.get("transparency"))


def _convert_image_for_extension(image, extension):
    if extension == "jpg":
        if _image_has_alpha(image):
            background = Image.new("RGB", image.size, (255, 255, 255))
            alpha_source = image.convert("RGBA")
            background.paste(alpha_source, mask=alpha_source.getchannel("A"))
            return background
        return image.convert("RGB")
    if extension == "png":
        return image.convert("RGBA") if _image_has_alpha(image) else image.convert("RGB")
    if extension == "webp":
        return image.convert("RGBA") if _image_has_alpha(image) else image.convert("RGB")
    return image


def _choose_output_extensions(image, allowed_extensions):
    normalized_extensions = _normalize_allowed_extensions(allowed_extensions)
    has_alpha = _image_has_alpha(image)
    preferred = []
    if has_alpha:
        preferred.extend([ext for ext in ("webp", "png", "jpg") if ext in normalized_extensions])
    else:
        preferred.extend([ext for ext in ("jpg", "webp", "png") if ext in normalized_extensions])
    return preferred or normalized_extensions


def _save_image_bytes(image, extension, quality=None):
    output = BytesIO()
    save_kwargs = {}
    save_format = "JPEG" if extension == "jpg" else extension.upper()
    if extension == "jpg":
        save_kwargs.update({"optimize": True, "quality": quality or 86, "progressive": True})
    elif extension == "webp":
        save_kwargs.update({"quality": quality or 80, "method": 6})
    elif extension == "png":
        save_kwargs.update({"optimize": True, "compress_level": 9})
    image.save(output, format=save_format, **save_kwargs)
    content_type = "image/jpeg" if extension == "jpg" else f"image/{extension}"
    return output.getvalue(), content_type


def _resize_image(image, scale_factor):
    if scale_factor >= 0.999:
        return image
    width, height = image.size
    resized_width = max(1, int(width * scale_factor))
    resized_height = max(1, int(height * scale_factor))
    return image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)


def normalize_avatar_image(raw_bytes, allowed_extensions, max_output_bytes, crop_options=None):
    image = _load_image_for_processing(raw_bytes)
    image = _apply_avatar_crop(image, crop_options)

    width, height = image.size
    largest_dimension = max(width, height)
    if largest_dimension > _AVATAR_MAX_DIMENSION:
        image = _resize_image(image, _AVATAR_MAX_DIMENSION / float(largest_dimension))

    output_extensions = _choose_output_extensions(image, allowed_extensions)
    current_image = image
    for _resize_pass in range(8):
        for extension in output_extensions:
            prepared = _convert_image_for_extension(current_image, extension)
            quality_steps = _AVATAR_QUALITY_STEPS if extension in {"jpg", "webp"} else (None,)
            for quality in quality_steps:
                candidate_bytes, content_type = _save_image_bytes(prepared, extension, quality=quality)
                if len(candidate_bytes) <= max_output_bytes:
                    return candidate_bytes, content_type, extension
        current_image = _resize_image(current_image, _AVATAR_RESIZE_STEP)

    raise ForumProviderError(
        f"The uploaded image could not be optimized below the avatar limit of {format_bytes_human(max_output_bytes)}."
    )


def read_limited_upload_bytes(upload, max_input_bytes):
    if upload is None or getattr(upload, "stream", None) is None:
        return b""
    upload.stream.seek(0)
    chunks = []
    total_size = 0
    while True:
        chunk = upload.stream.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_input_bytes:
            raise ForumProviderError(
                f"The selected image is too large to upload. Please keep it below {format_bytes_human(max_input_bytes)}."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def detect_image_type(raw_bytes):
    if raw_bytes.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
        return "webp"
    return None


class ForumProviderError(Exception):
    pass


@dataclass
class ForumSyncResult:
    changed: bool
    desired_state: str | None
    forum_account: ForumAccount | None
    error: str | None = None


class ForumAuthStrategy:
    slug = "base"

    def build_forum_redirect(self, destination_path=None):
        raise NotImplementedError

    def handle_provider_request(self, request_args, user, member, service):
        raise NotImplementedError


class OAuth2ProviderAuthStrategy(ForumAuthStrategy):
    slug = "oauth2_provider"

    def build_forum_redirect(self, destination_path=None):
        raise ForumProviderError("OAuth2 provider mode is not implemented yet.")

    def handle_provider_request(self, request_args, user, member, service):
        raise ForumProviderError("OAuth2 provider mode is not implemented yet.")


class ForumProvider:
    slug = "base"

    def sync_user(self, forum_account, user, member, desired_state, avatar_url=None, avatar_force_update=False):
        raise NotImplementedError

    def test_connection(self):
        raise NotImplementedError

    def build_avatar_url(self, submission):
        return None

    def set_avatar(self, forum_account, user, submission):
        raise NotImplementedError

    def log_out_user(self, forum_account, user):
        raise NotImplementedError

    def anonymize_user(self, forum_account, user):
        raise NotImplementedError


class DiscourseConnectProvider(ForumProvider):
    slug = "discourse"

    def __init__(self, settings):
        self.settings = settings

    def _api_headers(self):
        return {
            "Api-Key": self.settings["discourse_api_key"],
            "Api-Username": self.settings["discourse_api_username"],
            "Accept": "application/json",
            "User-Agent": DISCOURSE_USER_AGENT,
        }

    def _request(self, method, path, data=None, json_body=None, rate_limit_retries=0):
        """One call to Discourse.

        ``rate_limit_retries`` waits out a 429 and tries again, and defaults to
        off on purpose: every caller on a web request is serving somebody who
        is waiting, and sleeping fifty seconds inside a page load would be far
        worse than the error. Only bulk work, run from the command line, asks
        for it.
        """
        url = f"{self.settings['forum_base_url'].rstrip('/')}{path}"
        headers = self._api_headers()
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            body = urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                raw_body = response.read().decode("utf-8")
                if not raw_body:
                    return {}
                try:
                    return json.loads(raw_body)
                except json.JSONDecodeError:
                    return {"raw": raw_body}
        except HTTPError as exc:
            try:
                error_body = exc.read().decode("utf-8")
            except Exception:
                error_body = str(exc)
            if exc.code == 429 and rate_limit_retries > 0:
                # Discourse says how long to wait, so wait that long rather than
                # guessing. Bulk work runs into this by design: the admin API
                # allows about sixty calls a minute, and publishing seven
                # hundred profiles is fourteen hundred of them.
                wait_seconds = 30
                try:
                    wait_seconds = int(
                        json.loads(error_body)["extras"]["wait_seconds"]
                    )
                except Exception:  # noqa: BLE001 -- a 429 without the detail
                    pass
                # A second past what it asked for, because the window is
                # measured on Discourse's clock and not on ours.
                time.sleep(min(wait_seconds, 120) + 1)
                return self._request(
                    method, path, data=data, json_body=json_body,
                    rate_limit_retries=rate_limit_retries - 1,
                )
            raise ForumProviderError(f"Discourse API request failed ({exc.code}): {error_body}") from exc
        except URLError as exc:
            raise ForumProviderError(f"Could not reach Discourse: {exc}") from exc
        except Exception as exc:
            raise ForumProviderError(f"Discourse request failed: {exc}") from exc

    def _sign_sso_payload(self, payload_values):
        payload = urlencode(payload_values)
        encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
        digest = hmac.new(
            self.settings["discourse_connect_secret"].encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return encoded, digest

    def _build_group_fields(self, desired_state, user=None, member=None):
        """Which forum groups this person should be in, and which not.

        Sent on every sync, not only on the ones that change something, because
        the portal is the record and the forum is a copy of it. Membership
        lapses, somebody joins the committee, somebody leaves it -- each is a
        row changing here, and the next sync carries it across without anybody
        going to the forum to do it by hand.

        ``remove_groups`` matters as much as ``add_groups``: access that is
        only ever granted is access nobody ever loses.
        """
        onboarding_group = self.settings.get("forum_onboarding_group", "").strip()
        member_group = self.settings.get("forum_member_group", "").strip()
        inactive_group = self.settings.get("forum_inactive_group", "").strip()
        staff_group = self.settings.get("forum_staff_group", "").strip()

        add_groups = []
        remove_groups = []

        # What kind of member somebody is, which is a different question from
        # what they have paid for. Every group in the mapping is named on every
        # sync -- one to join, the rest to leave -- so a student who becomes an
        # alumnus moves between them without anybody touching the forum.
        by_category = member_category_groups(self.settings)
        if by_category:
            # Only while the membership is current. These groups are what the
            # lecture categories are granted to, so being in one has to mean
            # both "is a student here" and "has paid" -- Discourse checks a
            # union of groups and never an intersection, so the conjunction
            # cannot be expressed over there and is made here instead.
            theirs = by_category.get(
                getattr(member, "member_category", None) or ""
            ) if desired_state == FORUM_STATE_ACTIVE else None
            for group in dict.fromkeys(by_category.values()):
                (add_groups if group == theirs else remove_groups).append(group)

        if staff_group:
            # Who runs the association is a portal role, and this is the same
            # answer the admin pages give -- so somebody who stops being on the
            # committee stops being in the forum group at their next sync,
            # rather than when somebody remembers.
            runs_the_place = bool(user is not None and user.can(Permission.FORUM_MODERATE))
            (add_groups if runs_the_place else remove_groups).append(staff_group)
        if desired_state == FORUM_STATE_ACTIVE:
            if member_group:
                add_groups.append(member_group)
            if onboarding_group:
                remove_groups.append(onboarding_group)
            if inactive_group:
                remove_groups.append(inactive_group)
        elif desired_state == FORUM_STATE_ONBOARDING:
            if onboarding_group:
                add_groups.append(onboarding_group)
            if member_group:
                remove_groups.append(member_group)
            if inactive_group:
                remove_groups.append(inactive_group)
        else:
            if inactive_group:
                add_groups.append(inactive_group)
            if member_group:
                remove_groups.append(member_group)
            if onboarding_group:
                remove_groups.append(onboarding_group)

        payload = {}
        if add_groups:
            payload["add_groups"] = ",".join(dict.fromkeys(add_groups))
        if remove_groups:
            payload["remove_groups"] = ",".join(dict.fromkeys(remove_groups))
        return payload

    def build_sso_payload(self, user, member, desired_state, nonce, avatar_url=None, avatar_force_update=False):
        full_name = f"{member.first_name} {member.last_name}".strip() if member else (user.email or "")
        payload = {
            "nonce": nonce,
            "external_id": str(user.id),
            "email": user.email,
            "username": user.forum_username or f"member-{user.id}",
            "name": full_name,
            # Discourse trusts the address we assert only when we vouch for it.
            # If we have not verified ownership ourselves, ask Discourse to run
            # its own activation instead of silently accepting the address.
            "require_activation": "false" if user.email_is_verified else "true",
        }
        if avatar_url:
            payload["avatar_url"] = avatar_url
            if avatar_force_update:
                payload["avatar_force_update"] = "true"
        payload.update(self._build_group_fields(desired_state, user, member))
        return payload

    def sync_user(self, forum_account, user, member, desired_state, avatar_url=None, avatar_force_update=False):
        payload = self.build_sso_payload(
            user,
            member,
            desired_state,
            nonce=f"sync-{user.id}-{int(datetime.now(timezone.utc).timestamp())}",
            avatar_url=avatar_url,
            avatar_force_update=avatar_force_update,
        )
        encoded, signature = self._sign_sso_payload(payload)
        self._request(
            "POST",
            "/admin/users/sync_sso",
            data={"sso": encoded, "sig": signature},
        )
        remote_user = self.get_remote_user_by_external_id(forum_account.external_id)
        forum_account.remote_user_id = remote_user.get("id") or forum_account.remote_user_id
        _record_the_name_the_forum_gave(user, remote_user)
        return remote_user

    def sync_imported_profile(self, payload):
        """Create or update the forum profile of one imported person.

        The same endpoint the member sync uses, given a payload built for
        somebody who has no membership here. Discourse keys on external_id, so
        running this twice updates rather than duplicates.
        """
        encoded, signature = self._sign_sso_payload(payload)
        return self._request(
            "POST", "/admin/users/sync_sso", data={"sso": encoded, "sig": signature},
            rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
        )

    def ensure_group(self, name):
        """A Discourse group by that name, made if it is not there yet.

        Looked up before creating rather than created and the error ignored: a
        failure to create is worth hearing about, and "it already exists" and
        "the API key cannot do this" are otherwise the same 422.
        """
        try:
            existing = self._request(
                "GET", f"/groups/{quote(str(name))}.json",
                rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            )
        except ForumProviderError:
            # Discourse answers 404 for a group that is not there, which is the
            # ordinary case on a first run and not a failure. A real problem --
            # a bad key, an unreachable host -- surfaces on the create below,
            # where the message describes what was actually being attempted.
            existing = None
        if isinstance(existing, dict) and existing.get("group"):
            return existing["group"], False

        created = self._request(
            "POST", "/admin/groups.json",
            rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            json_body={"group": {
                "name": name,
                # Visible so people can browse it, and joinable by nobody: it
                # describes who was in a cohort, which is not a thing to opt
                # into.
                "visibility_level": 0,
                "members_visibility_level": 0,
                "public_admission": False,
                "public_exit": False,
            }},
        )
        group = created.get("basic_group") if isinstance(created, dict) else None
        return group or {}, True

    # Discourse takes a comma-separated list here, and there is no documented
    # ceiling, but seven hundred names in one URL-encoded body is a request
    # nothing in the chain has any reason to accept. A hundred at a time is
    # eight calls for the whole register and small enough to be uncontroversial.
    GROUP_MEMBER_BATCH = 100

    def add_group_members(self, group_id, usernames):
        """Put people into a group directly, without going through SSO.

        Necessary because the SSO payload's ``add_groups`` is not a way to
        *make* somebody a member: Discourse matches those names against the
        groups that already exist and quietly ignores the rest. A profile
        published before its cohort group existed is therefore in no group at
        all, and no amount of re-sending the same payload changes that unless
        the group is there first.

        Returns how many were newly added; the rest were already in the group.
        """
        names = [name for name in usernames if name]
        added = 0
        for start in range(0, len(names), self.GROUP_MEMBER_BATCH):
            added += self._add_some_group_members(
                group_id, names[start:start + self.GROUP_MEMBER_BATCH]
            )
        return added

    def _add_some_group_members(self, group_id, batch):
        """One call, minus anybody Discourse says is in the group already.

        Discourse refuses the *whole* batch when one name in it is already a
        member -- 422, "The following users are already members of this group",
        and not one of the other ninety-nine is added. Running this a second
        time is the ordinary case, so taken literally it means a group can never
        be completed once it is partly filled.

        The refusal names them, though, so they are dropped and the rest are
        sent again.
        """
        try:
            self._request(
                "PUT", f"/groups/{int(group_id)}/members.json",
                data={"usernames": ",".join(batch)},
                rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            )
            return len(batch)
        except ForumProviderError as exc:
            message = str(exc)
            if "already" not in message.lower():
                raise
            remaining = [name for name in batch if not _is_named_in(message, name)]
            if not remaining:
                return 0
            if len(remaining) == len(batch):
                # It complained about somebody, and not about anybody we sent.
                # Sending the same thing again would loop, so say so instead.
                raise
            return self._add_some_group_members(group_id, remaining)

    # Discourse has moved its user-field admin route between versions, and an
    # admin route also answers 404 -- rather than 403 -- when the API user is
    # not staff, so a single guessed path cannot tell "wrong URL" from "wrong
    # credentials". Try the known ones and let the caller decide what a total
    # failure means.
    USER_FIELD_PATHS = (
        "/admin/customize/user_fields.json",
        "/admin/config/user_fields.json",
        "/admin/user_fields.json",
    )

    def _user_fields_path(self):
        """The path this Discourse answers on, with the fields it returned."""
        last_error = None
        for path in self.USER_FIELD_PATHS:
            try:
                response = self._request(
                    "GET", path, rate_limit_retries=BULK_RATE_LIMIT_RETRIES
                )
            except ForumProviderError as exc:
                last_error = exc
                continue
            if isinstance(response, dict) and "user_fields" in response:
                return path, response["user_fields"]
        raise ForumProviderError(
            "Could not read the custom user fields from any known admin path "
            f"({', '.join(self.USER_FIELD_PATHS)}). Discourse answers 404 on "
            "admin routes when the API user is not staff, so check that "
            f"discourse_api_username is an administrator. Last error: {last_error}"
        )

    def find_user_field(self, name):
        """The id of a custom user field by name, or None.

        Returned as ``user_field_N`` because that is how DiscourseConnect names
        it in a payload, which is the only reason this is being looked up.
        """
        _path, fields = self._user_fields_path()
        for field in fields or []:
            if str(field.get("name", "")).strip().lower() == name.strip().lower():
                return f"user_field_{field.get('id')}"
        return None

    def ensure_user_field(self, name, description=""):
        path, fields = self._user_fields_path()
        for field in fields or []:
            if str(field.get("name", "")).strip().lower() == name.strip().lower():
                return f"user_field_{field.get('id')}", False

        created = self._request(
            "POST", path,
            rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            json_body={"user_field": {
                "name": name,
                "description": description or name,
                "field_type": "text",
                "editable": False,       # it comes from the portal, not the person
                "required": False,       # most members are not from the old forum
                "show_on_profile": True,
                "show_on_user_card": True,
                "searchable": True,
            }},
        )
        field = created.get("user_field") if isinstance(created, dict) else None
        if not field:
            return None, False
        return f"user_field_{field.get('id')}", True

    def get_remote_user_by_external_id(self, external_id):
        response = self._request("GET", f"/u/by-external/{quote(str(external_id))}.json")
        if isinstance(response, dict) and isinstance(response.get("user"), dict):
            return response["user"]
        return response if isinstance(response, dict) else {}

    def avatar_override_state(self):
        """``(setting, on)`` for whether this forum uses the avatars we send.

        ``None`` if the forum will not say -- an older version that calls it
        something else again, or a key that cannot read settings. Not knowing is
        reported as not knowing; the caller must not read it as "fine".
        """
        for path in SITE_SETTINGS_PATHS:
            try:
                payload = self._request("GET", path)
            except ForumProviderError:
                continue
            rows = payload.get("site_settings") if isinstance(payload, dict) else None
            if not rows:
                continue
            values = {row.get("setting"): row.get("value") for row in rows}
            for name in AVATAR_OVERRIDE_SETTINGS:
                if name in values:
                    return name, str(values[name]).lower() == "true"
            return None
        return None

    def test_connection(self):
        response = self._request("GET", "/site.json")
        site_name = response.get("site_name") if isinstance(response, dict) else None
        message = (
            f"Connected to Discourse site '{site_name}'." if site_name
            else "Connected to Discourse successfully."
        )
        # Asked here because this is the button somebody presses when something
        # about the forum looks wrong, and a blank avatar is the one fault with
        # no other symptom: the picture is uploaded, approved, fetched by the
        # forum and then quietly dropped.
        try:
            avatars = self.avatar_override_state()
        except ForumProviderError:
            avatars = None
        if avatars and not avatars[1]:
            message += (
                f" Avatars uploaded here will not be shown there: {avatars[0]} "
                f"is off in the forum's settings, so it fetches each picture and "
                f"keeps its own letter. Turn it on in Admin -> Settings."
            )
        return True, message

    def build_avatar_url(self, submission):
        if submission is None or not submission.public_token:
            return None
        cache_bust = submission.file_hash or int(datetime.now(timezone.utc).timestamp())
        return build_public_url("forum.forum_avatar_public_file", token=submission.public_token, v=cache_bust)

    def set_avatar(self, forum_account, user, submission):
        avatar_url = self.build_avatar_url(submission)
        if not avatar_url:
            raise ForumProviderError("The approved avatar is missing a public file URL.")
        return self.sync_user(
            forum_account,
            user,
            submission.member,
            FORUM_STATE_ONBOARDING,
            avatar_url=avatar_url,
            avatar_force_update=True,
        )

    def log_out_user(self, forum_account, user):
        if not forum_account.remote_user_id:
            try:
                remote_user = self.get_remote_user_by_external_id(forum_account.external_id)
            except ForumProviderError as exc:
                if "failed (404)" in str(exc):
                    return False
                raise
            forum_account.remote_user_id = remote_user.get("id")
        if not forum_account.remote_user_id:
            return False

        self._request(
            "POST",
            f"/admin/users/{quote(str(forum_account.remote_user_id))}/log_out",
        )
        return True

    def anonymize_user(self, forum_account, user):
        """Replace the forum identity with an anonymous one, keeping the posts.

        Discourse's own anonymise: username, email and avatar are replaced with
        generated values and the account is detached from its external id, so a
        later login cannot land back on it.

        The posts stay, deliberately. They are conversations other members took
        part in, and removing one side of a thread damages their records to
        protect data the anonymisation has already removed. Discourse also
        refuses to delete an account with any real posting history, so deletion
        is not a reliable option to build on in the first place.
        """
        if not self._resolve_remote_user_id(forum_account):
            return False

        self._request(
            "PUT",
            f"/admin/users/{quote(str(forum_account.remote_user_id))}/anonymize.json",
        )
        return True

    def delete_remote_user(self, remote_user_id):
        """Remove an empty Discourse account. Returns (deleted, reason).

        For the throwaway account a returning student had before they
        reconnected: made at signup and left holding the address their real
        account needs. Deleted rather than anonymised because there is nothing
        in it worth keeping and an anonymised husk would still sit in the
        register of everyone who was ever a member.

        Only when it really is empty. Forum access needs an active membership
        and an avatar -- not a confirmed university address -- so somebody can
        post from that account for weeks before the confirmation that moves
        them onto their old one. Deleting their posts to tidy up an email
        conflict would be destroying the thing this whole migration exists to
        preserve, so an account with anything in it is left alone and reported.

        A 404 counts as deleted: something else removed it, and the point was
        for it not to be there.
        """
        try:
            remote_user = self._request(
                "GET", f"/admin/users/{quote(str(remote_user_id))}.json",
                rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            )
        except ForumProviderError as exc:
            if "failed (404)" in str(exc):
                return True, None
            raise

        # Any of these means a person did something in that account.
        written = sum(
            int(remote_user.get(field) or 0)
            for field in ("post_count", "topic_count", "likes_given", "likes_received")
        )
        if written:
            return False, (
                f"the account has {written} posts, topics or likes and was left alone"
            )

        try:
            self._request(
                "DELETE", f"/admin/users/{quote(str(remote_user_id))}.json",
                json_body={"delete_posts": False, "block_email": False},
                rate_limit_retries=BULK_RATE_LIMIT_RETRIES,
            )
        except ForumProviderError as exc:
            if "failed (404)" in str(exc):
                return True, None
            raise
        return True, None

    def _resolve_remote_user_id(self, forum_account):
        """Fill in the remote id from the external id, tolerating a missing user."""
        if forum_account.remote_user_id:
            return forum_account.remote_user_id
        try:
            remote_user = self.get_remote_user_by_external_id(forum_account.external_id)
        except ForumProviderError as exc:
            if "failed (404)" in str(exc):
                return None
            raise
        forum_account.remote_user_id = remote_user.get("id")
        return forum_account.remote_user_id


class DiscourseConnectAuthStrategy(ForumAuthStrategy):
    slug = "discourse_connect"

    def __init__(self, settings, provider):
        self.settings = settings
        self.provider = provider

    def build_forum_redirect(self, destination_path=None):
        return_path = (destination_path or self.settings.get("forum_onboarding_path") or "/").strip() or "/"
        if not return_path.startswith("/"):
            return_path = f"/{return_path}"
        forum_base = self.settings["forum_base_url"].rstrip("/")
        return f"{forum_base}/session/sso?return_path={quote_plus(return_path)}"

    def handle_provider_request(self, request_args, user, member, service):
        encoded = request_args.get("sso", "")
        signature = request_args.get("sig", "")
        if not encoded or not signature:
            raise ForumProviderError("Missing DiscourseConnect payload.")

        expected_sig = hmac.new(
            self.settings["discourse_connect_secret"].encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            raise ForumProviderError("Invalid DiscourseConnect signature.")

        try:
            payload = base64.b64decode(encoded).decode("utf-8")
        except Exception as exc:
            raise ForumProviderError("Invalid DiscourseConnect payload encoding.") from exc

        values = dict(parse_qsl(payload, keep_blank_values=True))
        nonce = values.get("nonce")
        return_sso_url = values.get("return_sso_url")
        if not nonce or not return_sso_url:
            raise ForumProviderError("Incomplete DiscourseConnect payload.")

        desired_state = service.get_desired_state(member)
        approved_submission = service.get_current_approved_submission(member)
        provider_payload = self.provider.build_sso_payload(
            user,
            member,
            desired_state,
            nonce=nonce,
            avatar_url=self.provider.build_avatar_url(approved_submission),
            avatar_force_update=False,
        )
        encoded_response, response_sig = self.provider._sign_sso_payload(provider_payload)
        separator = "&" if "?" in return_sso_url else "?"
        return f"{return_sso_url}{separator}sso={quote_plus(encoded_response)}&sig={response_sig}"


class ForumService:
    def __init__(self, settings_map):
        self.settings = normalize_forum_settings(settings_map)
        self.provider = None
        self.auth_strategy = None
        if self.settings["forum_provider"] == "discourse" and self.settings["forum_auth_strategy"] == "discourse_connect":
            self.provider = DiscourseConnectProvider(self.settings)
            self.auth_strategy = DiscourseConnectAuthStrategy(self.settings, self.provider)
        elif self.settings["forum_provider"] == "discourse" and self.settings["forum_auth_strategy"] == "oauth2_provider":
            self.provider = DiscourseConnectProvider(self.settings)
            self.auth_strategy = OAuth2ProviderAuthStrategy()

    @property
    def config_errors(self):
        errors = []
        if not self.settings["forum_integration_enabled"]:
            return errors
        required_keys = ["forum_base_url", "forum_provider", "forum_auth_strategy", "discourse_api_key", "discourse_api_username", "discourse_connect_secret"]
        for key in required_keys:
            if not self.settings.get(key):
                errors.append(key)
        return errors

    def is_enabled(self):
        return self.settings["forum_integration_enabled"]

    def is_ready(self):
        return self.is_enabled() and not self.config_errors and self.provider is not None and self.auth_strategy is not None

    def ensure_forum_account(self, user, member=None):
        forum_account = user.forum_account
        changed = False
        if forum_account is None:
            forum_account = ForumAccount(
                user=user,
                member=member,
                provider=self.settings["forum_provider"],
                external_id=str(user.id),
                state=FORUM_STATE_INACTIVE,
            )
            db.session.add(forum_account)
            changed = True
        if member is not None and forum_account.member_id != member.id:
            forum_account.member = member
            changed = True
        if forum_account.provider != self.settings["forum_provider"]:
            forum_account.provider = self.settings["forum_provider"]
            changed = True
        external_id = str(user.id)
        if forum_account.external_id != external_id:
            forum_account.external_id = external_id
            changed = True
        return forum_account, changed

    def get_current_approved_submission(self, member):
        if member is None:
            return None
        return db.session.execute(
            db.select(ForumAvatarSubmission)
            .where(
                ForumAvatarSubmission.member_id == member.id,
                ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_APPROVED,
            )
            .order_by(ForumAvatarSubmission.uploaded_at.desc())
        ).scalars().first()

    def get_pending_submission(self, member):
        if member is None:
            return None
        return db.session.execute(
            db.select(ForumAvatarSubmission)
            .where(
                ForumAvatarSubmission.member_id == member.id,
                ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING,
            )
            .order_by(ForumAvatarSubmission.uploaded_at.desc())
        ).scalars().first()

    def get_latest_submission(self, member):
        if member is None:
            return None
        return db.session.execute(
            db.select(ForumAvatarSubmission)
            .where(ForumAvatarSubmission.member_id == member.id)
            .order_by(ForumAvatarSubmission.uploaded_at.desc())
        ).scalars().first()

    def get_upload_request_limit(self):
        return get_avatar_request_limit(self.settings["forum_avatar_max_bytes"])

    def get_desired_state(self, member):
        if member is None or member.user is None or not member_has_active_membership(member):
            return FORUM_STATE_INACTIVE
        # A switched-off account leaves the forum too, and this is where that
        # actually happens: Discourse is a separate system holding its own
        # group memberships, so barring somebody here without syncing would
        # stop them reaching the portal while they carried on posting.
        if member.user.is_disabled:
            return FORUM_STATE_INACTIVE
        if self.get_current_approved_submission(member) is not None:
            return FORUM_STATE_ACTIVE
        return FORUM_STATE_ONBOARDING

    def build_forum_redirect(self, destination_path=None):
        if not self.is_ready():
            raise ForumProviderError("Forum integration is not configured yet.")
        return self.auth_strategy.build_forum_redirect(destination_path=destination_path)

    def sync_member(self, member):
        if member is None or member.user is None:
            return ForumSyncResult(changed=False, desired_state=None, forum_account=None, error="No linked user or member available for forum sync.")

        forum_account, changed = self.ensure_forum_account(member.user, member)
        desired_state = self.get_desired_state(member)

        if not self.is_enabled():
            if forum_account.state != FORUM_STATE_INACTIVE:
                forum_account.state = FORUM_STATE_INACTIVE
                changed = True
            return ForumSyncResult(changed=changed, desired_state=desired_state, forum_account=forum_account, error=None)

        if not self.is_ready():
            forum_account.last_error = "Forum integration is enabled but not fully configured."
            if forum_account.state != FORUM_STATE_SYNC_ERROR:
                forum_account.state = FORUM_STATE_SYNC_ERROR
                changed = True
            return ForumSyncResult(changed=changed or True, desired_state=desired_state, forum_account=forum_account, error=forum_account.last_error)

        approved_submission = self.get_current_approved_submission(member)
        avatar_url = self.provider.build_avatar_url(approved_submission) if approved_submission is not None else None

        try:
            self.provider.sync_user(
                forum_account,
                member.user,
                member,
                desired_state,
                avatar_url=avatar_url,
                avatar_force_update=False,
            )
            if forum_account.state != desired_state:
                forum_account.state = desired_state
                changed = True
            if forum_account.last_synced_email != member.user.email:
                forum_account.last_synced_email = member.user.email
                changed = True
            if forum_account.last_synced_username != member.user.forum_username:
                forum_account.last_synced_username = member.user.forum_username
                changed = True
            if forum_account.last_error:
                forum_account.last_error = None
                changed = True
            forum_account.last_synced_at = datetime.now(timezone.utc)
            changed = True
            return ForumSyncResult(changed=changed, desired_state=desired_state, forum_account=forum_account, error=None)
        except ForumProviderError as exc:
            forum_account.last_error = str(exc)
            forum_account.last_synced_at = datetime.now(timezone.utc)
            forum_account.state = FORUM_STATE_SYNC_ERROR
            return ForumSyncResult(changed=True, desired_state=desired_state, forum_account=forum_account, error=str(exc))

    def create_avatar_submission(self, upload, user, member, crop_options=None):
        if upload is None or not getattr(upload, "filename", ""):
            raise ForumProviderError("Please choose an image file to upload.")

        allowed_extensions = _normalize_allowed_extensions(self.settings["forum_avatar_allowed_types"])
        request_limit = self.get_upload_request_limit()
        raw_bytes = read_limited_upload_bytes(upload, request_limit)
        if not raw_bytes:
            raise ForumProviderError("The uploaded image was empty.")

        normalized_bytes, content_type, file_extension = normalize_avatar_image(
            raw_bytes,
            allowed_extensions=allowed_extensions,
            max_output_bytes=self.settings["forum_avatar_max_bytes"],
            crop_options=crop_options,
        )

        safe_name = secure_filename(upload.filename or "avatar")
        storage_dir = get_forum_storage_dir()
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / f"avatar-{user.id}-{secrets.token_hex(12)}.{file_extension}"
        storage_path.write_bytes(normalized_bytes)

        pending_submissions = db.session.execute(
            db.select(ForumAvatarSubmission)
            .where(
                ForumAvatarSubmission.member_id == member.id,
                ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_PENDING,
            )
        ).scalars().all()
        for existing in pending_submissions:
            existing.status = FORUM_AVATAR_STATUS_SUPERSEDED
            delete_submission_file(existing, clear_reference=True)

        submission = ForumAvatarSubmission(
            user=user,
            member=member,
            status=FORUM_AVATAR_STATUS_PENDING,
            original_filename=safe_name or f"avatar.{file_extension}",
            content_type=content_type,
            file_size=len(normalized_bytes),
            file_hash=hashlib.sha256(normalized_bytes).hexdigest(),
            storage_path=str(storage_path),
            public_token=secrets.token_urlsafe(24),
        )
        db.session.add(submission)
        return submission

    def approve_avatar_submission(self, submission, reviewer=None, review_note=None):
        if submission.status != FORUM_AVATAR_STATUS_PENDING:
            raise ForumProviderError("Only pending avatar submissions can be approved.")
        if not submission.member or not submission.member.user:
            raise ForumProviderError("This avatar submission is not linked to a valid member account.")
        if not submission.storage_path or not Path(submission.storage_path).exists():
            raise ForumProviderError("The uploaded avatar file could not be found on the server.")

        forum_account, _ = self.ensure_forum_account(submission.user, submission.member)
        if not self.is_ready():
            raise ForumProviderError("Forum integration is not configured yet.")

        try:
            self.provider.sync_user(forum_account, submission.user, submission.member, FORUM_STATE_ONBOARDING)
            self.provider.set_avatar(forum_account, submission.user, submission)
        except ForumProviderError as exc:
            submission.sync_error = str(exc)
            forum_account.last_error = str(exc)
            forum_account.state = FORUM_STATE_SYNC_ERROR
            forum_account.last_synced_at = datetime.now(timezone.utc)
            return ForumSyncResult(changed=True, desired_state=FORUM_STATE_ONBOARDING, forum_account=forum_account, error=str(exc))

        previous_approved = db.session.execute(
            db.select(ForumAvatarSubmission)
            .where(
                ForumAvatarSubmission.member_id == submission.member_id,
                ForumAvatarSubmission.status == FORUM_AVATAR_STATUS_APPROVED,
                ForumAvatarSubmission.id != submission.id,
            )
        ).scalars().all()
        for old_submission in previous_approved:
            old_submission.status = FORUM_AVATAR_STATUS_SUPERSEDED
            delete_submission_file(old_submission, clear_reference=True)

        submission.status = FORUM_AVATAR_STATUS_APPROVED
        submission.reviewed_by = reviewer
        submission.reviewed_at = datetime.now(timezone.utc)
        submission.review_note = review_note
        submission.sync_error = None
        submission.forum_synced_at = datetime.now(timezone.utc)

        return self.sync_member(submission.member)

    def reject_avatar_submission(self, submission, reviewer=None, review_note=None):
        if submission.status != FORUM_AVATAR_STATUS_PENDING:
            raise ForumProviderError("Only pending avatar submissions can be rejected.")
        submission.status = FORUM_AVATAR_STATUS_REJECTED
        submission.reviewed_by = reviewer
        submission.reviewed_at = datetime.now(timezone.utc)
        submission.review_note = review_note
        submission.sync_error = None
        delete_submission_file(submission, clear_reference=True)
        if submission.member is not None:
            return self.sync_member(submission.member)
        return ForumSyncResult(changed=True, desired_state=None, forum_account=submission.user.forum_account if submission.user else None, error=None)

    def test_connection(self):
        if not self.is_ready():
            missing = ", ".join(self.config_errors)
            return False, f"Forum integration is not fully configured: {missing}"
        return self.provider.test_connection()

    def handle_provider_request(self, request_args, user, member):
        if not self.is_ready():
            raise ForumProviderError("Forum integration is not configured yet.")
        return self.auth_strategy.handle_provider_request(request_args, user, member, self)

    def log_out_user(self, user):
        if user is None or user.forum_account is None or not self.is_ready():
            return False, None
        try:
            result = self.provider.log_out_user(user.forum_account, user)
            return bool(result), None
        except ForumProviderError as exc:
            if user.forum_account is not None:
                user.forum_account.last_error = str(exc)
                user.forum_account.last_synced_at = datetime.now(timezone.utc)
            return False, str(exc)

    def anonymize_user(self, user):
        """Anonymise the member's forum identity. Returns (done, error)."""
        if user is None or user.forum_account is None:
            return False, None
        if not self.is_ready():
            # Not an error the caller should retry forever: with the integration
            # switched off there is no remote account to anonymise.
            return False, None
        try:
            result = self.provider.anonymize_user(user.forum_account, user)
            return bool(result), None
        except ForumProviderError as exc:
            user.forum_account.last_error = str(exc)
            user.forum_account.last_synced_at = datetime.now(timezone.utc)
            return False, str(exc)


def normalize_forum_settings(settings_map):
    values = dict(FORUM_SETTING_DEFAULTS)
    values.update(settings_map or {})
    values["forum_integration_enabled"] = normalize_bool(values.get("forum_integration_enabled"))
    values["forum_provider"] = (values.get("forum_provider") or "discourse").strip() or "discourse"
    values["forum_auth_strategy"] = (values.get("forum_auth_strategy") or "discourse_connect").strip() or "discourse_connect"
    values["forum_base_url"] = (values.get("forum_base_url") or "").strip().rstrip("/")
    values["discourse_api_key"] = (values.get("discourse_api_key") or "").strip()
    values["discourse_api_username"] = (values.get("discourse_api_username") or "").strip()
    values["discourse_connect_secret"] = (values.get("discourse_connect_secret") or "").strip()
    values["forum_onboarding_group"] = (values.get("forum_onboarding_group") or "").strip()
    values["forum_member_group"] = (values.get("forum_member_group") or "").strip()
    values["forum_inactive_group"] = (values.get("forum_inactive_group") or "").strip()
    values["forum_staff_group"] = (values.get("forum_staff_group") or "").strip()
    values["forum_category_groups"] = (values.get("forum_category_groups") or "").strip()
    values["forum_lecture_groups"] = (values.get("forum_lecture_groups") or "").strip()
    values["forum_archive_groups"] = (values.get("forum_archive_groups") or "").strip()
    values["forum_onboarding_path"] = (values.get("forum_onboarding_path") or "/").strip() or "/"
    values["forum_avatar_max_bytes"] = normalize_int(values.get("forum_avatar_max_bytes"), 5 * 1024 * 1024)
    values["forum_avatar_allowed_types"] = [
        item.strip().lower()
        for item in str(values.get("forum_avatar_allowed_types") or "jpg,jpeg,png,webp").split(",")
        if item.strip()
    ]
    return values



def normalize_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}



def normalize_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def member_has_active_membership(member):
    if member is None:
        return False
    # Use the same date-based access rule as the rest of the app so the forum and
    # the site never disagree: coverage must not have lapsed, regardless of the
    # possibly-stale is_active flag.
    return member_has_active_access(member)



def get_forum_storage_dir():
    return Path(current_app.root_path).parent / "storage" / "forum_avatar_staging"



def delete_submission_file(submission, clear_reference=False):
    storage_path = getattr(submission, "storage_path", None)
    if storage_path:
        try:
            path = Path(storage_path)
            if path.exists():
                path.unlink()
        except Exception:
            pass
    if clear_reference:
        submission.storage_path = None
        submission.public_token = None

