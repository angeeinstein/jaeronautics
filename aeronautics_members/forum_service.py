import base64
import hashlib
import hmac
import json
import secrets
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
    from .security_utils import build_public_url
except ImportError:
    from db_models import ForumAccount, ForumAvatarSubmission, Member, User, db
    from security_utils import build_public_url

FORUM_STATE_INACTIVE = "inactive"
FORUM_STATE_ONBOARDING = "onboarding"
FORUM_STATE_ACTIVE = "active"
FORUM_STATE_SYNC_ERROR = "sync_error"

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
    "forum_onboarding_group": "member-onboarding",
    "forum_member_group": "members",
    "forum_inactive_group": "",
    "forum_onboarding_path": "/",
    "forum_avatar_max_bytes": str(5 * 1024 * 1024),
    "forum_avatar_allowed_types": "jpg,jpeg,png,webp",
}
FORUM_SETTING_KEYS = tuple(FORUM_SETTING_DEFAULTS.keys())

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


def _load_image_for_processing(raw_bytes):
    if Image is None or ImageOps is None:
        raise ForumProviderError("Avatar processing is unavailable because Pillow is not installed on the server yet.")
    try:
        with Image.open(BytesIO(raw_bytes)) as image:
            processed = ImageOps.exif_transpose(image)
            processed.load()
            if processed.width * processed.height > 25_000_000:
                raise ForumProviderError("The uploaded image is too large to process safely. Please choose a smaller image.")
            return processed
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

    def sync_user(self, forum_account, user, member, desired_state):
        raise NotImplementedError

    def test_connection(self):
        raise NotImplementedError

    def set_avatar(self, forum_account, user, submission):
        raise NotImplementedError

    def log_out_user(self, forum_account, user):
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
            "User-Agent": "JoanneumAeronauticsForumSync/1.0 (+https://testmembers.joanneum-aeronautics.at)",
        }

    def _request(self, method, path, data=None, json_body=None):
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

    def _build_group_fields(self, desired_state):
        onboarding_group = self.settings.get("forum_onboarding_group", "").strip()
        member_group = self.settings.get("forum_member_group", "").strip()
        inactive_group = self.settings.get("forum_inactive_group", "").strip()

        add_groups = []
        remove_groups = []
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

    def build_sso_payload(self, user, member, desired_state, nonce):
        full_name = f"{member.first_name} {member.last_name}".strip() if member else (user.email or "")
        payload = {
            "nonce": nonce,
            "external_id": str(user.id),
            "email": user.email,
            "username": user.forum_username or f"member-{user.id}",
            "name": full_name,
            "require_activation": "false",
        }
        payload.update(self._build_group_fields(desired_state))
        return payload

    def sync_user(self, forum_account, user, member, desired_state):
        payload = self.build_sso_payload(user, member, desired_state, nonce=f"sync-{user.id}-{int(datetime.now(timezone.utc).timestamp())}")
        encoded, signature = self._sign_sso_payload(payload)
        self._request(
            "POST",
            "/admin/users/sync_sso",
            data={"sso": encoded, "sig": signature},
        )
        remote_user = self.get_remote_user_by_external_id(forum_account.external_id)
        forum_account.remote_user_id = remote_user.get("id") or forum_account.remote_user_id
        return remote_user

    def get_remote_user_by_external_id(self, external_id):
        response = self._request("GET", f"/u/by-external/{quote(str(external_id))}.json")
        if isinstance(response, dict) and isinstance(response.get("user"), dict):
            return response["user"]
        return response if isinstance(response, dict) else {}

    def test_connection(self):
        response = self._request("GET", "/site.json")
        site_name = response.get("site_name") if isinstance(response, dict) else None
        if site_name:
            return True, f"Connected to Discourse site '{site_name}'."
        return True, "Connected to Discourse successfully."

    def set_avatar(self, forum_account, user, submission):
        if not forum_account.remote_user_id:
            remote_user = self.get_remote_user_by_external_id(forum_account.external_id)
            forum_account.remote_user_id = remote_user.get("id")
        if not forum_account.remote_user_id:
            raise ForumProviderError("Could not determine the Discourse user id for the avatar upload.")

        public_url = build_public_url("forum_avatar_public_file", token=submission.public_token)
        upload_response = self._request(
            "POST",
            "/uploads.json",
            data={
                "type": "avatar",
                "user_id": str(forum_account.remote_user_id),
                "synchronous": "true",
                "url": public_url,
            },
        )
        upload_id = (
            upload_response.get("id")
            or upload_response.get("upload_id")
            or (upload_response.get("upload") or {}).get("id")
        )
        if not upload_id:
            raise ForumProviderError("Discourse did not return an upload id for the avatar upload.")

        username = user.forum_username or f"member-{user.id}"
        self._request(
            "PUT",
            f"/u/{quote(username)}/preferences/avatar/pick.json",
            data={"upload_id": str(upload_id), "type": "uploaded"},
        )
        return upload_id

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
        provider_payload = self.provider.build_sso_payload(user, member, desired_state, nonce=nonce)
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

        try:
            self.provider.sync_user(forum_account, member.user, member, desired_state)
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
        delete_submission_file(submission, clear_reference=True)

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
    return bool(member and member.is_active)



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

