#!/usr/bin/env python3
"""Parser, launch-argument, and clipboard-cap checks. Nothing here opens a meeting."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib.machinery import SourceFileLoader

mod = SourceFileLoader(
    "teams_join",
    str(Path(__file__).resolve().parent / "teams-join-from-clipboard.py"),
).load_module()

ROOT = Path(__file__).resolve().parent
REQUIRED = (ROOT / "fixtures" / "required-invite.txt").read_text(encoding="utf-8")

CASES = [
    (
        "required-outlook-angle-brackets",
        REQUIRED,
        {
            "provider": "teams",
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
            "provider": "teams",
            "source": "labeled-id",
            "meeting_id": "4128290799753",
            "passcode": "Sd94S45U",
            "join_url": "https://teams.microsoft.com/meet/4128290799753",
        },
    ),
    (
        "meetup-keeps-context-and-drops-passcode-parameter",
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d&p=AbCdEf12",
        {
            "provider": "teams",
            "source": "meetup-join-url",
            "passcode": "AbCdEf12",
            "join_url": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d",
        },
    ),
    (
        "short-meet-url-with-p",
        "Join: https://teams.microsoft.com/meet/31827829071516?p=AbCdEf12 extra",
        {
            "provider": "teams",
            "source": "short-meet-url",
            "meeting_id": "31827829071516",
            "passcode": "AbCdEf12",
        },
    ),
    (
        "meetup-join-bare",
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d",
        {
            "provider": "teams",
            "source": "meetup-join-url",
            "url_contains": "meetup-join/19%3ameeting_abc",
        },
    ),
    (
        "german-labels",
        "Konferenz-ID: 123 456 789 012\nKennwort: Qw3rTy88",
        {
            "provider": "teams",
            "source": "labeled-id",
            "meeting_id": "123456789012",
            "passcode": "Qw3rTy88",
        },
    ),
    (
        "dash-separated-id",
        "Meeting ID: 412-829-079-975-3\nPasscode: Sd94S45U",
        {
            "provider": "teams",
            "meeting_id": "4128290799753",
            "passcode": "Sd94S45U",
        },
    ),
    (
        "teams-host-wins-on-short-id",
        "https://teams.microsoft.com/meet/1234567890?p=AbCdEf12",
        {
            "provider": "teams",
            "source": "short-meet-url",
            "meeting_id": "1234567890",
        },
    ),
]

REJECTS = [
    (
        "zoom-overlap-length",
        "Meeting ID: 812 3456 7890\nPasscode: 123456\n",
        "ambiguous-length",
    ),
    (
        "zoom-phrase-without-link",
        "Join Zoom Meeting\nMeeting ID: 81234567890\nPasscode: 123456\n",
        "ambiguous-length",
    ),
    (
        "legacy-nine-digits",
        "Meeting ID: 123 456 789\nPasscode: AbCdEf12\n",
        "ambiguous-length",
    ),
]

ZOOM_CASES = [
    (
        "zoom-join-link",
        "Join Zoom Meeting\nhttps://us02web.zoom.us/j/81234567890?pwd=TokenOnly&uname=Pat\nMeeting ID: 812 3456 7890\nPasscode: 123456\n",
        {
            "provider": "zoom",
            "source": "zoom-url",
            "meeting_id": "81234567890",
            "join_url": "https://us02web.zoom.us/j/81234567890",
        },
    ),
    (
        "zoom-without-pwd-keeps-human-passcode-off-the-url",
        "https://zoom.us/j/81234567890\nPasscode: 123456\n",
        {
            "provider": "zoom",
            "join_url": "https://zoom.us/j/81234567890",
        },
    ),
    (
        "zoom-personal",
        "https://company.zoom.us/my/alex.smith?uname=Pat",
        {
            "provider": "zoom",
            "source": "zoom-personal",
            "join_url": "https://company.zoom.us/my/alex.smith",
        },
    ),
    (
        "zoom-scheme",
        "zoommtg://zoom.us/join?confno=81234567890&pwd=TokenOnly",
        {
            "provider": "zoom",
            "join_url": "https://zoom.us/j/81234567890",
        },
    ),
    (
        "web-client-path",
        "https://us02web.zoom.us/wc/join/81234567890?pwd=TokenOnly",
        {
            "provider": "zoom",
            "join_url": "https://us02web.zoom.us/j/81234567890",
        },
    ),
]

NOT_ZOOM = [
    "https://zoom.us.evil.example/j/81234567890",
    "https://notzoom.us/j/81234567890",
    "https://evilzoom.us/j/81234567890",
    "https://user:pass@zoom.us/j/81234567890",
    "http://zoom.us/j/81234567890",
    "https://zoom.us/j/8123456789012",
]


def check_expect(name: str, meeting, expect: dict) -> list[str]:
    url = meeting.join_url()
    errs = []
    if "provider" in expect and meeting.provider != expect["provider"]:
        errs.append(f"provider={meeting.provider!r} want {expect['provider']!r}")
    if "source" in expect and meeting.source != expect["source"]:
        errs.append(f"source={meeting.source!r} want {expect['source']!r}")
    if "meeting_id" in expect and meeting.meeting_id != expect["meeting_id"]:
        errs.append(f"id={meeting.meeting_id!r} want {expect['meeting_id']!r}")
    if "passcode" in expect and meeting.passcode != expect["passcode"]:
        errs.append(f"pass={meeting.passcode!r} want {expect['passcode']!r}")
    if "url_contains" in expect and (not meeting.url or expect["url_contains"] not in meeting.url):
        errs.append(f"url missing {expect['url_contains']!r}: {meeting.url!r}")
    if "join_url" in expect and url != expect["join_url"]:
        errs.append(f"join_url={url!r} want {expect['join_url']!r}")
    if not meeting.ok():
        errs.append("meeting.ok() is False")
    return errs


def test_parse() -> int:
    failed = 0
    for name, text, expect in CASES + ZOOM_CASES:
        errs = check_expect(name, mod.parse_invite(text), expect)
        if errs:
            failed += 1
            print(f"FAIL {name}: {'; '.join(errs)}")
        else:
            print(f"ok   {name}")
    for name, text, source in REJECTS:
        meeting = mod.parse_invite(text)
        errs = []
        if meeting.provider != "ambiguous" or meeting.ok() or meeting.source != source:
            errs.append(
                f"provider={meeting.provider!r} source={meeting.source!r} ok={meeting.ok()}"
            )
        if meeting.join_url():
            errs.append(f"join_url leaked {meeting.join_url()!r}")
        if errs:
            failed += 1
            print(f"FAIL {name}: {'; '.join(errs)}")
        else:
            print(f"ok   {name}")
    for text in NOT_ZOOM:
        meeting = mod.parse_invite(text)
        if meeting.provider == "zoom" or meeting.ok():
            failed += 1
            print(f"FAIL not-zoom {text!r}: provider={meeting.provider!r}")
        else:
            print(f"ok   not-zoom {text.split('/')[2] if '//' in text else text}")
    help_only = mod.parse_invite("Need help? https://aka.ms/JoinTeamsMeeting?omkt=en-US")
    if help_only.ok() or help_only.provider not in {"", "ambiguous"}:
        failed += 1
        print(f"FAIL aka.ms-only: {help_only}")
    else:
        print("ok   aka.ms-only")
    both = mod.parse_invite(
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d\n"
        "https://zoom.us/j/81234567890\n"
    )
    if both.provider != "ambiguous" or both.ok():
        failed += 1
        print(f"FAIL both-providers: {both}")
    else:
        print("ok   both-providers")
    mismatch = mod.parse_invite(
        "https://zoom.us/j/81234567890\nMeeting ID: 412 829 079 975 3\n"
    )
    if mismatch.provider != "ambiguous" or mismatch.source != "ambiguous-mismatch":
        failed += 1
        print(f"FAIL zoom-plus-teams-id: {mismatch}")
    else:
        print("ok   zoom-plus-teams-id")
    return failed


def _argv_text(argv) -> str:
    return "\n".join(argv or [])


def _passcode_leaked(argv, secrets: tuple[str, ...]) -> str:
    blob = _argv_text(argv)
    if argv and mod.command_exposes_passcode(argv):
        return f"passcode query in argv: {argv!r}"
    for secret in secrets:
        if secret and secret in blob:
            return f"{secret!r} in argv: {argv!r}"
    return ""


def test_launch_argv() -> int:
    failed = 0
    teams_bin = lambda path: path == "/usr/bin/teams-for-linux"
    no_flatpak = lambda _app: False
    meeting = mod.parse_invite(REQUIRED)
    argv = mod.launch_argv(meeting, executable=teams_bin, flatpak_ready=no_flatpak)
    url = meeting.join_url()
    leaked = _passcode_leaked(argv, ("Sd94S45U",))
    if argv != ["/usr/bin/teams-for-linux", "--url", url] or leaked:
        failed += 1
        print(f"FAIL teams argv: {argv!r} {leaked}")
    else:
        print("ok   teams argv")

    labeled = mod.parse_invite("Meeting ID: 412 829 079 975 3\nPasscode: Sd94S45U\n")
    argv = mod.launch_argv(labeled, executable=teams_bin, flatpak_ready=no_flatpak)
    leaked = _passcode_leaked(argv, ("Sd94S45U",))
    if argv != ["/usr/bin/teams-for-linux", "--url", "https://teams.microsoft.com/meet/4128290799753"] or leaked:
        failed += 1
        print(f"FAIL labeled teams argv: {argv!r} {leaked}")
    else:
        print("ok   labeled teams argv omits passcode")

    short = mod.parse_invite("https://teams.microsoft.com/meet/31827829071516?p=AbCdEf12")
    argv = mod.launch_argv(short, executable=teams_bin, flatpak_ready=no_flatpak)
    leaked = _passcode_leaked(argv, ("AbCdEf12",))
    if argv != ["/usr/bin/teams-for-linux", "--url", "https://teams.microsoft.com/meet/31827829071516"] or leaked:
        failed += 1
        print(f"FAIL short teams argv: {argv!r} {leaked}")
    else:
        print("ok   short teams argv omits p")

    encoded = mod.parse_invite("https://teams.microsoft.com/meet/31827829071516?%70=AbCdEf12")
    argv = mod.launch_argv(encoded, executable=teams_bin, flatpak_ready=no_flatpak)
    leaked = _passcode_leaked(argv, ("AbCdEf12",))
    if leaked or not argv or "31827829071516" not in argv[-1]:
        failed += 1
        print(f"FAIL encoded p argv: {argv!r} {leaked}")
    else:
        print("ok   encoded p is not launched")

    zoom = mod.parse_invite("https://zoom.us/j/81234567890?pwd=TokenOnly")
    argv = mod.launch_argv(zoom, executable=lambda path: path == "/opt/zoom/ZoomLauncher", flatpak_ready=no_flatpak)
    leaked = _passcode_leaked(argv, ("TokenOnly",))
    if argv != ["/opt/zoom/ZoomLauncher", "https://zoom.us/j/81234567890"] or leaked:
        failed += 1
        print(f"FAIL zoom argv: {argv!r} {leaked}")
    else:
        print("ok   zoom argv omits pwd")

    scheme = mod.parse_invite("zoommtg://zoom.us/join?confno=81234567890&pwd=TokenOnly")
    argv = mod.launch_argv(scheme, executable=lambda path: path == "/usr/bin/zoom", flatpak_ready=no_flatpak)
    leaked = _passcode_leaked(argv, ("TokenOnly",))
    if argv != ["/usr/bin/zoom", "https://zoom.us/j/81234567890"] or leaked:
        failed += 1
        print(f"FAIL zoom scheme argv: {argv!r} {leaked}")
    else:
        print("ok   zoom scheme argv omits pwd")

    flatpak = mod.launch_argv(
        zoom,
        executable=lambda path: path == "/usr/bin/flatpak",
        flatpak_ready=lambda app: app == mod.ZOOM_FLATPAK,
    )
    leaked = _passcode_leaked(flatpak, ("TokenOnly",))
    if flatpak != ["/usr/bin/flatpak", "run", "--", mod.ZOOM_FLATPAK, zoom.join_url()] or leaked:
        failed += 1
        print(f"FAIL zoom flatpak argv: {flatpak!r} {leaked}")
    else:
        print("ok   zoom flatpak argv omits pwd")

    ambiguous = mod.parse_invite("Meeting ID: 81234567890")
    if mod.launch_argv(ambiguous, executable=lambda _path: True, flatpak_ready=lambda _app: True) is not None:
        failed += 1
        print("FAIL ambiguous produced a launch command")
    else:
        print("ok   ambiguous has no launch")

    called = []

    def recording(path: str) -> bool:
        called.append(path)
        return False

    mod.launch_argv(meeting, executable=recording, flatpak_ready=lambda _app: False)
    if any(path.startswith("/tmp") or not path.startswith("/") for path in called):
        failed += 1
        print(f"FAIL launch searched an unexpected path: {called!r}")
    elif called != list(mod.TEAMS_BINS):
        failed += 1
        print(f"FAIL launch candidates: {called!r}")
    else:
        print("ok   launch candidates are fixed")

    launched: list[list[str]] = []
    original = mod.spawn_detached

    def record(argv: list[str]) -> bool:
        launched.append(list(argv))
        return True

    mod.spawn_detached = record
    try:
        code = mod.main(
            [
                "--text",
                "https://zoom.us/j/81234567890?pwd=TokenOnly",
                "--no-notify",
                "--client-arg",
                "https://zoom.us/j/81234567890?pwd=TokenOnly",
            ]
        )
    finally:
        mod.spawn_detached = original
    if launched or code == 0:
        failed += 1
        print(f"FAIL client-arg restored pwd: rc={code} launched={launched!r}")
    else:
        print("ok   client-arg cannot put pwd on the command line")
    return failed


def test_status_hides_meeting_material() -> int:
    failed = 0
    meeting = mod.parse_invite(REQUIRED)
    payload = mod.public_status("ok", meeting.provider, meeting.source)
    blob = json.dumps(payload)
    for secret in ("4128290799753", "Sd94S45U", "http", "passcode", "join_url"):
        if secret in blob:
            failed += 1
            print(f"FAIL status leaked {secret}: {blob}")
            return failed
    ambiguous = mod.public_status("ambiguous", source="ambiguous-length")
    blob = json.dumps(ambiguous)
    if "81234567890" in blob or ambiguous.get("error") != "ambiguous":
        failed += 1
        print(f"FAIL ambiguous status: {blob}")
    else:
        print("ok   status hides meeting material")
    return failed


def test_bounds() -> int:
    failed = 0
    code, data, problem = mod.run_bounded(
        ["/usr/bin/python3", "-I", "-S", "-c", "import sys; sys.stdout.write('x' * 1000)"],
        timeout=5,
        max_bytes=100,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if problem != "overflow" or data:
        failed += 1
        print(f"FAIL overflow: code={code} problem={problem!r} len={len(data)}")
    else:
        print("ok   stdout overflow drops the body")

    code, data, problem = mod.run_bounded(
        ["/usr/bin/python3", "-I", "-S", "-c", "import sys; sys.stdout.write('ok')"],
        timeout=5,
        max_bytes=10,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if problem or code != 0 or data != b"ok":
        failed += 1
        print(f"FAIL short read: code={code} problem={problem!r} data={data!r}")
    else:
        print("ok   short stdout")

    if mod.root_owned_executable("teams-for-linux") or mod.root_owned_executable("/tmp/teams-for-linux"):
        failed += 1
        print("FAIL untrusted path accepted")
    else:
        print("ok   relative and /tmp paths rejected")
    teams = "/usr/bin/teams-for-linux"
    if os.path.exists(teams) and not mod.root_owned_executable(teams):
        failed += 1
        print("FAIL installed teams-for-linux was not accepted")
    elif os.path.exists(teams):
        print("ok   installed teams-for-linux is a root-owned executable")
    zoom = "/usr/bin/zoom"
    if os.path.exists(zoom) and not mod.root_owned_executable(zoom):
        failed += 1
        print("FAIL installed zoom was not accepted")
    elif os.path.exists(zoom):
        print("ok   installed zoom is a root-owned executable")
    return failed


def test_manifest() -> int:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    qml = (ROOT / manifest["entryPoints"]["barWidget"]).read_text(encoding="utf-8")
    errs = []
    if manifest["id"] != "io.github.07dcolem.meeting-clipboard":
        errs.append("id")
    if manifest["kinds"] != ["bar-widget"]:
        errs.append("kinds")
    if f'moduleName: "{manifest["id"]}"' not in qml:
        errs.append("moduleName")
    if "textFormat: Text.PlainText" not in qml and "Text {" in qml:
        errs.append("text format")
    if "StdioCollector" in qml or "bash" in qml or "sh -c" in qml:
        errs.append("shell or collector")
    if errs:
        print(f"FAIL manifest: {errs}")
        return 1
    print("ok   manifest")
    return 0


def test_removed_from_panel() -> int:
    import tempfile

    failed = 0
    invite = "Meeting ID: 111 222 333 444 5\n"
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "clipboard-history.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([{"type": "text", "text": invite}], handle)
        if mod.removed_from_panel(invite.strip(), path=path, retry_s=0):
            failed += 1
            print("FAIL listed invite was treated as removed")
        else:
            print("ok   listed invite stays joinable")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump([{"type": "text", "text": "hello"}], handle)
        if not mod.removed_from_panel(invite.strip(), path=path, retry_s=0):
            failed += 1
            print("FAIL deleted invite was still joinable")
        else:
            print("ok   deleted invite is not joinable")
        os.remove(path)
        if mod.removed_from_panel(invite.strip(), path=path, retry_s=0):
            failed += 1
            print("FAIL missing panel blocked a join")
        else:
            print("ok   missing panel does not block")
        link = os.path.join(directory, "history-link")
        os.symlink("/etc/passwd", link)
        if mod.removed_from_panel(invite.strip(), path=link, retry_s=0):
            failed += 1
            print("FAIL symlink history blocked a join")
        else:
            print("ok   symlink history does not block")
    # A file passed on the command line is not the live clipboard.
    if mod.removed_from_panel(invite, path=os.devnull, retry_s=0):
        failed += 1
        print("FAIL unreadable history blocked a join")
    else:
        print("ok   unreadable history does not block")
    return failed


def test_cli_dry_run() -> int:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "teams-join-from-clipboard.py"), "--dry-run", "--file", str(ROOT / "fixtures" / "required-invite.txt"), "--no-notify"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or "meetup-join" not in result.stdout or "provider:   teams" not in result.stdout:
        print(f"FAIL dry-run rc={result.returncode} out={result.stdout!r} err={result.stderr!r}")
        return 1
    print("ok   required dry-run")
    return 0


def main() -> int:
    failed = test_parse()
    failed += test_launch_argv()
    failed += test_status_hides_meeting_material()
    failed += test_bounds()
    failed += test_manifest()
    failed += test_removed_from_panel()
    failed += test_cli_dry_run()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
