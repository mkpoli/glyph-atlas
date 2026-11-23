"""Replays a fixed set of chat situations against the live model and checks 大肥魚's answers.

Each case in replay.json is a short room log ending with a message that calls her, or one she reads
in another task ("task"), and rules her answer must keep: tools she must or must not use, patterns
her words must or must not match, and whether she must stay quiet.
The cases come from answers that went wrong in the community chat (an invented reading, a wrong
era name, a question answered from old chat instead of the homeserver) and from ones that must
stay cheap (small talk without lookups). Run it on the chat server after a prompt change; it reads
the homeserver, GitHub and the web but posts nothing and writes no memory:

    python3 replay.py --config /etc/fish-bot/config.toml --db /var/lib/fish-bot/chat.sqlite3
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import fish_bot as fb


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not all(isinstance(case, dict) and case.get("name") for case in cases):
        raise ValueError("replay cases must be a list of objects with a name")
    return cases


def check(case: dict[str, Any], said: str, tools: list[str], recap: bool = False) -> list[str]:
    """The rules an answer broke, empty when it kept them all."""
    broken = []
    rules = case.get("expect", {})
    if rules.get("tools_any") and not set(rules["tools_any"]) & set(tools):
        broken.append(f"used none of {rules['tools_any']} (used {tools or 'no tools'})")
    if "tools_none" in rules and rules["tools_none"] and tools:
        broken.append(f"looked something up for small talk ({tools})")
    for pattern in rules.get("must_match", []):
        if not re.search(pattern, said, re.IGNORECASE):
            broken.append(f"does not say /{pattern}/")
    for pattern in rules.get("must_not_match", []):
        if re.search(pattern, said, re.IGNORECASE):
            broken.append(f"says /{pattern}/")
    if "recap" in rules and rules["recap"] != recap:
        broken.append("did not turn to the catch-up" if rules["recap"] else "turned to the catch-up")
    if rules.get("quiet") and said:
        broken.append("spoke where she should stay quiet")
    if rules.get("max_chars") and len(said) > rules["max_chars"]:
        broken.append(f"{len(said)} characters, more than {rules['max_chars']}")
    return broken


def run_case(case: dict[str, Any], gemini: fb.Gemini, lookup: fb.Lookup, community: fb.Community,
             room_id: str, user_id: str) -> tuple[str, list[str], bool]:
    """One case's answer and the tools used, from a scratch log that holds only the case's lines."""
    with tempfile.TemporaryDirectory() as scratch:
        log = fb.Store(Path(scratch) / "replay.sqlite3")
        now = fb.now_ms() - 60_000 * len(case["messages"])
        names: dict[str, str] = {}
        people: dict[str, str] = {}
        for number, (sender, body) in enumerate(case["messages"]):
            user = user_id if sender == "大肥魚" else people.setdefault(sender, f"@replay{len(people) + 1}:example")
            names[user] = sender
            log.add_event(f"$replay{number}", room_id, user, now + number * 60_000, body, "m.text", backfilled=False)
        events = log.context(room_id, fb.now_ms() + 1000, fb.CHAT_CONTEXT)
        log.close()
    used: list[str] = []
    original = lookup.run

    def recording(name: str, arguments: Any) -> dict[str, Any]:
        used.append(name)
        return original(name, arguments)

    lookup.run = recording  # type: ignore[method-assign]
    lookup.here = room_id
    try:
        act = gemini.act(case.get("room", "中文"), "", events, names, task=case.get("task", "called"),
                         trigger=len(events) - 1,
                         bot_user_id=user_id, lookup=lookup, room_facts=community.summary(room_id))
    finally:
        lookup.run = original  # type: ignore[method-assign]
    return "\n".join(act.say), used, act.recap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay chat situations against the live model and check the answers.")
    parser.add_argument("--config", type=Path, default=Path("/etc/fish-bot/config.toml"))
    parser.add_argument("--db", type=Path, default=Path("/var/lib/fish-bot/chat.sqlite3"))
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("replay.json"))
    parser.add_argument("--only", help="run only the cases whose name contains this")
    args = parser.parse_args(argv)
    config = fb.Config.load(args.config)
    matrix = fb.Matrix(config.homeserver, fb._secret(config.token_file))
    gemini = fb.Gemini(fb._secret(config.gemini_key_file), config.gemini_model, config.gemini_url)
    github = fb.GitHub(fb._secret(config.github_token_file), config.github_repo) if config.github_token_file else None
    store = fb.Store(args.db)
    rooms = set(matrix.public_rooms())
    community = fb.Community(matrix, {config.user_id, *config.ignored_senders}, rooms=lambda: rooms)
    lookup = fb.Lookup(gemini, github, store, owner=config.github_repo.split("/")[0], rooms=lambda: rooms,
                       memory=config.memory_enabled, community=community)
    failed = 0
    for case in load_cases(args.cases):
        if args.only and args.only not in case["name"]:
            continue
        room_id = matrix.resolve(case["room_alias"]) if case.get("room_alias") else next(iter(sorted(rooms)))
        started = time.monotonic()
        try:
            said, tools, recap = run_case(case, gemini, lookup, community, room_id, config.user_id)
            broken = check(case, said, tools, recap)
        except (fb.GeminiError, fb.MatrixError, fb.GitHubError) as error:
            said, tools, broken = "", [], [f"error: {error}"]
        failed += bool(broken)
        print(f"{'FAIL' if broken else 'pass'}  {case['name']}  ({time.monotonic() - started:.1f}s, tools: {', '.join(tools) or '-'})")
        print("      " + said.replace("\n", " / ")[:300])
        for problem in broken:
            print("      - " + problem)
    store.close()
    print(f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
