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
import time
from collections import Counter, namedtuple
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

# Discourse's own rate limits are loosened for the import window, but not all
# of them are settings and none of them can be relied on to be off. When it
# says to wait, waiting is the fix: dropping the post instead loses it, since
# the settings go back at the end of the run and there is no way to ask for a
# re-run of only what failed.
RATE_LIMIT_RETRIES = 5

# What stands in for a post that had nothing in it on the old board. Discourse
# will not store an empty body, and leaving the post out would take its date
# and its place in the conversation with it -- a reply to it would then answer
# nothing. Said plainly, in the archive's own terms.
EMPTY_POST = "*(This post was empty on the old forum.)*"

# And what is added to a post Discourse counts as empty although it is not.
# It strips emoji shortcodes before measuring length, so ":f16:" -- the old
# board's F-16 smiley, five characters of it -- is nothing at all to Discourse,
# and no value of min_post_length will make it acceptable. The smiley is kept
# and this is added beneath it, rather than the post being rewritten.
SMILEY_ONLY_POST = "*(This post was only a smiley on the old forum.)*"

# Discourse's emoji shortcodes, which it removes before it measures a post.
SHORTCODE = re.compile(r":[a-z0-9_+-]{1,40}:", re.I)


def what_discourse_counts(body):
    """What is left of a post once Discourse has taken out what it ignores.

    Emoji shortcodes and whitespace. A post of nothing else is refused as too
    short whatever the minimum is set to, and the refusal quotes a minimum the
    post appears to meet -- "Body is too short (minimum is 2 characters)"
    against five characters of ``:f16:``.
    """
    return SHORTCODE.sub("", body or "").strip()
RATE_LIMIT_FALLBACK_WAIT = 30   # when Discourse does not say how long
RATE_LIMIT_MAX_WAIT = 300       # past which something else is wrong

