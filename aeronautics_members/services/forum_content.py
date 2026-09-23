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

    def site_settings(self):
        """Every site setting and its current value, as {name: value}."""
        payload = self._call("GET", "/admin/site_settings.json")
        return {
            row.get("setting"): row.get("value")
            for row in payload.get("site_settings", [])
            if row.get("setting")
        }

    def set_site_setting(self, setting, value):
        """Change one site setting. Discourse names the field after itself."""
        self._call(
            "PUT", f"/admin/site_settings/{quote(str(setting))}.json",
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

class Requirement(namedtuple("Requirement", "setting needed compare why restore blocks")):
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
    """

    __slots__ = ()

    def __new__(cls, setting, needed, compare, why, restore=True, blocks=True):
        return super().__new__(cls, setting, needed, compare, why, restore, blocks)


#: ``compare`` says what the live value has to be, relative to ``needed``.
AT_MOST = "at_most"      # a floor Discourse enforces: it must not be higher
AT_LEAST = "at_least"    # a ceiling: it must not be lower
EQUALS = "equals"        # a switch
INCLUDES = "includes"    # a comma-separated list that must contain these


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

        plainest = min(_entropy(title) for title in titles)
        requirements.append(Requirement(
            "title_min_entropy", plainest, AT_MOST,
            f"the plainest subject uses only {plainest} distinct characters, "
            f"which is what Discourse measures to decide a title is not "
            f"meaningful",
        ))

    lengths = [len(body) for body in bodies.values()]
    if lengths:
        requirements.append(Requirement(
            "min_post_length", min(lengths), AT_MOST,
            f"the shortest post on the board is {min(lengths)} characters "
            f"after conversion",
        ))

        plainest = min(_entropy(body) for body in bodies.values())
        requirements.append(Requirement(
            "body_min_entropy", plainest, AT_MOST,
            f"the plainest post uses only {plainest} distinct characters",
        ))

    opening_pids = {thread.get("firstpost") for thread in threads}
    opening = [len(body) for pid, body in bodies.items() if pid in opening_pids]
    if opening:
        requirements.append(Requirement(
            "min_first_post_length", min(opening), AT_MOST,
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
            requirements.append(Requirement(
                "newuser_max_replies_per_topic", most_in_one - 1, AT_LEAST,
                f"one person replied {most_in_one - 1} times in a single thread",
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

        most_links = max(len(re.findall(r"https?://", body)) for body in bodies.values())
        if most_links:
            requirements.append(Requirement(
                "newuser_max_links", most_links, AT_LEAST,
                f"one post carries {most_links} links",
            ))
        most_images = max(len(re.findall(r"!\[", body)) for body in bodies.values())
        if most_images:
            requirements.append(Requirement(
                "newuser_max_images", most_images, AT_LEAST,
                f"one post carries {most_images} images",
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

    return requirements


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
        actual = live.get(requirement.setting)
        ok = _satisfied(requirement, actual)
        needed = requirement.needed
        if requirement.compare == INCLUDES:
            missing = [item for item in needed if item not in _listed(actual)]
            needed = ", ".join(missing or needed)
        rows.append({
            "setting": requirement.setting,
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
    # A record already there means a previous run loosened these settings and
    # may never have put them back. Writing over it would record the loosened
    # values as the originals, and the way back would be gone -- so this is
    # refused rather than resolved. Restore from that file, or move it aside
    # if it has already been dealt with.
    if journal_path.exists():
        raise FileExistsError(
            f"{journal_path} already exists. It holds what the settings were "
            f"before an earlier run; writing over it would lose them. Restore "
            f"from it first, or move it aside if that has already been done."
        )

    changes = []
    for row in check_site_settings(poster, requirements):
        if row["ok"] is not False:
            continue
        requirement = row["requirement"]
        changes.append({
            "setting": requirement.setting,
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

        # Whoever gets here first opens the topic -- not whoever is first in
        # the list, who may have been skipped for having no account.
        opening = report["topic_id"] is None
        try:
            if opening:
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
            if opening:
                no_topic = True
                report["problems"].append(
                    "The opening post was refused, so this thread has no topic. "
                    "The rest of it was not attempted -- fix the reason above and "
                    "run the thread again."
                )
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
