"""Putting one old thread onto the new forum, to find out whether it can be done.

This is a spike, not the migration. It moves a single thread -- its posts, its
authors, its dates and its attachments -- so that the questions the real
importer depends on get answered by the forum itself rather than by reasoning:

1. **Do the dates survive?** A post is created by asking Discourse as the person
   who wrote it, and a date is asked for at the same time. Whether it honours
   that when the asking is done on somebody else's behalf decides the entire
   shape of the migration: the archive spans 2014 to 2026, and a thread that
   arrives dated today is worse than one that never arrives.
2. **Can an imported person post at all?** They exist, but they have never
   signed in, they are trust level 0, and their address is on a domain that
   cannot receive. Any of those could be a reason Discourse refuses.
3. Do attachments upload and attach, does the BBCode read sensibly afterwards,
   and does each post land under the right name.

Nothing here is idempotent. Re-running it posts the thread again. The real
importer needs a record of which MyBB post became which Discourse post so that
a run can be resumed; a spike that is about to be thrown away does not.
"""

import json
import mimetypes
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import current_app

from ..forum_service import DISCOURSE_USER_AGENT, ForumProviderError

# Vienna, like the exporter: MyBB stored UTC and displayed local, so reading
# these as UTC would move every late-evening post a day earlier than the board
# has shown for a decade.
BOARD_TIMEZONE = "Europe/Vienna"

# The vocabulary that counts as markup. Anything else in square brackets is
# somebody's words.
_FORMATTING_TAGS = (
    "b", "i", "u", "s", "size", "color", "font", "align", "bg", "highlight",
    "list", "quote", "url", "img", "code", "hr", "center", "left", "right",
    "indent", "spoiler", "table", "tr", "td",
)


# ---------------------------------------------------------------------------
# BBCode
# ---------------------------------------------------------------------------

# What the real export actually contains, by frequency: color 484, size 434,
# b 428, attachment 145, u 104, list 78, quote 62, i 42, font 38, hr 37,
# url 32, align 17.
#
# color, size, font and align have no Markdown equivalent and are dropped to
# their contents. That is a deliberate loss: they are somebody's 2014 formatting
# choices, not information, and inventing HTML to preserve them would make every
# imported post a special case for ever.

def _convert_lists(text):
    def one_list(match):
        items = re.findall(r"\[\*\]\s*(.*?)(?=\[\*\]|\Z)", match.group(1), re.S)
        return "\n" + "\n".join(f"- {item.strip()}" for item in items if item.strip()) + "\n"
    return re.sub(r"\[list[^\]]*\](.*?)\[/list\]", one_list, text, flags=re.S | re.I)


# MyBB does not write [quote=Name]. It writes
# [quote='Name' pid='112' dateline='1394119460'] -- so taking everything after
# the "=" put the post id and a unix timestamp into the attribution line of
# every quoted post on the board.
_QUOTE_AUTHOR = re.compile(r"""=\s*['"]?([^'"\]]+?)['"]?(?:\s+\w+=|\s*$)""", re.S)


def _quote_author(raw):
    if not raw:
        return ""
    match = _QUOTE_AUTHOR.match(raw.strip())
    return (match.group(1) if match else raw.strip(" ='\"")).strip()


def _convert_quotes(text):
    def one_quote(match):
        who = _quote_author(match.group(1))
        body = match.group(2).strip()
        quoted = "\n".join(f"> {line}" for line in body.splitlines())
        return (f"\n> **{who}**\n{quoted}\n" if who else f"\n{quoted}\n")
    # Innermost first, so nested quotes unwrap rather than swallowing each other.
    pattern = re.compile(r"\[quote(=[^\]]*)?\]((?:(?!\[quote).)*?)\[/quote\]", re.S | re.I)
    while pattern.search(text):
        text = pattern.sub(one_quote, text)
    return text