#: Attachments Discourse renders inline rather than as a link. It counts those
#: against newuser_max_embedded_media and the rest against newuser_max_links,
#: so what an attachment becomes decides which limit it is measured by.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})

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

    #: Where this Discourse keeps the site settings. The admin JSON routes have
    #: moved between versions -- reading the custom user fields already needs
    #: three candidates -- so the path is found rather than assumed.
    SITE_SETTINGS_PATHS = (
        "/admin/site_settings.json",
        "/admin/config/site_settings.json",
        "/admin/site_settings/category/all_results.json",
    )

    def __init__(self, settings):
        self.base_url = settings["forum_base_url"].rstrip("/")
        # Stripped, because these come out of a file often enough. A trailing
        # newline in an HTTP header value is not a small mistake: it either
        # raises somewhere far from here or changes the request, and neither
        # says anything about a stray character at the end of a key.
        self.api_key = (settings["discourse_api_key"] or "").strip()
        self.admin_username = (settings["discourse_api_username"] or "").strip()
        self._settings_base = None
        #: Everything the last settings read returned, not only name and value.
        #: What this forum will and will not accept is described in there, and
        #: guessing at it instead has now cost two runs.
        self.last_settings_rows = []
        #: Given the name the old board used, what this forum calls that person
        #: -- or None. Set by the caller, which is the part that knows how the
        #: two are tied together. Left unset, a name the forum does not have is
        #: simply reported, as it was before.
        self.find_author = None
        self._known_names = {}

    def _known_by(self, username):
        """What this forum calls the person the old board called ``username``.

        Asked only when a post has already been refused, so it costs nothing on
        a board whose names all fit -- and on this one it is four lookups
        against fourteen hundred posts.
        """
        if username in self._known_names:
            return self._known_names[username]
        found = None
        if self.find_author is not None:
            try:
                found = self.find_author(username)
            except Exception as exc:  # noqa: BLE001 -- a lookup must not end a run
                current_app.logger.warning(
                    "Could not look up %s on the forum: %s", username, exc
                )
        self._known_names[username] = found
        if found and found != username:
            current_app.logger.info(
                "The forum knows %s as %s", username, found
            )
        return found

    def username_for_external_id(self, external_id):
        """The forum's own name for the account carrying this external id.

        The portal's user id is what every imported profile was published under,
        so it is the one handle that survives Discourse rewriting a username.
        """
        payload = self._call(
            "GET", f"/u/by-external/{quote(str(external_id))}.json"
        )
        user = payload.get("user") if isinstance(payload, dict) else None
        return (user or {}).get("username")

    def _key_complaint(self):
        """Whether the key is the wrong shape, said plainly. None if it is fine.

        Worth checking before blaming anything else. A Discourse API key is 64
        hexadecimal characters, and a key Discourse does not recognise makes the
        request anonymous rather than refused -- after which every admin route
        answers 404, because Discourse hides them from people who are not staff
        rather than admitting they exist. A truncated key therefore looks
        exactly like a missing feature.
        """
        key = (self.api_key or "").strip()
        if not key:
            return "no API key was given at all"
        if len(key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in key):
            return (
                f"the API key is {len(key)} characters and Discourse's are 64 "
                f"hexadecimal ones, so this one looks truncated or mistyped -- "
                f"a partly-pasted key is the usual cause"
            )
        return None

    def _call(self, method, path, *, as_username=None, json_body=None, body=None,
              content_type=None, rate_limit_retries=RATE_LIMIT_RETRIES):
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
                # Two very different things arrive here as the same 403.
                #
                # The key may be bound to a single user, in which case every
                # admin call works -- they all act as that user -- and the first
                # attempt to act as somebody else fails.
                #
                # Or the name may simply not be a user on this forum. Discourse
                # caps a username at twenty characters and adjusts anything
                # longer when it creates the account, so the board's
                # NiedergrottenthalerR_L12 is somebody else there, and asking to
                # post as the old name is asking for a stranger.
                fixed = self._known_by(as_username)
                if fixed and fixed != as_username:
                    return self._call(
                        method, path, as_username=fixed, json_body=json_body,
                        body=body, content_type=content_type,
                        rate_limit_retries=rate_limit_retries,
                    )
                raise ForumProviderError(
                    f"Discourse will not let this API key act as {as_username}. "
                    f"Either the key is bound to one user -- posting on people's "
                    f"behalf needs one whose user level is 'All Users' (Admin -> "
                    f"API -> Keys), and revoke it afterwards -- or there is "
                    f"nobody of that name on the forum, which is what happens to "
                    f"a username past Discourse's twenty-character limit: the "
                    f"account exists under a name it shortened."
                ) from exc
            if exc.code == 429 and rate_limit_retries > 0:
                # Discourse says exactly how long to wait, so waiting is the
                # whole fix. Dropping the post instead loses it: the settings
                # go back at the end of the run, and the person re-running it
                # has no way to ask for only the posts that failed.
                wait = self._wait_seconds(detail)
                current_app.logger.info(
                    "Discourse rate limit on %s, waiting %ss", path, wait
                )
                time.sleep(wait)
                return self._call(
                    method, path, as_username=as_username, json_body=json_body,
                    body=body, content_type=content_type,
                    rate_limit_retries=rate_limit_retries - 1,
                )
            raise ForumProviderError(f"{method} {path} failed ({exc.code}): {detail}") from exc

        except URLError as exc:
            raise ForumProviderError(f"Could not reach Discourse: {exc}") from exc

    @staticmethod
    def _wait_seconds(detail):
        """How long Discourse asked to be left alone for, within reason."""
        try:
            asked = json.loads(detail).get("extras", {}).get("wait_seconds")
        except (ValueError, AttributeError):
            asked = None
        return min(max(int(asked or RATE_LIMIT_FALLBACK_WAIT), 1), RATE_LIMIT_MAX_WAIT)

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
        # Both names for the same thing. Discourse deprecated "type" in 3.4 and
        # removes it in 3.5 -- its own log says so on every upload this makes --
        # and versions before 3.4 do not know "upload_type". Sending both means
        # the import does not stop working on an upgrade, and does not need a
        # version check to decide.
        for name, value in (("type", "composer"),
                            ("upload_type", "composer"),
                            ("synchronous", "true")):
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

    def categories(self):
        """Every category the forum has, with its parent. Flat list.

        Read from /site.json rather than /categories.json because that one
        answers with the whole tree in one go and does not need paging through
        -- and a category missed by paging is a category made twice.
        """
        payload = self._call("GET", "/site.json")
        return payload.get("categories") or []

    def create_category(self, name, parent_id=None):
        """Make one category. Returns its id."""
        payload = {"name": name, "color": "0088CC", "text_color": "FFFFFF"}
        if parent_id:
            payload["parent_category_id"] = parent_id
        answer = self._call("POST", "/categories.json", json_body=payload)
        return (answer.get("category") or {}).get("id")

    def site_settings(self):
        """Every site setting and its current value, as {name: value}.

        Tries the paths this has been known to live at, and keeps the one that
        answers so that writing a setting back goes to the same place.
        """
        attempts = []
        for path in self.SITE_SETTINGS_PATHS:
            try:
                payload = self._call("GET", path)
            except ForumProviderError as exc:
                attempts.append(f"{path}: {exc}")
                continue
            rows = payload.get("site_settings") if isinstance(payload, dict) else None
            if rows is None:
                attempts.append(f"{path}: answered, but with no site settings in it")
                continue
            self._settings_base = path[: -len(".json")]
            self.last_settings_rows = [row for row in rows if row.get("setting")]
            return {
                row.get("setting"): row.get("value")
                for row in self.last_settings_rows
            }

        complaint = self._key_complaint()
        codes = {int(found) for found in re.findall(r"failed \((\d{3})\)", " ".join(attempts))}

        # A bad key and a forum that is not running look nothing alike, and
        # telling somebody their API user is not an administrator when the
        # forum answered 502 sends them looking in the wrong place. So the
        # advice follows what actually came back.
        if codes and not codes & {401, 403, 404}:
            raise ForumProviderError(
                f"The forum did not answer properly on any known settings path "
                f"(HTTP {', '.join(str(code) for code in sorted(codes))}). "
                f"That is the forum itself rather than this key or its "
                f"permissions -- a 502 usually means Discourse is not running, "
                f"which after ./launcher rebuild often means the rebuild asked "
                f"to be run a second time and has not been. Tried:\n  "
                + "\n  ".join(attempts)
            )
        if complaint:
            raise ForumProviderError(
                f"Could not read the forum's settings, and {complaint}. "
                f"Discourse makes a request with a key it does not recognise "
                f"an anonymous one, and answers 404 on every admin route to "
                f"anybody who is not staff -- so a bad key looks exactly like "
                f"a missing page. Check the key before anything else."
            )
        raise ForumProviderError(
            "Could not read the forum's settings from any known admin path "
            f"({', '.join(self.SITE_SETTINGS_PATHS)}). A 404 here usually means "
            f"{self.admin_username} is not an administrator on the forum, since "
            f"Discourse hides admin routes rather than refusing them. Tried:\n  "
            + "\n  ".join(attempts)
        )

    def set_site_setting(self, setting, value):
        """Change one site setting. Discourse names the field after itself."""
        if self._settings_base is None:
            # Reading first is how the path is discovered, and nothing changes
            # a setting without having read it anyway.
            self.site_settings()
        self._call(
            "PUT", f"{self._settings_base}/{quote(str(setting))}.json",
            json_body={setting: str(value)},
        )


