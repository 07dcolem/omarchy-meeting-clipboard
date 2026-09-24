#!/usr/bin/env python3
"""Sanity checks for the required invite format and a few cousins."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib.machinery import SourceFileLoader

mod = SourceFileLoader(
    "teams_join",
    str(Path(__file__).resolve().parent / "teams-join-from-clipboard.py"),
).load_module()

REQUIRED = """Microsoft Teams Need help?<https://aka.ms/JoinTeamsMeeting?omkt=en-US>
Join the meeting now<https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZGE4ZmY1MGYtMzAxMS00OGE2LThkMWYtYWI1OTUxNGUyMDgy%40thread.v2/0?context=%7b%22Tid%22%3a%2289522aa1-1984-4d7a-96ba-2ac240623b6b%22%2c%22Oid%22%3a%22d79442ba-06b6-42e7-ac83-78e61f2b0fd7%22%7d>
Meeting ID: 412 829 079 975 3
Passcode: Sd94S45U"""

CASES = [
    (
        "required-outlook-angle-brackets",
        REQUIRED,
        {
            "source": "meetup-join-url",
            "meeting_id": "4128290799753",
            "passcode": "Sd94S45U",
            "url_contains": "meetup-join/19%3ameeting_ZGE4ZmY1MGYtMzAxMS00OGE2LThkMWYtYWI1OTUxNGUyMDgy",
        },
    ),
    (
        "id-and-pass-only",
        "Please join.\nMeeting ID: 412 829 079 975 3\nPasscode: Sd94S45U\n",
        {
            "source": "labeled-id",
            "meeting_id": "4128290799753",
            "passcode": "Sd94S45U",
            "join_url": "https://teams.microsoft.com/meet/4128290799753?p=Sd94S45U",
        },
    ),
    (
        "short-meet-url-with-p",
        "Join: https://teams.microsoft.com/meet/31827829071516?p=AbCdEf12 extra",
        {
            "source": "short-meet-url",
            "meeting_id": "31827829071516",
            "passcode": "AbCdEf12",
        },
    ),
    (
        "meetup-join-bare",
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d",
        {
            "source": "meetup-join-url",
            "url_contains": "meetup-join/19%3ameeting_abc",
        },
    ),
    (
        "german-labels",
        "Konferenz-ID: 123 456 789 012\nKennwort: Qw3rTy88",
        {
            "source": "labeled-id",
            "meeting_id": "123456789012",
            "passcode": "Qw3rTy88",
        },
    ),
    (
        "dash-separated-id",
        "Meeting ID: 412-829-079-975-3\nPasscode: Sd94S45U",
        {
            "meeting_id": "4128290799753",
            "passcode": "Sd94S45U",
        },
    ),
]


def run() -> int:
    failed = 0
    for name, text, expect in CASES:
        m = mod.parse_invite(text)
        url = m.join_url()
        errs = []
        if "source" in expect and m.source != expect["source"]:
            errs.append(f"source={m.source!r} want {expect['source']!r}")
        if "meeting_id" in expect and m.meeting_id != expect["meeting_id"]:
            errs.append(f"id={m.meeting_id!r} want {expect['meeting_id']!r}")
        if "passcode" in expect and m.passcode != expect["passcode"]:
            errs.append(f"pass={m.passcode!r} want {expect['passcode']!r}")
        if "url_contains" in expect and (not m.url or expect["url_contains"] not in m.url):
            errs.append(f"url missing {expect['url_contains']!r}: {m.url!r}")
        if "join_url" in expect and url != expect["join_url"]:
            errs.append(f"join_url={url!r} want {expect['join_url']!r}")
        if not m.ok():
            errs.append("meeting.ok() is False")
        if errs:
            failed += 1
            print(f"FAIL {name}: {'; '.join(errs)}")
        else:
            print(f"ok   {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
