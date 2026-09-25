#!/usr/bin/python3
"""Extract a Teams or Zoom meeting from invite text and open the desktop client.

Teams is recognized from a Teams join link, or from a numeric meeting id of
12 to 16 digits. Zoom meeting ids are 9 to 11 digits, which overlaps older
Teams ids, so a bare number in that range is not opened as either app.
Zoom is opened only from a Zoom join link.

    python3 teams-join-from-clipboard.py
    python3 teams-join-from-clipboard.py --dry-run --file fixtures/required-invite.txt
    python3 teams-join-from-clipboard.py --plugin

Exit codes:
    0  launched, or a dry run found a single join target
    1  nothing to join, or the text could be either app
    2  a meeting was found but its client is not installed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Callable, Optional


CLIP_MAX = 65536
URL_MAX = 4096
STDERR_MAX = 4096
CLIP_TIMEOUT = 3.0
NOTIFY_TIMEOUT = 2.0
FLATPAK_TIMEOUT = 5.0
# Omarchy's clipboard panel writes this file. Deleting an entry updates the
# file and leaves the Wayland selection alone, so wl-paste can still return
# an invite the panel no longer lists.
HISTORY_MAX = 2_000_000
HISTORY_ENTRIES = 500
HISTORY_RETRY_S = 0.15

TEAMS_HOSTS = frozenset(
    {"teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft"}
)
TEAMS_BINS = (
    "/usr/bin/teams-for-linux",
    "/usr/local/bin/teams-for-linux",
    "/opt/teams-for-linux/teams-for-linux",
)
ZOOM_BINS = (
    "/usr/bin/zoom",
    "/opt/zoom/ZoomLauncher",
)
FLATPAK_BIN = "/usr/bin/flatpak"
TEAMS_FLATPAK = "com.github.IsmaelMartinez.teams_for_linux"
ZOOM_FLATPAK = "us.zoom.Zoom"
SYSTEM_BINS = frozenset(
    TEAMS_BINS
    + ZOOM_BINS
    + (
        FLATPAK_BIN,
        "/usr/bin/wl-paste",
        "/usr/bin/xclip",
        "/usr/bin/xsel",
        "/usr/bin/notify-send",
    )
)

# Readers started in their own session. A stop signal kills these groups and
# does not track the meeting client, which is launched detached on purpose.
_OWNED_GROUPS: list[int] = []


# Outlook and Gmail wrap links as Label<https://...> or as bare URLs.
ANGLE_URL = re.compile(r"<(https?://[^>\s]+)>", re.IGNORECASE)
BARE_URL = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
ZOOM_SCHEME_URL = re.compile(
    r"((?:zoommtg|zoomus)://[^\s<>\"']+)",
    re.IGNORECASE,
)

MEETUP_JOIN = re.compile(
    r"https?://teams\.(?:microsoft\.com|live\.com|cloud\.microsoft)"
    r"/l/meetup-join/[^\s<>\"']+",
    re.IGNORECASE,
)
SHORT_MEET = re.compile(
    r"https?://teams\.(?:microsoft\.com|live\.com|cloud\.microsoft)"
    r"/meet/(\d{9,16})(?:[^\s<>\"']*)?",
    re.IGNORECASE,
)
LIVE_MEET = re.compile(
    r"https?://teams\.live\.com/meet/(\d{9,16})(?:[^\s<>\"']*)?",
    re.IGNORECASE,
)
AKA_JOIN = re.compile(
    r"https?://aka\.ms/JoinTeamsMeeting[^\s<>\"']*",
    re.IGNORECASE,
)

# Labeled meeting id. Digit count is checked after separators are removed.
# "Meeting ID: 412 829 079 975 3" is the form that must keep working.
ID_LABEL = re.compile(
    r"(?:"
    r"meeting\s*id|konferenz[- ]?id|id\s*(?:de\s*r[eé]union|de\s*la\s*r[eé]union)"
    r"|meeting[- ]?nummer|teilnehmer[- ]?id|conference\s*id"
    r")"
    r"\s*[:#]?\s*"
    r"([0-9][0-9 \t.-]{7,30}[0-9])",
    re.IGNORECASE,
)
# Last resort for a Teams-shaped cluster. The smallest match is 12 digits,
# which Zoom does not issue.
BARE_ID = re.compile(
    r"(?<!\d)(\d{3}[ \t.-]\d{3}[ \t.-]\d{3}[ \t.-]\d{3,5}(?:[ \t.-]\d{1,2})?)(?!\d)"
)
PASS_LABEL = re.compile(
    r"(?:"
    r"pass\s*code|passcode|password|kennwort|passwort|code\s*d['’]acc[eè]s"
    r"|c[oó]digo(?:\s*de)?\s*acceso|pin(?:\s*code)?"
    r")"
    r"\s*[:#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._-]{3,31})",
    re.IGNORECASE,
)
P_PARAM = re.compile(r"[?&]p=([^&#\s<>\"']+)", re.IGNORECASE)
ZOOM_PERSONAL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,39})")
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

NOTICES = {
    ("ok", "teams"): (
        "Joining meeting",
        "Opening Teams. Enter the passcode there if it asks.",
    ),
    ("ok", "zoom"): (
        "Joining meeting",
        "Opening Zoom. Enter the passcode there if it asks.",
    ),
    ("empty", ""): (
        "Meeting clipboard",
        "Copy a Teams or Zoom invite, then click the calendar icon.",
    ),
    ("not-found", ""): (
        "Meeting clipboard",
        "No Teams or Zoom join link was found.",
    ),
    ("removed", ""): (
        "Meeting clipboard",
        "That invite was removed from the clipboard.",
    ),
    ("ambiguous", ""): (
        "Meeting clipboard",
        "That text could be Teams or Zoom. Copy a full join link.",
    ),
    ("clipboard-too-large", ""): (
        "Meeting clipboard",
        "The clipboard is too large to scan.",
    ),
    ("clipboard-unavailable", ""): (
        "Meeting clipboard",
        "Could not read the clipboard.",
    ),
    ("no-client", "teams"): (
        "Meeting clipboard",
        "teams-for-linux is not installed.",
    ),
    ("no-client", "zoom"): (
        "Meeting clipboard",
        "Zoom is not installed.",
    ),
    ("launch-failed", ""): (
        "Meeting clipboard",
        "The meeting app did not start.",
    ),
    ("failed", ""): (
        "Meeting clipboard",
        "Could not scan the clipboard.",
    ),
}


@dataclass
class Meeting:
    url: Optional[str] = None
    meeting_id: Optional[str] = None
    passcode: Optional[str] = None
    source: str = ""
    provider: str = ""

    def join_url(self) -> Optional[str]:
        """URL for the desktop client, with the passcode parameter removed."""
        if self.provider == "zoom":
            if not self.url:
                return None
            safe = strip_secret_query(self.url)
            return safe if _as_zoom(safe) == safe else None
        if self.provider != "teams":
            return None
        if self.url:
            safe = strip_secret_query(self.url)
            return safe if _teams_url_ok(safe) else None
        if self.meeting_id and _teams_digits(self.meeting_id):
            base = f"https://teams.microsoft.com/meet/{self.meeting_id}"
            return base if _teams_url_ok(base) else None
        return None

    def ok(self) -> bool:
        return self.provider in {"teams", "zoom"} and bool(self.join_url())


def _on_stop(signum: int, _frame: object) -> None:
    for pid in list(_OWNED_GROUPS):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    raise SystemExit(128 + signum)


def _install_stop_handlers() -> None:
    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _teams_digits(value: str) -> bool:
    # 12 to 16 digits cannot be a Zoom id. Zoom's published range is 9 to 11.
    return bool(re.fullmatch(r"\d{12,16}", value))


def _short_digits(value: str) -> bool:
    return bool(re.fullmatch(r"\d{9,11}", value))


def _clean_url(raw: str) -> str:
    raw = raw.strip().rstrip(").,;]>\"'")
    return (
        raw.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _no_controls(value: str) -> bool:
    return not any(character in value for character in ("\r", "\n", "\x00", " ", "\t"))


# Teams calls the passcode p. Zoom calls its join token pwd. Either value on
# the client command line stays readable for the whole meeting.
_PASSCODE_QUERY_KEYS = frozenset({"p", "pwd"})
_PASSCODE_ARG = re.compile(
    r"(?:^|[?&#]|%3f|%26|%23)(?:p|pwd)(?:=|%3d)",
    re.IGNORECASE,
)


def _query_key(name: str) -> str:
    """Decode a query name twice, so ``%70`` and ``%2570`` both resolve to ``p``."""
    current = name
    for _ in range(2):
        decoded = urllib.parse.unquote_plus(current)
        if decoded == current:
            break
        current = decoded
    return current.lower()


def strip_secret_query(url: str) -> str:
    """Return url without Teams ``p`` or Zoom ``pwd`` parameters.

    Other parameters, including a Teams ``context`` value, stay as they were.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept: list[str] = []
    removed = False
    for piece in parts.query.split("&"):
        if piece and _query_key(piece.split("=", 1)[0]) in _PASSCODE_QUERY_KEYS:
            removed = True
            continue
        kept.append(piece)
    if not removed:
        return url
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment)
    )