# ---------------------------------------------------------------------------
# What the forum has to allow before any of this can land
# ---------------------------------------------------------------------------
#
# Discourse's defaults are written for people typing into a box today. They
# reject a good deal of what a decade-old board actually contains, and they do
# it one post at a time, hours into a run, which is the worst possible moment
# to find out. So the archive is measured first and the forum is asked what it
# currently allows, and the two are compared before anything is posted.
#
# Every one of these is a migration-window setting: loosened for the import and
# put back afterwards, exactly like disable_emails. The check prints the
# current value next to the needed one so there is a record of what to restore.

class Requirement(namedtuple("Requirement", "setting needed compare why restore blocks aliases")):
    """One thing the forum has to allow, and what to do about it.

    ``restore`` is false for the few settings that describe what this forum is
    for rather than what the import needs. A board whose purpose is sharing
    exam papers has to accept PDFs on the Monday after the import as much as
    during it, so narrowing the extensions again would break the new forum to
    tidy up after the old one.

    ``blocks`` separates the settings that make Discourse refuse a post from
    the ones that merely have an effect nobody wants. A title two characters
    too short is refused; title_prettify quietly rewrites what it is given, and
    mail goes out to addresses that stopped existing years ago. Both are worth
    changing; only the first is worth stopping for.

    ``aliases`` are the other names Discourse has called this setting. It
    renames them between versions, and a setting looked up under a name this
    forum has never heard of reads as unknown rather than as renamed --
    reported with a "?" and then quietly not changed, which is the worst of
    both. Whichever name the site actually reports is the one used.
    """

    __slots__ = ()

    def __new__(cls, setting, needed, compare, why, restore=True, blocks=True,
                aliases=()):
        return super().__new__(
            cls, setting, needed, compare, why, restore, blocks, tuple(aliases)
        )


#: ``compare`` says what the live value has to be, relative to ``needed``.
AT_MOST = "at_most"      # a floor Discourse enforces: it must not be higher
AT_LEAST = "at_least"    # a ceiling: it must not be lower
EQUALS = "equals"        # a switch
INCLUDES = "includes"    # a comma-separated list that must contain these


def _longest_run(posts):
    """The most posts one person made in a row in any one thread.

    Not the same as the most they made in it altogether, and Discourse counts
    them separately: max_consecutive_replies stops the fourth in a row at its
    default of three, however few the author has posted in the thread.
    """
    longest = 0
    by_thread = {}
    for post in posts:
        by_thread.setdefault(post.get("tid"), []).append(post)
    for in_thread in by_thread.values():
        in_thread.sort(key=lambda row: int(row.get("dateline") or 0))
        run, previous = 0, object()
        for post in in_thread:
            run = run + 1 if post.get("uid") == previous else 1
            previous = post.get("uid")
            longest = max(longest, run)
    return longest


def _post_bodies(posts):
    return {post.get("pid"): bbcode_to_markdown(post.get("message")) for post in posts}


def _entropy(text):
    """Discourse's measure of whether something is meaningful.

    TextSentinel counts the distinct characters and compares that against
    title_min_entropy or body_min_entropy. "Klausuren" scores 8 against a
    default of 10, so this is not academic: it refuses a good part of a real
    board for looking like noise.
    """
    return len(set(text or ""))


