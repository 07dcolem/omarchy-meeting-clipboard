#!/usr/bin/env python3
"""Extract a Microsoft Teams meeting URL / ID / passcode from invite text
and hand it to teams-for-linux.

Designed to be wrapped later as an Omarchy plugin. Standalone usage:

    teams-join-from-clipboard.py              # clipboard
    teams-join-from-clipboard.py --dry-run    # parse only, print what would launch
    echo "$INVITE" | teams-join-from-clipboard.py --stdin
    teams-join-from-clipboard.py --text 'Meeting ID: 412 829 079 975 3 ...'

Exit codes:
    0  launched (or dry-run printed a join target)
    1  no meeting found
    2  found meeting but could not launch client
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Outlook / Gmail often wrap links as Label<https://...> or Label (https://...)
ANGLE_URL = re.compile(
    r"<(https?://[^>\s]+)>",
    re.IGNORECASE,
)
BARE_URL = re.compile(
    r"(https?://[^\s<>\"']+)",
    re.IGNORECASE,
)

# Long join links (scheduled meetings)
MEETUP_JOIN = re.compile(
    r"https?://teams\.(?:microsoft\.com|live\.com|cloud\.microsoft)"
    r"/l/meetup-join/[^\s<>\"']+",
    re.IGNORECASE,
)

# Short join links: https://teams.microsoft.com/meet/<id>?p=<pass>
SHORT_MEET = re.compile(
    r"https?://teams\.(?:microsoft\.com|live\.com|cloud\.microsoft)"
    r"/meet/(\d{8,16})(?:[^\s<>\"']*)?",
    re.IGNORECASE,
)

# Personal / consumer
LIVE_MEET = re.compile(
    r"https?://teams\.live\.com/meet/(\d{8,16})(?:[^\s<>\"']*)?",
    re.IGNORECASE,
)

# aka.ms redirect used in the stock invite footer — not a join target
AKA_JOIN = re.compile(
    r"https?://aka\.ms/JoinTeamsMeeting[^\s<>\"']*",
    re.IGNORECASE,
)

# Labeled Meeting ID. IDs are 9–16 digits, often grouped with spaces or dashes.
# Example that MUST work: "Meeting ID: 412 829 079 975 3"
ID_LABEL = re.compile(
    r"(?:"
    r"meeting\s*id|konferenz[- ]?id|id\s*(?:de\s*r[eé]union|de\s*la\s*r[eé]union)"
    r"|meeting[- ]?nummer|teilnehmer[- ]?id|conference\s*id"
    r")"
    r"\s*[:#]?\s*"
    r"([0-9][0-9 \t.-]{7,30}[0-9])",
    re.IGNORECASE,
)

# Bare clustered IDs that look like Teams IDs (last-resort)
BARE_ID = re.compile(
    r"(?<!\d)(\d{3}[ \t.-]\d{3}[ \t.-]\d{3}[ \t.-]\d{3,5}(?:[ \t.-]\d{1,2})?)(?!\d)"
)

# Passcode / password / PIN. Keep it tight so we don't swallow the next sentence.
PASS_LABEL = re.compile(
    r"(?:"
    r"pass\s*code|passcode|password|kennwort|passwort|code\s*d['’]acc[eè]s"
    r"|c[oó]digo(?:\s*de)?\s*acceso|pin(?:\s*code)?"
    r")"
    r"\s*[:#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._-]{3,31})",
    re.IGNORECASE,
)

# Query param p= on a meet URL
P_PARAM = re.compile(r"[?&]p=([^&#\s<>\"']+)", re.IGNORECASE)


@dataclass
class Meeting:
    url: Optional[str] = None
    meeting_id: Optional[str] = None  # digits only
    passcode: Optional[str] = None
    source: str = ""  # which extractor won

    def join_url(self) -> Optional[str]:
        """Best URL to hand teams-for-linux."""
        if self.url:
            # If we have a short meet URL without p= but we extracted a passcode, attach it.
            if self.passcode and "/meet/" in self.url.lower() and "p=" not in self.url.lower():
                sep = "&" if "?" in self.url else "?"
                return f"{self.url}{sep}p={urllib.parse.quote(self.passcode, safe='')}"
            return self.url
        if self.meeting_id:
            base = f"https://teams.microsoft.com/meet/{self.meeting_id}"
            if self.passcode:
                return f"{base}?p={urllib.parse.quote(self.passcode, safe='')}"
            return base
        return None

    def ok(self) -> bool:
        return bool(self.join_url())


def _clean_url(raw: str) -> str:
    """Strip trailing punctuation that emails/markdown glue onto URLs."""
    raw = raw.strip()
    # Outlook sometimes wraps the URL and then appends a closing paren or period.
    raw = raw.rstrip(").,;]>\"'")
    # Decode a single layer of HTML entities commonly seen in copied mail.
    raw = (
        raw.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return raw


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _looks_like_id(digits: str) -> bool:
    # Teams meeting IDs are typically 9–14 digits. The required example is 13.
    return 9 <= len(digits) <= 16


def parse_invite(text: str) -> Meeting:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Normalize fancy dashes / non-breaking spaces that calendar apps insert.
    text = (
        text.replace("\u00a0", " ")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )

    urls: list[str] = []
    for rx in (ANGLE_URL, BARE_URL):
        for m in rx.finditer(text):
            urls.append(_clean_url(m.group(1) if rx is ANGLE_URL else m.group(1)))

    meetup: Optional[str] = None
    short: Optional[str] = None
    short_id: Optional[str] = None
    short_p: Optional[str] = None

    for u in urls:
        if AKA_JOIN.match(u):
            continue
        if MEETUP_JOIN.match(u):
            meetup = u
            continue
        sm = SHORT_MEET.match(u) or LIVE_MEET.match(u)
        if sm:
            short = u
            short_id = sm.group(1)
            pm = P_PARAM.search(u)
            if pm:
                short_p = urllib.parse.unquote(pm.group(1))

    labeled_id: Optional[str] = None
    m = ID_LABEL.search(text)
    if m:
        d = _digits(m.group(1))
        if _looks_like_id(d):
            labeled_id = d

    if labeled_id is None:
        m = BARE_ID.search(text)
        if m:
            d = _digits(m.group(1))
            if _looks_like_id(d):
                labeled_id = d

    labeled_pass: Optional[str] = None
    m = PASS_LABEL.search(text)
    if m:
        labeled_pass = m.group(1).strip().rstrip(".,;)]}")

    # Preference:
    # 1. Long meetup-join URL — most reliable for teams-for-linux.
    # 2. Short /meet/<id>?p= URL.
    # 3. Labeled ID (+ passcode if we have it).
    meeting = Meeting()

    if meetup:
        meeting.url = meetup
        meeting.meeting_id = labeled_id or short_id
        meeting.passcode = labeled_pass or short_p
        meeting.source = "meetup-join-url"
        return meeting

    if short:
        meeting.url = short
        meeting.meeting_id = short_id or labeled_id
        meeting.passcode = short_p or labeled_pass
        meeting.source = "short-meet-url"
        return meeting

    if labeled_id:
        meeting.meeting_id = labeled_id
        meeting.passcode = labeled_pass or short_p
        meeting.source = "labeled-id"
        return meeting

    return meeting


def read_clipboard() -> str:
    """Wayland first, then X11. Empty string if nothing is available."""
    tools = [
        (["wl-paste", "--no-newline"], None),
        (["xclip", "-selection", "clipboard", "-o"], None),
        (["xsel", "--clipboard", "--output"], None),
    ]
    for cmd, _ in tools:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                return r.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
    return ""


def find_client() -> Optional[list[str]]:
    """Return the argv prefix that launches teams-for-linux.

    Override with TEAMS_FOR_LINUX_CMD (space-separated) if needed.
    """
    override = os.environ.get("TEAMS_FOR_LINUX_CMD")
    if override:
        return override.split()

    # Bare binary on PATH
    for name in ("teams-for-linux", "teams-for-linux-unofficial"):
        path = shutil.which(name)
        if path:
            return [path]

    # Flatpak
    if shutil.which("flatpak"):
        r = subprocess.run(
            ["flatpak", "info", "com.github.IsmaelMartinez.teams_for_linux"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode == 0:
            return ["flatpak", "run", "com.github.IsmaelMartinez.teams_for_linux"]

    # Common install prefixes
    for path in (
        "/opt/teams-for-linux/teams-for-linux",
        "/usr/bin/teams-for-linux",
        "/usr/local/bin/teams-for-linux",
        os.path.expanduser("~/.local/bin/teams-for-linux"),
    ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return [path]

    return None


def launch(client: list[str], url: str, extra: list[str]) -> int:
    # teams-for-linux accepts the meeting URL as a positional arg and as --url.
    # Positional is what the protocol-handler wrappers use; --url is explicit.
    argv = client + extra + ["--url", url]
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"failed to launch {argv[0]}: {e}", file=sys.stderr)
        return 2
    return 0


def notify(title: str, body: str, urgency: str = "normal") -> None:
    if shutil.which("notify-send") is None:
        return
    subprocess.run(
        ["notify-send", "-a", "teams-join", "-u", urgency, title, body],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pull a Teams meeting out of invite text and open it in teams-for-linux.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--stdin", action="store_true", help="read invite text from stdin")
    src.add_argument("--text", metavar="STR", help="parse this string instead of the clipboard")
    src.add_argument("--file", metavar="PATH", help="read invite text from a file")
    p.add_argument("--dry-run", action="store_true", help="parse only; do not launch")
    p.add_argument("--json", action="store_true", help="print parse result as JSON")
    p.add_argument(
        "--print-url",
        action="store_true",
        help="print the join URL on stdout (implies --dry-run if no launch requested)",
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="do not send a desktop notification",
    )
    p.add_argument(
        "--client-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument forwarded to teams-for-linux (repeatable)",
    )
    return p


def load_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.stdin or (not sys.stdin.isatty()):
        # If data is being piped, prefer stdin even without --stdin.
        if args.stdin or not sys.stdin.isatty():
            data = sys.stdin.read()
            if data.strip():
                return data
    clip = read_clipboard()
    if clip.strip():
        return clip
    return ""


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    text = load_text(args)
    if not text.strip():
        msg = "clipboard (and stdin) are empty — copy a Teams invite first"
        print(msg, file=sys.stderr)
        if not args.no_notify and not args.dry_run:
            notify("Teams join", msg, "critical")
        return 1

    meeting = parse_invite(text)
    url = meeting.join_url()

    if args.json:
        payload = asdict(meeting)
        payload["join_url"] = url
        print(json.dumps(payload, indent=2))

    if not meeting.ok() or not url:
        msg = "no Teams meeting ID, passcode, or join URL found in the text"
        if not args.json:
            print(msg, file=sys.stderr)
        if not args.no_notify and not args.dry_run:
            notify("Teams join", msg, "critical")
        return 1

    if args.print_url and not args.json:
        print(url)

    if args.dry_run:
        if not args.json and not args.print_url:
            print(f"source:     {meeting.source}")
            print(f"meeting_id: {meeting.meeting_id or '-'}")
            print(f"passcode:   {meeting.passcode or '-'}")
            print(f"join_url:   {url}")
        return 0

    client = find_client()
    if client is None:
        msg = "teams-for-linux not found (set TEAMS_FOR_LINUX_CMD to override)"
        print(msg, file=sys.stderr)
        if not args.json:
            print(f"join_url would have been: {url}", file=sys.stderr)
        if not args.no_notify:
            notify("Teams join", msg, "critical")
        return 2

    rc = launch(client, url, args.client_arg)
    if rc == 0 and not args.no_notify:
        bits = []
        if meeting.meeting_id:
            bits.append(meeting.meeting_id)
        if meeting.passcode:
            bits.append(f"p={meeting.passcode}")
        notify("Joining Teams meeting", " ".join(bits) or url)
    if not args.json and not args.print_url:
        print(f"launching: {' '.join(client)} --url {url}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