def command_exposes_passcode(argv: list[str]) -> bool:
    if any(_PASSCODE_ARG.search(item) for item in argv):
        return True
    for item in argv:
        query = urllib.parse.urlsplit(item).query
        for piece in query.split("&"):
            if piece and _query_key(piece.split("=", 1)[0]) in _PASSCODE_QUERY_KEYS:
                return True
    return False


def _https_parts(url: str) -> Optional[urllib.parse.SplitResult]:
    if not url or len(url) > URL_MAX or not _no_controls(url):
        return None
    if re.search(r"%0[0aAdD]", url):
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        return None
    if parts.username or parts.password or parts.port not in (None, 443):
        return None
    if not parts.hostname:
        return None
    return parts


def _zoom_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    labels = host.split(".")
    if labels[-2:] != ["zoom", "us"]:
        return False
    return all(HOST_LABEL.fullmatch(label) for label in labels)


def _rebuild_zoom(host: str, path: str) -> Optional[str]:
    """Zoom join URL with no pwd parameter.

    Zoom keeps its command line for the whole meeting, so the join token
    cannot travel as an argument. Zoom asks for the passcode itself.
    """
    if not _zoom_host(host):
        return None
    url = f"https://{host}{path}"
    if len(url) > URL_MAX or not _no_controls(url):
        return None
    return url