def plan_site_settings(threads, posts, attachments=()):
    """What this archive needs the forum to allow, measured from the archive.

    Nothing here is a guess about Discourse's defaults: each requirement says
    what the content needs, and the live value is read from the site and
    compared against it.
    """
    threads = list(threads)
    posts = list(posts)
    attachments = list(attachments)
    bodies = _post_bodies(posts)

    requirements = [
        # Not measured from anything -- it is true of every bulk import. The
        # imported people have addresses on a domain that no longer receives,
        # so a run with mail on means hundreds of bounces against this domain's
        # reputation, and everybody watching a category gets a decade of
        # notifications in one afternoon. Staff mail is left on so that whoever
        # is running this can still get a password reset.
        Requirement(
            "disable_emails", "non-staff", EQUALS,
            "otherwise the import sends notification mail for every post, to "
            "addresses that stopped existing years ago",
            blocks=False,
        ),
    ]

    titles = [(thread.get("subject") or "").strip() for thread in threads]
    titles = [title for title in titles if title]
    if titles:
        shortest = min(len(title) for title in titles)
        requirements.append(Requirement(
            "min_topic_title_length", shortest, AT_MOST,
            f"the shortest thread subject on the board is {shortest} characters",
        ))

        repeated = Counter(titles)
        clashing = sum(count for count in repeated.values() if count > 1)
        if clashing:
            worst, count = repeated.most_common(1)[0]
            requirements.append(Requirement(
                "allow_duplicate_topic_titles", "true", EQUALS,
                f"{clashing} threads share a subject with another thread "
                f"({worst!r} is used {count} times); with this off, only the "
                f"first of each name is accepted and the rest are refused",
            ))

        requirements.append(Requirement(
            "title_prettify", "false", EQUALS,
            "left on, Discourse rewrites the titles it is given -- capitalising "
            "them and stripping punctuation -- so the archive would not read as "
            "it did",
            blocks=False,
        ))

        requirements.append(Requirement(
            "max_topic_title_length", max(len(title) for title in titles),
            AT_LEAST,
            f"the longest thread subject is "
            f"{max(len(title) for title in titles)} characters",
        ))

        # German runs words together, and Discourse has a limit on how long
        # one word in a title may be. "Weisswurstfruehstueck" is ordinary here.
        longest_word = max(
            (len(word) for title in titles for word in title.split()), default=0
        )
        if longest_word:
            requirements.append(Requirement(
                "title_max_word_length", longest_word, AT_LEAST,
                f"the longest single word in any subject is {longest_word} "
                f"characters, which German subjects reach without trying",
            ))

        # A subject in capitals is refused as "did you mean to enter it in
        # ALL CAPS?", and a board this old has several.
        shouting = [
            title for title in titles
            if title.upper() == title and any(c.isalpha() for c in title)
        ]
        if shouting:
            requirements.append(Requirement(
                "allow_uppercase_posts", "true", EQUALS,
                f"{len(shouting)} subjects are written in capitals, such as "
                f"{shouting[0]!r}, and Discourse refuses those as shouting",
            ))

        plainest = min(_entropy(title) for title in titles)
        requirements.append(Requirement(
            "title_min_entropy", plainest, AT_MOST,
            f"the plainest subject uses only {plainest} distinct characters, "
            f"which is what Discourse measures to decide a title is not "
            f"meaningful",
        ))

    # Measured the way Discourse measures: it strips whitespace, and it removes
    # emoji shortcodes, so the board's ":f16:" smiley is five characters here
    # and none there. Both of those produced the same contradictory refusal --
    # "Body is too short (minimum is 2 characters)" about a post with more than
    # two in it. Anything that counts as nothing is sent with a marker, so the
    # floor of one below is a floor these posts really do meet.
    lengths = [len(what_discourse_counts(body)) for body in bodies.values()]
    if lengths:
        requirements.append(Requirement(
            "max_post_length", max(len(body) for body in bodies.values()), AT_LEAST,
            f"the longest post on the board is "
            f"{max(len(body) for body in bodies.values())} characters "
            f"after conversion",
        ))
        # Floored at one: a post with nothing in it is sent as EMPTY_POST
        # rather than as nothing, so the forum is never asked to accept an
        # empty body -- and asking Discourse for a minimum of zero is asking
        # for something it does not offer.
        shortest = max(min(lengths), 1)
        requirements.append(Requirement(
            "min_post_length", shortest, AT_MOST,
            f"the shortest post on the board is {min(lengths)} characters "
            f"after conversion",
        ))

        plainest = min(_entropy(body) for body in bodies.values())
        requirements.append(Requirement(
            "body_min_entropy", plainest, AT_MOST,
            f"the plainest post uses only {plainest} distinct characters",
        ))

    opening_pids = {thread.get("firstpost") for thread in threads}
    opening = [len(what_discourse_counts(body)) for pid, body in bodies.items()
               if pid in opening_pids]
    if opening:
        requirements.append(Requirement(
            "min_first_post_length", max(min(opening), 1), AT_MOST,
            f"the shortest thread-opening post is {min(opening)} characters",
        ))

    repeats = Counter(
        (post.get("uid"), bodies.get(post.get("pid"), "")) for post in posts
    )
    duplicated = sum(count - 1 for count in repeats.values() if count > 1)
    if duplicated:
        requirements.append(Requirement(
            "unique_posts_mins", 0, AT_MOST,
            f"{duplicated} posts repeat something their own author had already "
            f"written; during an import they all arrive within minutes of each "
            f"other, so Discourse reads them as accidental double-posts",
        ))

    # Everybody posting is a brand new account, created minutes ago, at trust
    # level 0 -- exactly the shape Discourse's anti-spam guards are aimed at.
    # A decade of somebody's posting arrives in one afternoon.
    if posts:
        in_thread = Counter((post.get("uid"), post.get("tid")) for post in posts)
        most_in_one = max(in_thread.values())
        if most_in_one > 1:
            # Every post they make in the topic, not every post after their
            # first. Discourse counted thirteen where this counted twelve, and
            # refused the thirteenth with "temporarily limited to 12 replies".
            requirements.append(Requirement(
                "newuser_max_replies_per_topic", most_in_one, AT_LEAST,
                f"one person posted {most_in_one} times in a single thread, and "
                f"Discourse counts all of them against this",
            ))

        # Replies in a row, which is a different count from replies in total
        # and has its own limit. Somebody posting four exam papers one after
        # another is the ordinary way this board was used, and at the default
        # of three the fourth is refused.
        in_a_row = _longest_run(posts)
        if in_a_row > 1:
            requirements.append(Requirement(
                "max_consecutive_replies", in_a_row, AT_LEAST,
                f"somebody posted {in_a_row} times in a row in one thread, "
                f"which is counted separately from how many times they posted "
                f"in it altogether",
            ))

        opened = Counter(
            post.get("uid") for post in posts if post.get("pid") in opening_pids
        )
        if opened:
            requirements.append(Requirement(
                "max_topics_in_first_day", max(opened.values()), AT_LEAST,
                f"one person started {max(opened.values())} of these threads, and "
                f"their account will be a few minutes old when it does so again",
            ))
            requirements.append(Requirement(
                "max_topics_per_day", max(opened.values()), AT_LEAST,
                "the whole of one person's thirteen years lands on one day",
            ))

        replied = Counter(
            post.get("uid") for post in posts if post.get("pid") not in opening_pids
        )
        if replied:
            requirements.append(Requirement(
                "max_replies_in_first_day", max(replied.values()), AT_LEAST,
                f"one person wrote {max(replied.values())} replies",
            ))

        # What a post carries is not what its author typed. Each attachment is
        # appended to the body as markdown -- an image inline, anything else as
        # a link -- so a post with four screenshots and no URLs in it arrives
        # at Discourse carrying four embedded media items, and one with three
        # PDFs arrives carrying three links. Counting only the old text missed
        # both, and the run found out one refused post at a time.
        by_post = {}
        for row in attachments:
            by_post.setdefault(row.get("pid"), []).append(row)

        def carried(post):
            body = bodies.get(post.get("pid"), "")
            files = by_post.get(post.get("pid"), [])
            images = sum(
                1 for row in files
                if Path(row.get("filename") or "").suffix.lstrip(".").lower()
                in IMAGE_EXTENSIONS
            )
            return (
                len(re.findall(r"https?://", body)) + len(files),
                len(re.findall(r"!\[", body)) + images,
            )

        counted = [carried(post) for post in posts]
        most_links = max(links for links, _media in counted)
        if most_links:
            requirements.append(Requirement(
                "newuser_max_links", most_links, AT_LEAST,
                f"one post carries {most_links} links, counting each attachment "
                f"-- they are appended to the post as markdown links",
            ))
        most_media = max(media for _links, media in counted)
        if most_media:
            requirements.append(Requirement(
                "newuser_max_images", most_media, AT_LEAST,
                f"one post carries {most_media} embedded media items, counting "
                f"attachments that are pictures -- those go inline, not as links",
                # Renamed in Discourse 2.7, and a forum that has never heard of
                # the old name reports the setting as unknown rather than as
                # renamed -- after which it is quietly not changed.
                aliases=("newuser_max_embedded_media",),
            ))

        # Posting is not the same as waiting. Discourse makes a new account
        # pause between posts, and during an import a person's whole history
        # arrives in seconds -- so this is hit on the second post somebody
        # makes in a thread, every time. The run waits it out when it has to,
        # but 30 seconds a post across 1,500 posts is twelve hours of waiting
        # to avoid.
        for setting in (
            "rate_limit_create_post",
            "rate_limit_new_user_create_post",
            "rate_limit_create_topic",
            "rate_limit_new_user_create_topic",
        ):
            requirements.append(Requirement(
                setting, 0, AT_MOST,
                "the seconds Discourse makes somebody wait between posts, "
                "which an import spends doing nothing",
            ))

    if attachments:
        per_post = Counter(row.get("pid") for row in attachments)
        most = max(per_post.values())
        requirements.append(Requirement(
            "newuser_max_attachments", most, AT_LEAST,
            f"one post carries {most} attachments, and everybody posting here "
            f"is a brand new account at trust level 0",
        ))

        extensions = sorted({
            Path(row.get("filename") or "").suffix.lstrip(".").lower()
            for row in attachments
            if Path(row.get("filename") or "").suffix
        })
        if extensions:
            requirements.append(Requirement(
                "authorized_extensions", extensions, INCLUDES,
                "these are the file types people actually attached",
                # Kept afterwards. This forum exists so that people can share
                # exam papers and summaries; a board that accepts a PDF during
                # the import and refuses one the next morning would be a
                # strange thing to have built.
                restore=False,
            ))

        sizes = [int(row.get("filesize") or 0) for row in attachments]
        biggest_kb = (max(sizes) + 1023) // 1024
        if biggest_kb:
            why = f"the largest attachment is {biggest_kb / 1024:.1f} MB"
            if biggest_kb > 10 * 1024:
                # The setting is only half of it: the webserver in front of
                # Discourse has its own limit, and it is the one that answers
                # first. A file over that comes back as 413, not as anything
                # Discourse said.
                why += (
                    "; above 10 MB the webserver's own client_max_body_size "
                    "matters too, or the upload is refused before Discourse "
                    "ever sees it"
                )
            requirements.append(Requirement(
                "max_attachment_size_kb", biggest_kb, AT_LEAST, why,
            ))

        # Discourse sizes pictures separately from everything else, and the
        # two settings have different defaults. An upload that is an image is
        # measured against max_image_size_kb and never against the other one,
        # so raising only the attachment limit lets a 4 MB photograph through
        # everything except the check that actually applies to it.
        image_sizes = [
            int(row.get("filesize") or 0) for row in attachments
            if Path(row.get("filename") or "").suffix.lstrip(".").lower()
            in IMAGE_EXTENSIONS
        ]
        if image_sizes:
            biggest_image_kb = (max(image_sizes) + 1023) // 1024
            requirements.append(Requirement(
                "max_image_size_kb", biggest_image_kb, AT_LEAST,
                f"the largest picture is {biggest_image_kb / 1024:.1f} MB, and "
                f"pictures are measured against this rather than against "
                f"max_attachment_size_kb",
            ))

    return requirements


