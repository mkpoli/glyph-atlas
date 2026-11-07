"""The IDS bot: every ideographic description in the community chat gets its IDS Workshop page.

The bot is the Matrix account @idsbot:glyphatlas.org. It joins every public room of its homeserver
and follows the homeserver's sync stream; a message holding a description (⿱⿻韦丷吋) is
answered with one https://ids.mkpo.li/<description> line per description. Edits, its own messages and
messages that already link the Workshop are left alone.

The file uses the standard library only, so a copy of it runs on the chat server without the
project's environment:

    python3 ids_bot.py --token-file /etc/ids-bot/token --state /var/lib/ids-bot/state.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

WORKSHOP = "https://ids.mkpo.li/"
# Cloudflare in front of the homeserver refuses the default urllib agent, so the bot names itself.
USER_AGENT = "glyph-atlas-ids-bot/1 (+https://glyphatlas.org)"
HOMESERVER = "https://matrix.glyphatlas.org"

# The operators the Workshop reads, each with how many descriptions it takes (its `OPERATORS`).
OPERATORS = {
    "⿰": 2, "⿱": 2, "⿲": 3, "⿳": 3, "⿴": 2, "⿵": 2, "⿶": 2, "⿷": 2, "⿸": 2, "⿹": 2,
    "⿺": 2, "⿻": 2, "⿼": 2, "⿽": 2, "⿾": 1, "⿿": 1, "㇯": 2, "〾": 1,
}
EMPTY = frozenset("⬚＋")
# Characters that would end or cut the destination path, so a link carrying them could not open. The
# Workshop reads them as components in its editor; the bot leaves descriptions holding them alone.
UNSAFE = frozenset("./?#%")
# The operators a zi.tools modifier may follow (its `MODIFIABLE`), and the characters one holds.
MODIFIABLE = frozenset("⿻⿴⿵⿶⿷⿼㇯")
MODIFIER = re.compile(r"[0-9:,._xabcdlr|-]+")
# The longest description the Workshop reads as one ordinary description, in code points; a longer
# one is read only to find where it ends, so the bot can stay silent instead of linking its fragments.
LONGEST = 64
LONGEST_READ = 8192
# The most long probes one message gets, so a run of operators with no description cannot stall the
# sync loop.
PROBES = 16
# A message longer than this is read only this far: chat messages need no more, and the scan stays bounded.
SCAN = 8192
# The most links one message gets; a wall of descriptions should not become a wall of replies.
MOST = 10
# How long /sync is asked to hold a request open, and how much longer the socket waits for the answer.
SYNC_TIMEOUT_MS = 30_000
SYNC_READ_TIMEOUT = SYNC_TIMEOUT_MS / 1000 + 15
# The timeline the bot reads; room state and ephemeral events are of no use to it.
FILTER = json.dumps({"room": {"timeline": {"limit": 50}, "state": {"limit": 0}, "ephemeral": {"limit": 0}}})
REFRESH_SECONDS = 3600

log = logging.getLogger("ids-bot")


def _selector(char: str) -> bool:
    point = ord(char)
    return 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF


def _component(char: str) -> bool:
    """Whether the Workshop reads `char` as a component: one graphic character, no operator or slot.

    The Workshop draws mixed-script descriptions, so a component is any character a message can carry
    against the Unicode version this Python knows: an unassigned code point is read as a component the
    Workshop may know by a later version. A character that would break the answer's URL is refused.
    """
    if char in OPERATORS or char in EMPTY or char in UNSAFE or _selector(char):
        return False
    category = unicodedata.category(char)
    return not category.startswith("Z") and category not in ("Cc", "Cf", "Cs")


def _modifier_length(text: str, at: int) -> int:
    """How many characters the zi.tools modifier at `text[at]` takes, 0 where none starts there."""
    if at >= len(text) or text[at] != "[":
        return 0
    end = text.find("]", at + 1)
    if end < 0 or end - at > 65 or not MODIFIER.fullmatch(text[at + 1:end]):
        return 0
    return end - at + 1


def description_end(text: str, at: int = 0, longest: int = LONGEST) -> int:
    """Where the description starting at `text[at]` ends, or -1 when none well formed starts there.

    The walk is flat: each operator says how many parts it still needs, so a description of the
    Workshop's pyramid length needs no recursion.
    """
    if at >= len(text) or text[at] not in OPERATORS:
        return -1
    start = at
    limit = min(len(text), at + longest)
    required = 1
    while required:
        if at >= limit:
            return -1
        char = text[at]
        arity = OPERATORS.get(char)
        if arity is None:
            if not _component(char):
                return -1
            at += 1
            if at < limit and _selector(text[at]):
                at += 1
            required -= 1
            continue
        at += 1
        if char in MODIFIABLE:
            skip = _modifier_length(text, at)
            if at + skip > limit:
                return -1
            at += skip
        required += arity - 1
    return at if at > start else -1


def _edge(char: str) -> bool:
    """Whether `char` separates a description from prose: a description never ends against a letter,
    a digit or a mark, as it would in a sentence that merely uses the operator (用⿰表示)."""
    return unicodedata.category(char)[0] not in ("L", "N", "M")


def _delimited(text: str, at: int, end: int, after: int = -1) -> bool:
    """Whether a description between `at` and `end` stands apart: at the start, beside a break, or
    directly after `after`, the end of the description before it (⿰木木⿰日曰)."""
    return (at == 0 or at == after or _edge(text[at - 1])) and (end >= len(text) or _edge(text[end]))


def sequences(text: str) -> list[str]:
    """The descriptions in `text`, in reading order and once each.

    A description is read from its operator to the exact end its operators ask for, so the prose after
    it is not taken for components. It must stand as a word of its own, between the start or a space or
    punctuation and the end or another such break: 用⿰表示 names no description, while ⿰木木、是林
    names ⿰木木. A text may carry several (⿱⿻韦丷吋 ⿳⿻韦丷一吋). A description too long for the
    Workshop's ordinary parser is skipped whole, so none of its parts is linked on its own.
    """
    found: list[str] = []
    seen: set[str] = set()
    at = 0
    after = -1
    probes = 0
    limit = min(len(text), SCAN)
    while at < limit:
        if text[at] in OPERATORS:
            end = description_end(text, at)
            if end > 0 and _delimited(text, at, end, after):
                sequence = text[at:end]
                if UNSAFE.isdisjoint(sequence) and sequence not in seen:
                    seen.add(sequence)
                    found.append(sequence)
                after = end
                at = end
                continue
            if probes < PROBES:
                probes += 1
                long_end = description_end(text, at, LONGEST_READ)
                if long_end > at + LONGEST:
                    at = long_end
                    continue
        at += 1
    return found


def _unquoted(body: str) -> str:
    """The part of a message below Matrix's quoted reply fallback, the `> …` lines it starts with."""
    lines = body.splitlines()
    while lines and lines[0].lstrip().startswith(">"):
        lines.pop(0)
    return "\n".join(lines).strip()