def _as_zoom(raw: str) -> Optional[str]:
    """Return a rebuilt Zoom join URL, or None when the link is not one."""
    parts = _https_parts(raw)
    if parts is None or not _zoom_host(parts.hostname or ""):
        return None
    path = parts.path or ""
    join = re.fullmatch(r"/(?:j|wc/join)/(\d{9,11})/?", path)
    if join:
        return _rebuild_zoom(parts.hostname or "", f"/j/{join.group(1)}")
    personal = re.fullmatch(r"/my/([A-Za-z0-9](?:[A-Za-z0-9._-]{0,39}))/?", path)
    if personal and ZOOM_PERSONAL.fullmatch(personal.group(1)):
        return _rebuild_zoom(parts.hostname or "", f"/my/{personal.group(1)}")
    return None


def _as_zoom_scheme(raw: str) -> Optional[str]:
    if len(raw) > URL_MAX or not _no_controls(raw):
        return None
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme.lower() not in {"zoommtg", "zoomus"}:
        return None
    if parts.username or parts.password or (parts.hostname or "").lower() != "zoom.us":
        return None
    if parts.path.rstrip("/") != "/join":
        return None
    parsed = urllib.parse.parse_qs(parts.query, keep_blank_values=False)
    confno = parsed.get("confno", [])
    if len(confno) != 1 or not re.fullmatch(r"\d{9,11}", confno[0]):
        return None
    return _rebuild_zoom("zoom.us", f"/j/{confno[0]}")


def _teams_url_ok(url: str) -> bool:
    parts = _https_parts(url)
    if parts is None or (parts.hostname or "").lower() not in TEAMS_HOSTS:
        return False
    path = parts.path or ""
    if path.startswith("/l/meetup-join/") and len(path) > len("/l/meetup-join/"):
        return True
    return bool(re.fullmatch(r"/meet/\d{9,16}", path))