#: What to do once the forum has been asked what it allows.
PROCEED = "proceed"    # go ahead as things stand
LOOSEN = "loosen"      # change the settings first, and put them back after
REFUSE = "refuse"      # the forum would reject part of this


def what_to_do_about_settings(*, ready, anything, adjust_settings, dry_run):
    """Whether a run can go ahead, given what the forum currently allows.

    ``ready`` is that nothing would be refused; ``anything`` is that something
    is nonetheless worth changing.

    A dry run is never refused. It sends nothing, so no setting can stop it --
    and it is how you find out which attachments are missing, which is exactly
    what you want to know *before* changing anything on the forum. Refusing it
    for a title minimum turns the safe step into the one that needs the unsafe
    step done first.
    """
    if dry_run:
        return PROCEED
    if anything and adjust_settings:
        return LOOSEN
    if not ready:
        return REFUSE
    return PROCEED


def rehearsal_threads(threads, posts, attachments=(), limit=5):
    """Threads worth trying the spike on, hardest first.

    A rehearsal only tells you something if it exercises the things that break:
    several different authors, so impersonation is actually tested; more than
    one post, so replies land in the topic the first post opened; and
    attachments, since uploading is where the file types, the size limits and
    the .attach renaming all show up at once. A thread of one post by one
    person with nothing attached proves almost nothing.
    """
    posts_by_thread = {}
    for post in posts:
        posts_by_thread.setdefault(post.get("tid"), []).append(post)

    attached = Counter()
    pid_to_tid = {post.get("pid"): post.get("tid") for post in posts}
    for row in attachments:
        tid = pid_to_tid.get(row.get("pid"))
        if tid is not None:
            attached[tid] += 1

    candidates = []
    for thread in threads:
        tid = thread.get("tid")
        in_thread = posts_by_thread.get(tid, [])
        if len(in_thread) < 2:
            continue
        authors = len({post.get("uid") for post in in_thread})
        if authors < 2:
            continue
        candidates.append({
            "tid": tid,
            "subject": thread.get("subject") or "",
            "posts": len(in_thread),
            "authors": authors,
            "attachments": attached[tid],
        })

    candidates.sort(
        key=lambda row: (row["attachments"] > 0, row["authors"], row["posts"]),
        reverse=True,
    )
    return candidates[:limit]