def bbcode_to_markdown(text):
    """Good enough to read. Not a parser, and not trying to be."""
    text = (text or "").replace("\r\n", "\n")

    text = _convert_quotes(text)
    text = _convert_lists(text)

    text = re.sub(r"\[code\](.*?)\[/code\]", r"\n```\n\1\n```\n", text, flags=re.S | re.I)
    text = re.sub(r"\[b\](.*?)\[/b\]", r"**\1**", text, flags=re.S | re.I)
    text = re.sub(r"\[i\](.*?)\[/i\]", r"*\1*", text, flags=re.S | re.I)
    text = re.sub(r"\[u\](.*?)\[/u\]", r"\1", text, flags=re.S | re.I)
    text = re.sub(r"\[s\](.*?)\[/s\]", r"~~\1~~", text, flags=re.S | re.I)
    text = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", r"[\2](\1)", text, flags=re.S | re.I)
    text = re.sub(r"\[url\](.*?)\[/url\]", r"\1", text, flags=re.S | re.I)
    text = re.sub(r"\[img[^\]]*\](.*?)\[/img\]", r"![](\1)", text, flags=re.S | re.I)
    text = re.sub(r"\[hr\]", "\n---\n", text, flags=re.I)

    # Formatting with no counterpart: keep the words, drop the decoration.
    for tag in ("color", "size", "font", "align", "bg", "highlight"):
        text = re.sub(rf"\[{tag}[^\]]*\](.*?)\[/{tag}\]", r"\1", text, flags=re.S | re.I)

    # And whatever was never closed. A decade of posts contains plenty of
    # [size=4] with no [/size], which the paired passes above cannot see -- and
    # a stray tag left in the text reads as a mistake rather than as styling.
    #
    # Only tags that are actually BBCode. "[LAV19]" and "[20140326]" appear in
    # real posts as ordinary text and must survive: stripping anything inside
    # brackets would quietly edit what people wrote.
    text = re.sub(
        rf"\[/?(?:{'|'.join(_FORMATTING_TAGS)})(?:=[^\]]*)?\]", "", text, flags=re.I
    )

    return text.strip()


# ---------------------------------------------------------------------------
# Talking to Discourse
# ---------------------------------------------------------------------------

class ContentPoster:
    """The few API calls this needs, as the person who wrote each post."""

    def __init__(self, settings):
        self.base_url = settings["forum_base_url"].rstrip("/")
        self.api_key = settings["discourse_api_key"]
        self.admin_username = settings["discourse_api_username"]

    def _call(self, method, path, *, as_username=None, json_body=None, body=None, content_type=None):
        headers = {
            "Api-Key": self.api_key,
            "Api-Username": as_username or self.admin_username,
            "Accept": "application/json",
            # Without this Cloudflare refuses the request outright, and the
            # error looks like Discourse saying no.
            "User-Agent": DISCOURSE_USER_AGENT,
        }
        data = body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type

        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=120) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code == 403 and "invalid_access" in detail and as_username:
                # Discourse keys carry a user level. One bound to a single user
                # works perfectly for every admin call -- they all act as that
                # user -- and fails the moment it is asked to act as somebody
                # else, which is the whole of this job.
                raise ForumProviderError(
                    f"Discourse will not let this API key act as {as_username}. "
                    "Posting on people's behalf needs a key whose user level is "
                    "'All Users' (Admin -> API -> Keys). Make one for the "
                    "migration, pass it as --api-key or in "
                    "DISCOURSE_MIGRATION_API_KEY, and revoke it afterwards -- a "
                    "key that can act as anybody should not outlive the job."
                ) from exc
            raise ForumProviderError(f"{method} {path} failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise ForumProviderError(f"Could not reach Discourse: {exc}") from exc

    def upload(self, path, as_username, filename=None, content_type=None):
        """Upload one file as that person. Returns Discourse's upload record.

        ``filename`` matters more than it looks. MyBB stores every upload as
        ``post_<pid>_<time>_<hash>.attach`` -- the original bytes, renamed so
        the webserver cannot serve or execute them. The name somebody actually
        chose, and its type, are columns in the database.

        Sending the on-disk name would offer Discourse a file called
        ``....attach``, which it refuses because that extension is not in
        authorized_extensions, and which would be meaningless to read even if
        it accepted it. What goes up is the original name and the original
        type; only the bytes come from the file.
        """
        path = Path(path)
        display_name = filename or path.name
        boundary = f"----jaeronautics{secrets.token_hex(16)}"
        mime = (
            content_type
            or mimetypes.guess_type(display_name)[0]
            or "application/octet-stream"
        )

        parts = []
        for name, value in (("type", "composer"), ("synchronous", "true")):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                .encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{display_name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        )
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        return self._call(
            "POST", "/uploads.json", as_username=as_username,
            body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}",
        )

    def create_post(self, *, raw, as_username, created_at, category=None,
                    title=None, topic_id=None):
        payload = {"raw": raw, "created_at": created_at}
        if topic_id:
            payload["topic_id"] = topic_id
        else:
            payload["title"] = title
            payload["category"] = category
        return self._call("POST", "/posts.json", as_username=as_username, json_body=payload)

    def read_post(self, post_id):
        return self._call("GET", f"/posts/{quote(str(post_id))}.json")


# ---------------------------------------------------------------------------
# Moving one thread
# ---------------------------------------------------------------------------

def _as_iso(dateline):
    """A MyBB timestamp as the instant it actually was."""
    return datetime.fromtimestamp(int(dateline), tz=timezone.utc).isoformat()