def _collect_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in ANGLE_URL.finditer(text):
        found.append(_clean_url(match.group(1)))
    for match in BARE_URL.finditer(text):
        found.append(_clean_url(match.group(1)))
    for match in ZOOM_SCHEME_URL.finditer(text):
        found.append(_clean_url(match.group(1)))
    unique: list[str] = []
    for url in found:
        if url not in unique:
            unique.append(url)
    return unique


def _labeled_id(text: str) -> Optional[str]:
    match = ID_LABEL.search(text)
    if not match:
        return None
    digits = _digits(match.group(1))
    if _teams_digits(digits) or _short_digits(digits):
        return digits
    return None


def _bare_teams_id(text: str) -> Optional[str]:
    match = BARE_ID.search(text)
    if not match:
        return None
    digits = _digits(match.group(1))
    return digits if _teams_digits(digits) else None


def _labeled_passcode(text: str) -> Optional[str]:
    match = PASS_LABEL.search(text)
    if not match:
        return None
    return match.group(1).strip().rstrip(".,;)]}")


def _zoom_id(url: str) -> Optional[str]:
    match = re.search(r"/j/(\d{9,11})(?:\?|$)", url)
    return match.group(1) if match else None


def _ambiguous(source: str, meeting_id: Optional[str] = None) -> Meeting:
    return Meeting(meeting_id=meeting_id, source=source, provider="ambiguous")


def parse_invite(text: str) -> Meeting:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = (
        text.replace("\u00a0", " ")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )
    if "\x00" in text:
        return Meeting()

    teams_urls: list[str] = []
    zoom_urls: list[str] = []
    for raw in _collect_urls(text):
        if AKA_JOIN.match(raw):
            continue
        zoom = _as_zoom(raw) or _as_zoom_scheme(raw)
        if zoom:
            if zoom not in zoom_urls:
                zoom_urls.append(zoom)
            continue
        if not (MEETUP_JOIN.match(raw) or SHORT_MEET.match(raw) or LIVE_MEET.match(raw)):
            continue
        if _teams_url_ok(urllib.parse.urlunsplit(urllib.parse.urlsplit(raw)._replace(fragment=""))):
            cleaned = urllib.parse.urlunsplit(urllib.parse.urlsplit(raw)._replace(fragment=""))
            if cleaned not in teams_urls:
                teams_urls.append(cleaned)

    labeled = _labeled_id(text)
    bare = _bare_teams_id(text) if labeled is None else None
    long_id = labeled if labeled and _teams_digits(labeled) else bare
    short_id = labeled if labeled and _short_digits(labeled) else None
    passcode = _labeled_passcode(text)

    zoom_ids = {_zoom_id(url) for url in zoom_urls}
    zoom_ids.discard(None)
    if len(zoom_urls) > 1 and len(zoom_ids) > 1:
        return _ambiguous("ambiguous-several", labeled or long_id)
    if teams_urls and zoom_urls:
        return _ambiguous("ambiguous-both", labeled or long_id)
    if zoom_urls:
        zoom_id = _zoom_id(zoom_urls[0])
        if long_id and long_id != zoom_id:
            return _ambiguous("ambiguous-mismatch", long_id)
        if short_id and zoom_id and short_id != zoom_id:
            return _ambiguous("ambiguous-mismatch", short_id)
        meeting = Meeting(
            url=zoom_urls[0],
            meeting_id=zoom_id or short_id,
            passcode=passcode,
            source="zoom-personal" if "/my/" in zoom_urls[0] else "zoom-url",
            provider="zoom",
        )
        return meeting
    meetup_paths = [url for url in teams_urls if "/l/meetup-join/" in url.lower()]
    meet_ids = []
    for url in teams_urls:
        match = re.search(r"/meet/(\d{9,16})", url, re.IGNORECASE)
        if match:
            meet_ids.append(match.group(1))
    if len(meetup_paths) > 1 or len(set(meet_ids)) > 1:
        return _ambiguous("ambiguous-several", labeled or long_id)

    meetup = next((url for url in teams_urls if "/l/meetup-join/" in url.lower()), None)
    short = next((url for url in teams_urls if "/meet/" in url.lower()), None)
    if meetup or short:
        chosen = meetup or short
        assert chosen is not None
        short_match = SHORT_MEET.match(chosen) or LIVE_MEET.match(chosen)
        url_id = short_match.group(1) if short_match else None
        param = P_PARAM.search(chosen)
        url_pass = urllib.parse.unquote(param.group(1)) if param else None
        return Meeting(
            url=strip_secret_query(chosen),
            meeting_id=labeled or url_id or long_id,
            passcode=passcode or url_pass,
            source="meetup-join-url" if meetup else "short-meet-url",
            provider="teams",
        )

    if long_id:
        return Meeting(
            meeting_id=long_id,
            passcode=passcode,
            source="labeled-id",
            provider="teams",
        )
    if short_id:
        # 9 to 11 digits is both a legacy Teams id and a Zoom id.
        return _ambiguous("ambiguous-length", short_id)
    return Meeting()