def _as_number(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _listed(value):
    """A Discourse list setting as a set. It stores them pipe-separated."""
    return {
        item.strip().lstrip(".").lower()
        for item in str(value or "").replace("|", ",").split(",")
        if item.strip()
    }


def _satisfied(requirement, actual):
    """Is the live value acceptable? None when it cannot be read."""
    if actual is None:
        return None
    if requirement.compare == EQUALS:
        return str(actual).strip().lower() == str(requirement.needed).lower()
    if requirement.compare == INCLUDES:
        have = _listed(actual)
        # "*" is Discourse's "anything at all".
        return "*" in have or not [item for item in requirement.needed if item not in have]
    number = _as_number(actual)
    if number is None:
        return None
    if requirement.compare == AT_MOST:
        return number <= requirement.needed
    return number >= requirement.needed


def check_site_settings(poster, requirements):
    """Compare what the archive needs against what the forum currently allows.

    Returns a row per requirement: the setting, what it is now, what it has to
    be, and whether it is already there. The current value is part of the
    answer on purpose -- it is what gets put back when the import is done.
    """
    live = poster.site_settings()
    rows = []
    for requirement in requirements:
        # The name this forum knows it by, which may not be the one asked for.
        name = requirement.setting
        if name not in live:
            name = next(
                (alias for alias in requirement.aliases if alias in live),
                requirement.setting,
            )
        actual = live.get(name)
        ok = _satisfied(requirement, actual)
        needed = requirement.needed
        if requirement.compare == INCLUDES:
            missing = [item for item in needed if item not in _listed(actual)]
            needed = ", ".join(missing or needed)
        rows.append({
            "setting": name,
            "now": actual,
            "needed": needed,
            "compare": requirement.compare,
            "ok": ok,
            "why": requirement.why,
            "blocks": requirement.blocks,
            "requirement": requirement,
        })
    return rows


def _value_for(requirement, now):
    """What to write, given what is there. Only lists need the old value."""
    if requirement.compare == INCLUDES:
        return "|".join(sorted(_listed(now) | set(requirement.needed)))
    return str(requirement.needed)


def loosen_site_settings(poster, requirements, journal_path):
    """Change what has to change, having first written down what it was.

    The journal is written to disk *before* the first setting is touched, and
    that ordering is the whole point. A run that is interrupted -- a dropped
    connection, a full disk, somebody's Ctrl-C -- leaves a forum with its
    guards down and no memory of what they were. With the journal on disk the
    damage is one command to undo, by somebody who was not there.

    Returns the list of changes, which is also what the journal holds.
    """
    journal_path = Path(journal_path)
    # A record already there means a previous run loosened these settings, and
    # writing over it would record the loosened values as the originals -- the
    # way back would be gone, silently, at the moment it was most needed.
    #
    # Unless that run put them back. A journal whose settings have been
    # restored has done its job and holds nothing worth keeping, and refusing
    # over it would mean every successful run blocked the next one.
    if journal_path.exists() and not journal_is_spent(journal_path):
        raise FileExistsError(
            f"{journal_path} already exists and its settings were never put "
            f"back. It holds what they were before an earlier run; writing "
            f"over it would lose them. Restore from it first:\n"
            f"  flask restore-forum-settings {journal_path}"
        )

    changes = []
    for row in check_site_settings(poster, requirements):
        if row["ok"] is not False:
            continue
        requirement = row["requirement"]
        changes.append({
            # The name this forum knows it by, not the one asked for: writing
            # it back under a name the site does not have would be a 404 at
            # restore time, when there is least appetite for one.
            "setting": row["setting"],
            "was": row["now"],
            "set_to": _value_for(requirement, row["now"]),
            "restore": bool(requirement.restore),
        })

    if not changes:
        return changes

    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(json.dumps({
        "written_at": datetime.now(tz=timezone.utc).isoformat(),
        "forum": poster.base_url,
        "changes": changes,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    for change in changes:
        poster.set_site_setting(change["setting"], change["set_to"])
        current_app.logger.info(
            "Migration: %s %s -> %s", change["setting"], change["was"], change["set_to"]
        )
    return changes


def _same_value(one, other):
    """Two site-setting values, compared the way Discourse means them.

    A boolean comes back from the API as JSON true, not as the string "true"
    that was sent, so comparing the two literally would decide that every
    switch had been changed by somebody else and refuse to put any of them
    back -- which is the one failure this whole mechanism exists to avoid.
    """
    return str(one).strip().lower() == str(other).strip().lower()


def restore_site_settings(poster, changes, force=False):
    """Put the settings back. Returns a row per setting saying what happened.

    A setting that no longer holds the value the import gave it was changed by
    somebody else in the meantime, and is left alone: restoring it would throw
    their change away. ``force`` overrides that, for the case where the person
    doing it knows better.
    """
    live = poster.site_settings()
    results = []
    for change in changes:
        setting = change["setting"]
        result = {"setting": setting, "was": change["was"], "set_to": change["set_to"]}

        if not change.get("restore", True):
            result["outcome"] = "left as it is, on purpose"
        elif _same_value(live.get(setting), change["was"]):
            # Checked before the "somebody changed it" case, and it is the
            # commonest outcome of all: a run that was interrupted puts the
            # settings back on its way out, so running the restore afterwards --
            # which is exactly what the interrupt tells you to do -- finds them
            # already back. Reporting that as an unknown third party having
            # moved them is alarming, and wrong.
            result["outcome"] = "already back"
        elif not _same_value(live.get(setting), change["set_to"]) and not force:
            result["outcome"] = (
                f"left alone: it now reads {live.get(setting)!r}, which is not "
                f"what the import set it to, so somebody else has changed it"
            )
        else:
            poster.set_site_setting(setting, change["was"])
            result["outcome"] = "restored"
        results.append(result)
    return results


def read_settings_journal(journal_path):
    """The changes a previous run wrote down, for restoring after a crash."""
    payload = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    changes = payload.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError(f"{journal_path} does not hold any recorded changes.")
    return payload


def journal_is_spent(journal_path):
    """Has the run that wrote this already put the settings back?

    A journal exists to get the forum back to how it was. Once that has
    happened it is a receipt rather than a plan, and standing in the way of
    the next run is the one thing it should not do.
    """
    try:
        payload = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable is not the same as dealt with. Err towards refusing.
        return False
    return bool(payload.get("restored_at"))


def mark_journal_restored(journal_path, results):
    """Write on the journal that its settings went back, if they all did.

    One setting left alone -- because somebody else changed it in the meantime
    -- means the forum is not as it was, so the journal stays live and the next
    run still stops on it.
    """
    stuck = [row for row in results if row["outcome"].startswith("left alone")]
    if stuck:
        return False
    path = Path(journal_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    payload["restored_at"] = datetime.now(tz=timezone.utc).isoformat()
    payload["outcomes"] = results
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Moving one thread
# ---------------------------------------------------------------------------

def _as_iso(dateline):
    """A MyBB timestamp as the instant it actually was."""
    return datetime.fromtimestamp(int(dateline), tz=timezone.utc).isoformat()


def _attachment_markdown(upload, original_name):
    """How Discourse refers to an upload from inside a post."""
    short_url = upload.get("short_url") or upload.get("url")
    if (upload.get("extension") or "").lower() in IMAGE_EXTENSIONS:
        return f"![{original_name}]({short_url})"
    size = upload.get("human_filesize") or ""
    return f"[{original_name}|attachment]({short_url}) {size}".strip()


def migrate_thread(poster, thread, posts, attachments_by_post, usernames_by_uid,
                   uploads_dir, category_id, *, dry_run=False, fallback_username=None,
                   ledger=None, require_attachments=True, title=None,
                   author_lookup_verified=False):
    """Post one old thread onto the forum. Returns a report.

    Every post is sent as its own author with its own date, and the report says
    what Discourse recorded against what was asked for -- which is the entire
    reason this exists.

    With a ``ledger``, what is already on the forum is left alone and the
    thread carries on from where a previous run stopped. Without one, running
    this twice posts the thread twice.

    ``require_attachments`` makes a post whose files are not on this machine,
    or which the forum will not accept, wait for a later run rather than
    arrive without them. It is the default
    because the alternative loses them silently: a post written down as done
    is never revisited, and the one thing nobody thinks to check is a post
    that looks complete. Turning it off is for a run where the files are known
    to be gone for good and the words are worth having anyway.
    """
    report = {"thread": thread.get("subject"), "posts": [], "problems": [],
              "topic_id": None}
    # The title may differ from the old subject: Discourse will not take two
    # topics with the same name, and this board has ninety-one called
    # "Klausuren".
    title = title or thread.get("subject") or "(no subject)"
    uploads_dir = Path(uploads_dir)
    if ledger is not None:
        report["topic_id"] = ledger.topic_for(thread.get("tid"))

    # Posting the whole thread under one name is not a migration, it is a
    # mistake wearing one. It already happened once: "users" matched a plugin's
    # table, every lookup missed, and the fallback quietly took the lot.
    #
    # But a thread whose every author has since been deleted from the board is
    # an ordinary thing -- this board has three -- and refusing those as though
    # the lookup were broken loses real posts to a guard against a different
    # fault. So the caller checks the whole board once, and says so here.
    resolved = sum(1 for post in posts if usernames_by_uid.get(post.get("uid")))
    if not resolved and not author_lookup_verified:
        raise ValueError(
            "Not one of these posts could be matched to a forum account. "
            "The author lookup is empty or reading the wrong table -- "
            "check that the users table was found, rather than a plugin's."
        )
    if not resolved:
        report["problems"].append(
            f"None of this thread's {len(posts)} posts has an author still in "
            f"the old board's user table; all are attributed to "
            f"{fallback_username or 'the fallback'}."
        )
    if resolved < len(posts):
        report["problems"].append(
            f"{len(posts) - resolved} of {len(posts)} posts have no forum account "
            "for their author and will be attributed to the fallback."
        )

    # Set when the opening post could not be created. Everything after it
    # belongs to a topic that does not exist, and must not be attempted: the
    # first run of this turned one real failure -- a title Discourse thought
    # too short -- into twelve, because each reply then tried to open a topic
    # of its own with no title and no category.
    no_topic = False

    for post in posts:
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

        if no_topic:
            record["result"] = "not attempted: the thread has no topic to go in"
            continue

        if ledger is not None and ledger.post_for(post.get("pid")):
            # Already on the forum from an earlier run. Posting it again would
            # be the one mistake a resumed run must not make.
            record["result"] = "already there"
            record["post_id"] = ledger.post_for(post.get("pid"))
            continue

        if not author:
            record["result"] = "skipped: no forum account for this author"
            report["problems"].append(f"pid={post.get('pid')}: no author")
            continue

        body = bbcode_to_markdown(post.get("message"))
        attachments = attachments_by_post.get(post.get("pid"), [])

        # Whether every file this post carries is actually on this machine.
        #
        # This decides the post, not just the attachment. Posting the words
        # without the PDF and writing it down as done would lose that PDF for
        # good: the ledger would skip the post on every later run, and the one
        # thing nobody would think to check is a post that arrived looking
        # complete. So the post waits for its files instead.
        absent = [
            row for row in attachments
            if not (uploads_dir / (row.get("attachname") or "")).exists()
        ]
        if absent and require_attachments:
            record["result"] = (
                f"waiting: {len(absent)} of {len(attachments)} files are not on "
                f"this machine yet"
            )
            for row in absent:
                report["problems"].append(
                    f"pid={post.get('pid')}: "
                    f"{uploads_dir / (row.get('attachname') or '')} is not there"
                )
            if report["topic_id"] is None:
                # It would have opened the topic. Letting the next post open it
                # instead would put the thread under the wrong name and date,
                # and this one could then never be first.
                no_topic = True
                report["problems"].append(
                    "The thread's opening post is waiting for its files, so the "
                    "thread was left for a later run rather than started "
                    "without it."
                )
            continue

        # Attachments first: a post referring to an upload has to be written
        # after the upload exists.
        links = []
        refused = []
        for attachment in attachments:
            source = uploads_dir / (attachment.get("attachname") or "")
            # Stripped: one real filename on the board begins with two spaces,
            # which is a thing a 2017 upload dialog allowed and a filename is
            # better off without.
            original = (attachment.get("filename") or "").strip() or source.name
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
                refused.append(original)
                report["problems"].append(f"pid={post.get('pid')} {original}: {exc}")
                continue
            links.append(_attachment_markdown(upload, original))
            record["attachments"] += 1

        # A file the forum would not take is the same loss as a file that was
        # never here, and it arrives looking less like one. The first time this
        # happened -- nginx refusing three archives as too large -- the posts
        # went out without them and were written down as done, which is how a
        # file stops existing quietly.
        if refused and require_attachments:
            record["result"] = (
                f"waiting: the forum refused {len(refused)} of "
                f"{len(attachments)} files"
            )
            if report["topic_id"] is None:
                no_topic = True
                report["problems"].append(
                    "The thread's opening post carries a file the forum would "
                    "not take, so the thread was left for a later run rather "
                    "than started without it."
                )
            continue

        if links:
            # Appended, because 94% of these were never referenced in the text:
            # MyBB simply listed them under the post.
            body = f"{body}\n\n{chr(10).join(links)}" if body else "\n".join(links)

        # Dropping either of these would take the post's date and its place in
        # the thread with it, so they are marked rather than lost, and the
        # marker says which of the two it was.
        was_empty = not body.strip()
        uncounted = bool(body.strip()) and not what_discourse_counts(body)
        if was_empty:
            # Whitespace, or BBCode that converts to nothing at all. Discourse
            # will not store a post with nothing in it, and the old board did.
            body = EMPTY_POST
        elif uncounted:
            # There is something there and Discourse does not count it: the
            # board's smilies. The smiley stays and this goes beneath it.
            body = f"{body}\n\n{SMILEY_ONLY_POST}"

        # An inline [attachment=N] is rare (145 across the whole board) and is
        # left as-is rather than guessed at, so it shows up when read.
        if dry_run:
            record["result"] = "would post"
            continue

        # Whoever gets here first opens the topic -- not whoever is first in
        # the list, who may have been skipped for having no account.
        opening = report["topic_id"] is None
        try:
            if opening:
                created = poster.create_post(
                    raw=body, as_username=author, created_at=asked_for,
                    title=title, category=category_id,
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
            if opening:
                no_topic = True
                report["problems"].append(
                    "The opening post was refused, so this thread has no topic. "
                    "The rest of it was not attempted -- fix the reason above and "
                    "run the thread again."
                )
            continue

        record["result"] = "posted"
        if was_empty:
            record["result"] = "posted: empty on the old board"
        elif uncounted:
            record["result"] = "posted: only a smiley on the old board"
        record["post_id"] = created.get("id")
        # What Discourse actually stored, read back rather than assumed.
        record["recorded"] = created.get("created_at")
        if ledger is not None:
            # Written down before the next post is attempted, because a record
            # of what landed is only useful if it is never behind what landed.
            if opening and report["topic_id"]:
                ledger.record_topic(thread.get("tid"), report["topic_id"])
            ledger.record_post(post.get("pid"), created.get("id"))

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
