# Handoff for grok-build TUI

Pick up here. Goal: wrap `teams-join-from-clipboard.py` as an Omarchy plugin that reads clipboard/invite text, extracts Teams meeting ID + passcode (or join URL), and launches `teams-for-linux`.

## What already works

Standalone Python 3 script, no third-party deps.

```bash
python3 test_parse.py
python3 teams-join-from-clipboard.py --dry-run --file fixtures/required-invite.txt
```

Recorded output is in `output/`.

Required invite format that MUST keep working is `fixtures/required-invite.txt`.

Join URL preference already implemented:

1. Long `https://teams.microsoft.com/l/meetup-join/...` URL if present (best for teams-for-linux).
2. Short `https://teams.microsoft.com/meet/<id>?p=<pass>` URL.
3. Construct `https://teams.microsoft.com/meet/<digits>?p=<passcode>` from labeled ID + passcode.

Launch shape:

```bash
teams-for-linux --url '<join-url>'
```

Client discovery: `TEAMS_FOR_LINUX_CMD`, then PATH (`teams-for-linux`), then Flatpak `com.github.IsmaelMartinez.teams_for_linux`, then common prefixes.

Clipboard: `wl-paste` then `xclip` then `xsel`.

Machine-readable mode for a plugin UI: `--json` and `--print-url`.

## Next work (Omarchy plugin)

Omarchy plugins live in `~/.config/omarchy/plugins/<id>/` with a root `manifest.json` (`schemaVersion: 1`) and QML entry points. Kinds: `bar-widget`, `panel`, `overlay`, `menu`, `service`, `bar`.

Suggested first cut:

- Plugin id: something like `07dcolem.meeting-clipboard` (do not use the reserved `omarchy.` prefix).
- Kind: `menu` or `overlay` summoned from a keybind, plus optional `service`.
- On summon: run the script with `--json` against clipboard.
- Show meeting ID, passcode, and join URL.
- Confirm -> exec `teams-for-linux --url ...` (or `TEAMS_FOR_LINUX_CMD`).
- Keep the Python script as the parser so tests stay green.

Docs to use:

- https://omarchy.org/manual/shell-plugins/
- teams-for-linux URL handling: `urlHandling.meetupJoinRegEx` already matches both `/l/meetup-join/` and `/meet/`.

## Constraints

- Do not break the required Outlook angle-bracket invite.
- Do not treat `https://aka.ms/JoinTeamsMeeting` as the join target.
- Meeting IDs are 9-16 digits, often spaced (`412 829 079 975 3` = `4128290799753`).
- Human passcodes (e.g. `Sd94S45U`) are not always the same as hashed `p=` values on short URLs; if a meetup-join URL exists, prefer that URL.