def root_owned_executable(path: str) -> bool:
    """True for a fixed system binary, following one root-owned link under /usr or /opt."""
    if path not in SYSTEM_BINS:
        return False
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    # Symlinks are reported as mode 777 on Linux. The owner of the link and the
    # mode of the regular file it names are the checks that mean something.
    if metadata.st_uid != 0:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            return False
        if not target.startswith("/"):
            target = os.path.join(os.path.dirname(path), target)
        parts = target.split("/")
        if ".." in parts or not target.startswith(("/usr/", "/opt/")):
            return False
        try:
            target_meta = os.lstat(target)
        except OSError:
            return False
        if (
            not stat.S_ISREG(target_meta.st_mode)
            or target_meta.st_uid != 0
            or target_meta.st_mode & 0o022
            or not target_meta.st_mode & stat.S_IXUSR
        ):
            return False
        return True
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
        return False
    return bool(metadata.st_mode & stat.S_IXUSR)


def _first_binary(paths: tuple[str, ...], executable: Callable[[str], bool]) -> Optional[str]:
    for path in paths:
        if executable(path):
            return path
    return None


def _gui_env() -> dict[str, str]:
    kept = (
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_MESSAGES",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_DATA_DIRS",
        "XDG_CONFIG_DIRS",
    )
    env: dict[str, str] = {"PATH": "/usr/bin:/bin"}
    for key in kept:
        value = os.environ.get(key)
        if value and "\n" not in value and "\r" not in value and "\x00" not in value:
            env[key] = value
    return env


def _stop_proc(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=0.4)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return