def wanted(event: dict[str, Any], user_id: str) -> list[str]:
    """The descriptions in a message that deserve a reply, or none for a message the bot leaves alone."""
    if event.get("type") != "m.room.message" or event.get("sender") == user_id:
        return []
    content = event.get("content")
    if not isinstance(content, dict) or content.get("msgtype") not in ("m.text", "m.emote"):
        return []
    relates = content.get("m.relates_to")
    if content.get("m.new_content") or (isinstance(relates, dict) and relates.get("rel_type") == "m.replace"):
        return []
    body = content.get("body")
    if not isinstance(body, str):
        return []
    body = _unquoted(body)
    if not body or "ids.mkpo.li" in body:
        return []
    return sequences(body)[:MOST]


def link(sequence: str) -> str:
    """The Workshop page a description opens, as written in a message."""
    return WORKSHOP + sequence


def reply_content(descriptions: list[str], event_id: str) -> dict[str, Any]:
    """The message that answers `event_id` with one page per description."""
    body = "\n".join(link(sequence) for sequence in descriptions)
    formatted = "<br />".join(
        f'<a href="{WORKSHOP}{urllib.parse.quote(sequence, safe="")}">{html.escape(link(sequence))}</a>'
        for sequence in descriptions
    )
    return {
        "msgtype": "m.text",
        "body": body,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted,
        "m.relates_to": {"rel_type": "m.in_reply_to", "event_id": event_id},
    }