def _attachment_markdown(upload, original_name):
    """How Discourse refers to an upload from inside a post."""
    short_url = upload.get("short_url") or upload.get("url")
    if (upload.get("extension") or "").lower() in {"png", "jpg", "jpeg", "gif", "webp"}:
        return f"![{original_name}]({short_url})"
    size = upload.get("human_filesize") or ""
    return f"[{original_name}|attachment]({short_url}) {size}".strip()


def migrate_thread(poster, thread, posts, attachments_by_post, usernames_by_uid,
                   uploads_dir, category_id, *, dry_run=False, fallback_username=None):
    """Post one old thread onto the forum. Returns a report.

    Every post is sent as its own author with its own date, and the report says
    what Discourse recorded against what was asked for -- which is the entire
    reason this exists.
    """
    report = {"thread": thread.get("subject"), "posts": [], "problems": [], "topic_id": None}
    uploads_dir = Path(uploads_dir)

    # Posting the whole thread under one name is not a migration, it is a
    # mistake wearing one. It already happened once: "users" matched a plugin's
    # table, every lookup missed, and the fallback quietly took the lot.
    resolved = sum(1 for post in posts if usernames_by_uid.get(post.get("uid")))
    if not resolved:
        raise ValueError(
            "Not one of these posts could be matched to a forum account. "
            "The author lookup is empty or reading the wrong table -- "
            "check that the users table was found, rather than a plugin's."
        )
    if resolved < len(posts):
        report["problems"].append(
            f"{len(posts) - resolved} of {len(posts)} posts have no forum account "
            "for their author and will be attributed to the fallback."
        )

    for index, post in enumerate(posts):
        author = usernames_by_uid.get(post.get("uid")) or fallback_username
        asked_for = _as_iso(post["dateline"])
        record = {
            "pid": post.get("pid"),
            "author": author,
            "asked_for": asked_for,
            "recorded": None,
            "attachments": 0,
            "result": "",
        }
        report["posts"].append(record)

        if not author:
            record["result"] = "skipped: no forum account for this author"
            report["problems"].append(f"pid={post.get('pid')}: no author")
            continue

        body = bbcode_to_markdown(post.get("message"))

        # Attachments first: a post referring to an upload has to be written
        # after the upload exists.
        links = []
        for attachment in attachments_by_post.get(post.get("pid"), []):
            source = uploads_dir / (attachment.get("attachname") or "")
            original = attachment.get("filename") or source.name
            if not source.exists():
                record["result"] = "missing file"
                report["problems"].append(f"pid={post.get('pid')}: {source} is not there")
                continue
            if dry_run:
                links.append(f"[{original}|attachment](upload://would-be-uploaded)")
                record["attachments"] += 1
                continue
            try:
                upload = poster.upload(
                    source, author,
                    filename=original,
                    content_type=attachment.get("filetype") or None,
                )
            except ForumProviderError as exc:
                report["problems"].append(f"pid={post.get('pid')} {original}: {exc}")
                continue
            links.append(_attachment_markdown(upload, original))
            record["attachments"] += 1

        if links:
            # Appended, because 94% of these were never referenced in the text:
            # MyBB simply listed them under the post.
            body = f"{body}\n\n{chr(10).join(links)}" if body else "\n".join(links)

        # An inline [attachment=N] is rare (145 across the whole board) and is
        # left as-is rather than guessed at, so it shows up when read.
        if dry_run:
            record["result"] = "would post"
            continue

        try:
            if index == 0:
                created = poster.create_post(
                    raw=body, as_username=author, created_at=asked_for,
                    title=thread.get("subject") or "(no subject)", category=category_id,
                )
                report["topic_id"] = created.get("topic_id")
            else:
                created = poster.create_post(
                    raw=body, as_username=author, created_at=asked_for,
                    topic_id=report["topic_id"],
                )
        except ForumProviderError as exc:
            record["result"] = f"failed: {exc}"
            report["problems"].append(f"pid={post.get('pid')}: {exc}")
            continue

        record["result"] = "posted"
        record["post_id"] = created.get("id")
        # What Discourse actually stored, read back rather than assumed.
        record["recorded"] = created.get("created_at")

    current_app.logger.info("Spike: moved thread %s", thread.get("tid"))
    return report


def dates_survived(report):
    """Did Discourse keep the dates it was given? The question this is for."""
    checked = [
        person for person in report["posts"]
        if person.get("recorded") and person.get("asked_for")
    ]
    if not checked:
        return None, "nothing was posted, so nothing can be said"

    def day(value):
        return (value or "")[:10]

    kept = [p for p in checked if day(p["recorded"]) == day(p["asked_for"])]
    if len(kept) == len(checked):
        return True, f"all {len(checked)} posts kept the date they were given"
    return False, (
        f"{len(kept)} of {len(checked)} kept their date; the rest were "
        "recorded as something else, most likely today"
    )