def run_bounded(
    argv: list[str],
    timeout: float,
    max_bytes: int,
    env: dict[str, str],
) -> tuple[int, bytes, str]:
    """Run argv. Return (code, stdout, problem). problem is empty on success.

    Stdout is retained only up to max_bytes. One extra byte marks overflow and
    the retained bytes are dropped. The child runs in its own session.
    """
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
    except OSError:
        return 1, b"", "spawn"
    assert proc.stdout is not None and proc.stderr is not None
    _OWNED_GROUPS.append(proc.pid)
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    streams = {
        proc.stdout.fileno(): (stdout_buf, max_bytes, "overflow"),
        proc.stderr.fileno(): (stderr_buf, STDERR_MAX, "err-overflow"),
    }
    problem = ""
    deadline = time.monotonic() + timeout
    try:
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                problem = "timeout"
                break
            readable, _, _ = select.select(list(streams), [], [], remaining)
            if not readable:
                problem = "timeout"
                break
            for descriptor in readable:
                buf, cap, kind = streams[descriptor]
                room = cap + 1 - len(buf)
                if room <= 0:
                    problem = kind
                    break
                chunk = os.read(descriptor, min(65536, room))
                if not chunk:
                    streams.pop(descriptor, None)
                    continue
                if len(buf) + len(chunk) > cap:
                    problem = kind
                    break
                buf.extend(chunk)
            if problem:
                break
        if problem:
            _stop_proc(proc)
            return 1, b"", problem
        try:
            code = proc.wait(timeout=max(0.2, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _stop_proc(proc)
            return 1, b"", "timeout"
        return code, bytes(stdout_buf), ""
    finally:
        try:
            _OWNED_GROUPS.remove(proc.pid)
        except ValueError:
            pass
        for pipe in (proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass


def _decode_clip(code: int, data: bytes, problem: str) -> tuple[str, str]:
    if problem == "overflow":
        return "", "too-large"
    if problem:
        return "", "unavailable"
    if b"\x00" in data:
        return "", "unavailable"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "", "unavailable"
    if text.strip():
        return text, ""
    if code != 0:
        return "", "empty"
    return "", "empty"


def _read_reader(argv: list[str]) -> tuple[str, str]:
    code, data, problem = run_bounded(argv, CLIP_TIMEOUT, CLIP_MAX, _gui_env())
    return _decode_clip(code, data, problem)


def read_clipboard() -> tuple[str, str]:
    """Return (text, problem). problem is empty, empty, too-large, or unavailable."""
    if root_owned_executable("/usr/bin/wl-paste"):
        text, problem = _read_reader(
            ["/usr/bin/wl-paste", "--no-newline", "--type", "text/plain"]
        )
        if problem == "too-large" or text.strip():
            return text, problem
        # A copy that is only HTML, or an empty clipboard. One more capped read.
        # Images are not UTF-8 text and come back unavailable.
        if problem in {"empty", "unavailable"}:
            return _read_reader(["/usr/bin/wl-paste", "--no-newline"])
        return text, problem
    if root_owned_executable("/usr/bin/xclip"):
        return _read_reader(["/usr/bin/xclip", "-selection", "clipboard", "-o"])
    if root_owned_executable("/usr/bin/xsel"):
        return _read_reader(["/usr/bin/xsel", "--clipboard", "--output"])
    return "", "unavailable"


def clipboard_history_path() -> Optional[str]:
    """Path of the clipboard panel's history, or None when HOME is unusable."""
    home = os.environ.get("HOME", "")
    if not home.startswith("/") or any(char in home for char in ("\n", "\r", "\x00")):
        return None
    return os.path.join(home, ".local", "state", "omarchy", "clipboard-history.json")


def _norm_clip(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _read_history_file(path: str) -> tuple[str, bytes]:
    """Return (state, bytes). state is missing, unreadable, or ok.

    A missing panel means there is nothing to check. An unreadable file is
    also not a reason to skip a join: a broken history must not disable the click.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return "missing", b""
    except OSError:
        return "unreadable", b""
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > HISTORY_MAX
        ):
            return "unreadable", b""
        os.set_blocking(fd, True)
        buf = bytearray()
        while len(buf) <= HISTORY_MAX:
            chunk = os.read(fd, min(65536, HISTORY_MAX + 1 - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
        if len(buf) > HISTORY_MAX:
            return "unreadable", b""
        return "ok", bytes(buf)
    finally:
        os.close(fd)


def _history_texts(data: bytes) -> Optional[set[str]]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    texts: set[str] = set()
    for item in parsed[:HISTORY_ENTRIES]:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str) or len(text) > CLIP_MAX:
            continue
        norm = _norm_clip(text)
        if norm:
            texts.add(norm)
    return texts


def removed_from_panel(
    text: str,
    path: Optional[str] = None,
    retry_s: float = HISTORY_RETRY_S,
) -> bool:
    """True when the clipboard panel exists and no longer lists this text.

    A copy can reach wl-paste before the panel writes it down, so a miss is
    checked twice. A panel that is not installed, or a history file that
    cannot be read, does not block the join.
    """
    if path is None:
        path = clipboard_history_path()
    if not path:
        return False
    norm = _norm_clip(text)
    if not norm:
        return False

    def listed() -> Optional[bool]:
        state, data = _read_history_file(path)
        if state != "ok":
            return None
        texts = _history_texts(data)
        if texts is None:
            return None
        return norm in texts

    found = listed()
    if found is None or found:
        return False
    if retry_s > 0:
        time.sleep(retry_s)
        found = listed()
        if found is None or found:
            return False
    return True


def flatpak_installed(app_id: str, executable: Callable[[str], bool] = root_owned_executable) -> bool:
    if app_id not in {TEAMS_FLATPAK, ZOOM_FLATPAK} or not executable(FLATPAK_BIN):
        return False
    code, _data, problem = run_bounded(
        [FLATPAK_BIN, "info", "--", app_id],
        FLATPAK_TIMEOUT,
        CLIP_MAX,
        _gui_env(),
    )
    return problem == "" and code == 0


def launch_argv(
    meeting: Meeting,
    executable: Callable[[str], bool] = root_owned_executable,
    flatpak_ready: Optional[Callable[[str], bool]] = None,
) -> Optional[list[str]]:
    url = meeting.join_url()
    if not meeting.ok() or not url or not _no_controls(url):
        return None
    # join_url() already drops p and pwd. Refuse here too, so a later edit
    # cannot hand the passcode to the long-lived client by accident.
    if command_exposes_passcode([url]):
        return None
    ready = flatpak_installed if flatpak_ready is None else flatpak_ready
    argv: Optional[list[str]] = None
    if meeting.provider == "teams":
        if not _teams_url_ok(url):
            return None
        binary = _first_binary(TEAMS_BINS, executable)
        if binary:
            argv = [binary, "--url", url]
        elif ready(TEAMS_FLATPAK):
            argv = [FLATPAK_BIN, "run", "--", TEAMS_FLATPAK, "--url", url]
    elif meeting.provider == "zoom":
        if _as_zoom(url) != url:
            return None
        binary = _first_binary(ZOOM_BINS, executable)
        if binary:
            argv = [binary, url]
        elif ready(ZOOM_FLATPAK):
            argv = [FLATPAK_BIN, "run", "--", ZOOM_FLATPAK, url]
    if argv is None or command_exposes_passcode(argv):
        return None
    return argv


def spawn_detached(argv: list[str]) -> bool:
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_gui_env(),
        )
    except OSError:
        return False
    return True


def notify(kind: str, provider: str = "") -> None:
    title, body = NOTICES.get((kind, provider), NOTICES.get((kind, ""), NOTICES[("failed", "")]))
    if not root_owned_executable("/usr/bin/notify-send"):
        return
    run_bounded(
        ["/usr/bin/notify-send", "-a", "meeting-clipboard", "-u", "normal", title, body],
        NOTIFY_TIMEOUT,
        1024,
        _gui_env(),
    )


def public_status(kind: str, provider: str = "", source: str = "") -> dict[str, str | bool]:
    """Status for the bar. Meeting ids, passcodes, and URLs stay out of it."""
    if kind == "ok":
        payload: dict[str, str | bool] = {"ok": True, "provider": provider}
        if source:
            payload["source"] = source
        return payload
    payload = {"ok": False, "error": kind}
    if provider in {"teams", "zoom"}:
        payload["provider"] = provider
    if source:
        payload["reason"] = source
    return payload


def emit_status(payload: dict[str, str | bool]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")


def plugin_main() -> int:
    _install_stop_handlers()
    try:
        return _plugin_main()
    except SystemExit:
        raise
    except Exception:
        payload = public_status("failed")
        emit_status(payload)
        notify("failed")
        return 1


def _plugin_main() -> int:
    text, problem = read_clipboard()
    if problem == "too-large":
        emit_status(public_status("clipboard-too-large"))
        notify("clipboard-too-large")
        return 1
    if problem == "unavailable":
        emit_status(public_status("clipboard-unavailable"))
        notify("clipboard-unavailable")
        return 1
    if problem == "empty" or not text.strip():
        emit_status(public_status("empty"))
        notify("empty")
        return 1

    meeting = parse_invite(text)
    if meeting.provider == "ambiguous":
        emit_status(public_status("ambiguous", source=meeting.source))
        notify("ambiguous")
        return 1
    if not meeting.ok():
        emit_status(public_status("not-found"))
        notify("not-found")
        return 1
    if removed_from_panel(text):
        emit_status(public_status("removed"))
        notify("removed")
        return 1

    argv = launch_argv(meeting)
    if argv is None:
        emit_status(public_status("no-client", meeting.provider))
        notify("no-client", meeting.provider)
        return 2
    if not spawn_detached(argv):
        emit_status(public_status("launch-failed", meeting.provider))
        notify("launch-failed")
        return 2
    emit_status(public_status("ok", meeting.provider, meeting.source))
    notify("ok", meeting.provider)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open the Teams or Zoom meeting in the clipboard.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--stdin", action="store_true", help="read invite text from stdin")
    source.add_argument("--text", metavar="STR", help="parse this string instead of the clipboard")
    source.add_argument("--file", metavar="PATH", help="read invite text from a file")
    parser.add_argument("--dry-run", action="store_true", help="parse only; do not launch")
    parser.add_argument("--json", action="store_true", help="print the parse result as JSON")
    parser.add_argument("--print-url", action="store_true", help="print the join URL")
    parser.add_argument("--no-notify", action="store_true", help="do not send a desktop notification")
    parser.add_argument(
        "--plugin",
        action="store_true",
        help="read the clipboard, launch the client, print a short status line",
    )
    parser.add_argument(
        "--client-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument forwarded to the client (repeatable, terminal use)",
    )
    return parser


def load_text(args: argparse.Namespace) -> tuple[str, str, bool]:
    """Return (text, problem, from_clipboard)."""
    if args.text is not None:
        return args.text, "", False
    if args.file:
        with open(args.file, "rb") as handle:
            data = handle.read(CLIP_MAX + 1)
        if len(data) > CLIP_MAX:
            return "", "too-large", False
        try:
            return data.decode("utf-8"), "", False
        except UnicodeDecodeError:
            return "", "unavailable", False
    if args.stdin or not sys.stdin.isatty():
        data = sys.stdin.read(CLIP_MAX + 1)
        if len(data) > CLIP_MAX:
            return "", "too-large", False
        if data.strip():
            return data, "", False
    text, problem = read_clipboard()
    return text, problem, True


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.plugin:
        return plugin_main()
    _install_stop_handlers()
    text, problem, from_clipboard = load_text(args)
    if problem == "too-large":
        print("clipboard text exceeds the size limit", file=sys.stderr)
        return 1
    if problem == "unavailable" or not text.strip():
        message = "clipboard is empty or unreadable — copy a meeting invite first"
        print(message, file=sys.stderr)
        if not args.no_notify and not args.dry_run:
            notify("empty" if problem != "unavailable" else "clipboard-unavailable")
        return 1

    meeting = parse_invite(text)
    url = meeting.join_url()
    if args.json:
        payload = asdict(meeting)
        payload["join_url"] = url
        print(json.dumps(payload, indent=2))

    if meeting.provider == "ambiguous" or not meeting.ok() or not url:
        if meeting.provider == "ambiguous":
            message = "meeting text matches both Teams and Zoom shapes; copy one full join link"
        else:
            message = "no Teams or Zoom join target found in the text"
        if not args.json:
            print(message, file=sys.stderr)
        if not args.no_notify and not args.dry_run:
            notify("ambiguous" if meeting.provider == "ambiguous" else "not-found")
        return 1
    if from_clipboard and removed_from_panel(text):
        if not args.json:
            print("that invite was removed from the clipboard", file=sys.stderr)
        if not args.no_notify and not args.dry_run:
            notify("removed")
        return 1

    if args.print_url and not args.json:
        print(url)
    if args.dry_run:
        if not args.json and not args.print_url:
            print(f"provider:   {meeting.provider}")
            print(f"source:     {meeting.source}")
            print(f"meeting_id: {meeting.meeting_id or '-'}")
            print(f"passcode:   {meeting.passcode or '-'}")
            print(f"join_url:   {url}")
        return 0

    client = launch_argv(meeting)
    if client is None:
        print(f"{meeting.provider} client not found", file=sys.stderr)
        if not args.no_notify:
            notify("no-client", meeting.provider)
        return 2
    extra = [arg for arg in args.client_arg if arg and not arg.startswith("-") and _no_controls(arg)]
    command = client + extra
    if command_exposes_passcode(command):
        print("refusing to put a meeting passcode on the client command line", file=sys.stderr)
        if not args.no_notify:
            notify("launch-failed")
        return 2
    if not spawn_detached(command):
        print("failed to launch the meeting client", file=sys.stderr)
        if not args.no_notify:
            notify("launch-failed")
        return 2
    if not args.no_notify:
        notify("ok", meeting.provider)
    if not args.json and not args.print_url:
        print(f"launching: {meeting.provider}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