def transaction_id(event_id: str) -> str:
    """The send transaction that answers `event_id`: the same answer is sent once, even across restarts."""
    return "ids-" + hashlib.sha256(event_id.encode()).hexdigest()[:48]


class MatrixError(Exception):
    """An answer from the homeserver that is not the one asked for."""

    def __init__(self, status: int | None, message: str = "", retry_after_ms: int | None = None,
                 code: str | None = None):
        super().__init__(f"{status}: {message}" if status is not None else message)
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.code = code


class Matrix:
    """The handful of client API calls the bot makes, over the standard library."""

    def __init__(self, homeserver: str = HOMESERVER, token: str = "", timeout: float = 45.0):
        self.homeserver = homeserver.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, *, params: dict[str, str] | None = None,
             body: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        url = self.homeserver + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(error.read() or b"{}")
            except ValueError:
                pass
            retry = payload.get("retry_after_ms")
            code = payload.get("errcode")
            raise MatrixError(error.code, str(payload.get("error") or error.reason),
                              retry if isinstance(retry, int) else None,
                              code if isinstance(code, str) else None) from None
        except ValueError as error:
            raise MatrixError(None, f"the homeserver answered with JSON that could not be read: {error}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise MatrixError(None, str(getattr(error, "reason", error))) from None


class State:
    """The sync token to resume from, kept in a file so a restart does not replay the rooms."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None

    def load(self) -> str | None:
        if self.path is None:
            return None
        try:
            token = json.loads(self.path.read_text(encoding="utf-8")).get("since")
        except (OSError, ValueError, AttributeError):
            return None
        return token if isinstance(token, str) else None

    def save(self, since: str) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps({"since": since}), encoding="utf-8")
        os.replace(temporary, self.path)

    def forget(self) -> None:
        """Drop the saved token, after the homeserver no longer knows it."""
        if self.path is not None:
            self.path.unlink(missing_ok=True)


def _room(room_id: str) -> str:
    return urllib.parse.quote(room_id, safe="")


class Bot:
    """The account, the rooms it answers in, and the sync loop that carries their messages to it."""

    def __init__(self, matrix: Matrix, state: State, *, refresh_seconds: float = REFRESH_SECONDS):
        self.matrix = matrix
        self.state = state
        self.refresh_seconds = refresh_seconds
        self.user_id = ""
        self._joined_at = 0.0

    def run(self) -> None:
        self.user_id = self.matrix.call("GET", "/_matrix/client/v3/account/whoami")["user_id"]
        log.info("signed in as %s", self.user_id)
        self.join_public_rooms()
        since = self.state.load()
        if since is None:
            since = self.initial_sync()
        wait = 1
        while True:
            try:
                since = self.cycle(since)
                wait = 1
            except MatrixError as error:
                if error.status in (401, 403):
                    raise
                if error.status == 400 and error.code in ("M_UNKNOWN_POS", "M_UNKNOWN"):
                    log.warning("sync position refused: %s; reading the rooms again", error)
                    self.state.forget()
                    since = self.initial_sync()
                    wait = 1
                    continue
                log.warning("sync: %s", error)
                time.sleep(wait)
                wait = min(wait * 2, 60)
            if time.monotonic() - self._joined_at >= self.refresh_seconds:
                self.join_public_rooms()

    def initial_sync(self) -> str:
        """Read the rooms once without answering them, and keep the token the next sync resumes from."""
        response = self.matrix.call("GET", "/_matrix/client/v3/sync", params={"timeout": "0", "filter": FILTER},
                                    timeout=60)
        since = self._token(response)
        self.state.save(since)
        return since

    def cycle(self, since: str) -> str:
        """Wait for the next events, answer them, and keep the token the next cycle resumes from."""
        response = self.matrix.call(
            "GET", "/_matrix/client/v3/sync",
            params={"since": since, "timeout": str(SYNC_TIMEOUT_MS), "filter": FILTER},
            timeout=SYNC_READ_TIMEOUT,
        )
        self.process(response)
        token = self._token(response)
        self.state.save(token)
        return token

    @staticmethod
    def _token(response: dict[str, Any]) -> str:
        token = response.get("next_batch") if isinstance(response, dict) else None
        if not isinstance(token, str):
            raise MatrixError(None, "the sync answer carries no next_batch")
        return token

    def process(self, response: dict[str, Any]) -> None:
        rooms = response.get("rooms") if isinstance(response, dict) else None
        joined = rooms.get("join") if isinstance(rooms, dict) else None
        if not isinstance(joined, dict):
            return
        for room_id, room in joined.items():
            timeline = room.get("timeline") if isinstance(room, dict) else None
            if not isinstance(timeline, dict):
                continue
            if timeline.get("limited"):
                log.warning("the timeline of %s was cut; some messages went unread", room_id)
            for event in timeline.get("events") or []:
                if not isinstance(event, dict):
                    continue
                try:
                    self.answer(room_id, event)
                except Exception:
                    # A room's events are not trusted to have this code's shape; one bad event must not
                    # stop the stream or replay it after a restart.
                    log.exception("answering in %s", room_id)

    def answer(self, room_id: str, event: dict[str, Any]) -> None:
        descriptions = wanted(event, self.user_id)
        event_id = event.get("event_id")
        if not descriptions or not isinstance(event_id, str):
            return
        path = f"/_matrix/client/v3/rooms/{_room(room_id)}/send/m.room.message/{transaction_id(event_id)}"
        try:
            self.send(path, reply_content(descriptions, event_id))
        except MatrixError as error:
            log.warning("reply in %s: %s", room_id, error)

    def send(self, path: str, content: dict[str, Any]) -> None:
        """Send one message, waiting out a rate limit instead of losing the answer to it."""
        for attempt in range(3):
            try:
                self.matrix.call("PUT", path, body=content)
                return
            except MatrixError as error:
                if error.status == 429 and attempt < 2:
                    wait = error.retry_after_ms if error.retry_after_ms is not None else 1000
                    time.sleep(wait / 1000)
                    continue
                raise

    def join_public_rooms(self) -> None:
        """Join every public room on the bot's homeserver, so a room added later is covered too.

        The homeserver's room directory is the list of public rooms; a room it names that refuses the
        join is left alone, and the listing is read a page at a time.
        """
        self._joined_at = time.monotonic()
        try:
            answer = self.matrix.call("GET", "/_matrix/client/v3/joined_rooms")
            joined = set(answer.get("joined_rooms") or []) if isinstance(answer, dict) else set()
            since = None
            for _ in range(20):
                params = {"limit": "100"}
                if since:
                    params["since"] = since
                answer = self.matrix.call("GET", "/_matrix/client/v3/publicRooms", params=params)
                if not isinstance(answer, dict):
                    break
                for room in answer.get("chunk") or []:
                    room_id = room.get("room_id") if isinstance(room, dict) else None
                    if isinstance(room_id, str) and room_id not in joined:
                        self._join(room_id)
                since = answer.get("next_batch")
                if not since:
                    break
        except (MatrixError, KeyError, TypeError, AttributeError) as error:
            log.warning("joining the public rooms: %s", error)

    def _join(self, room_id: str) -> None:
        try:
            self.matrix.call("POST", f"/_matrix/client/v3/join/{_room(room_id)}", body={})
        except MatrixError as error:
            # One room refusing (or failing) the join must not leave the rooms after it unread.
            if error.status in (403, 404):
                log.info("not joining %s: %s", room_id, error)
            else:
                log.warning("joining %s: %s", room_id, error)


def read_token(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Answer a description in the community chat with its IDS Workshop page.")
    parser.add_argument("--homeserver", default=os.environ.get("MATRIX_HOMESERVER", HOMESERVER))
    parser.add_argument("--token", default=os.environ.get("MATRIX_ACCESS_TOKEN"),
                        help="the bot's access token; or give --token-file")
    parser.add_argument("--token-file", type=Path, help="file holding the bot's access token")
    parser.add_argument("--state", type=Path, help="file keeping the sync position between starts")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    token = args.token or (read_token(args.token_file) if args.token_file else "")
    if not token:
        parser.error("a token is needed: --token-file, --token or MATRIX_ACCESS_TOKEN")
    Bot(Matrix(args.homeserver, token), State(args.state)).run()


if __name__ == "__main__":
    main()
