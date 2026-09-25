# Meeting clipboard

<p>
  <a href="https://github.com/07dcolem/omarchy-meeting-clipboard/actions/workflows/test.yml"><img alt="Test status" height="20" src="https://github.com/07dcolem/omarchy-meeting-clipboard/actions/workflows/test.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" height="20" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://github.com/tcballard/omarchy-badges"><img alt="Built for Omarchy: Plugin" height="20" src="https://raw.githubusercontent.com/tcballard/omarchy-badges/75975e5b5bf75e7ede3764bcd2950046f7abfe2c/badges/v1/omarchy-plugin.svg"></a>
</p>

Left-click reads the clipboard once and opens the meeting in [teams-for-linux](https://github.com/IsmaelMartinez/teams-for-linux), or in Zoom when the clipboard contains a Zoom join link. The icon on the [Omarchy](https://omarchy.org/) bar is the same calendar mark the clock uses, in a horizontal or vertical bar.

Right-click and middle-click do nothing. The plugin runs inside `omarchy-shell`, unsandboxed, like every other shell plugin. It does not read the clipboard until that click.

## Requirements

Omarchy with the Quattro shell. These are already part of a normal Omarchy install: Python 3 at `/usr/bin/python3`, `wl-paste` from `wl-clipboard`, and `notify-send`. `xclip` or `xsel` are used only when `wl-paste` is not installed.

Install the meeting app you want to open. This plugin does not install packages. teams-for-linux and Zoom are separate applications under their own licenses. This repository does not bundle them.

| App | Accepted locations |
|---|---|
| teams-for-linux | `/usr/bin/teams-for-linux`, `/usr/local/bin/teams-for-linux`, `/opt/teams-for-linux/teams-for-linux`, or Flatpak `com.github.IsmaelMartinez.teams_for_linux` |
| Zoom | `/usr/bin/zoom`, `/opt/zoom/ZoomLauncher`, or Flatpak `us.zoom.Zoom` |

## Install

```bash
omarchy plugin add https://github.com/07dcolem/omarchy-meeting-clipboard.git --enable
```

The icon lands on the right of the bar. Move it with:

```bash
omarchy bar move io.github.07dcolem.meeting-clipboard --section right
```

## Update

```bash
omarchy plugin update io.github.07dcolem.meeting-clipboard
```

That updates a checkout cloned by `omarchy plugin add`. It fetches the current upstream `HEAD`, fast-forwards when validation passes, and asks the shell to rescan plugins. A copy placed by hand, with no `.git` directory, is left as it is. The command updates this plugin only.

## Remove

```bash
omarchy plugin remove io.github.07dcolem.meeting-clipboard
```

That unloads the widget. When the checkout was cloned with `omarchy plugin add`, the command deletes `~/.config/omarchy/plugins/io.github.07dcolem.meeting-clipboard`. A copy placed there by hand, with no `.git` directory, is moved to a hidden backup in `~/.config/omarchy/plugins/` instead of being deleted.

The plugin writes no state file, cache, credential, or package, and it adds no background service, so none of those remain. teams-for-linux, Zoom, and Flatpak stay installed, including whatever configuration those applications already keep. The plugin does not edit Hyprland config, `shell.json`, or any other plugin. Omarchy's own enable and remove commands are what place and clear the bar entry.

## Use

Copy a meeting invite, then left-click the icon. A notification names the result. It does not include the meeting id, the passcode, or the link.

| Clipboard | Result |
|---|---|
| A join link on `teams.microsoft.com`, `teams.live.com`, or `teams.cloud.microsoft` | Opens that link in teams-for-linux. `https://aka.ms/JoinTeamsMeeting` is ignored. |
| A labeled meeting id of 12 to 16 digits, or a grouped number of that length | Opens `https://teams.microsoft.com/meet/<id>` in teams-for-linux. A labeled passcode is not added to that link. |
| `https://….zoom.us/j/<id>`, `/wc/join/<id>`, `/my/<name>`, or `zoommtg://zoom.us/join?confno=<id>` | Opens that meeting in Zoom. |
| A labeled meeting id of 9 to 11 digits and no join link | Nothing opens. |
| A Teams link and a Zoom link, two different meetings, or a Zoom link beside a different 12 to 16 digit id | Nothing opens. |
| An invite you deleted from the clipboard panel | Nothing opens. |

Zoom publishes meeting ids of 9, 10, or 11 digits. Older Teams ids use that same length, so a bare number there is not enough to choose an app. Twelve digits and longer are Teams. A Zoom invite still joins when it includes a Zoom link, which is the usual invite.

Deleting an entry in the clipboard panel takes it off that list immediately. The previous copy can still be pasted by other programs until something else is copied. The click follows the panel: a deleted invite does not open Teams or Zoom.

When one Teams invite contains both the long `meetup-join` link and a short `/meet/` link, the long link is used. A passcode carried as Teams `p` or Zoom `pwd` is removed before the meeting app starts. The human passcode printed in a Zoom invite is not copied onto the link. The meeting app asks for the passcode when the meeting requires one. Other Teams parameters, including `context`, stay on the link.

The notification text is one of:

- Opening Teams. Enter the passcode there if it asks.
- Opening Zoom. Enter the passcode there if it asks.
- Copy a Teams or Zoom invite, then click the calendar icon.
- No Teams or Zoom join link was found.
- That invite was removed from the clipboard.
- That text could be Teams or Zoom. Copy a full join link.
- The clipboard is too large to scan.
- Could not read the clipboard.
- teams-for-linux is not installed.
- Zoom is not installed.
- The meeting app did not start.
- Could not scan the clipboard.

## What the click runs

The icon starts `/usr/bin/python3 -I -S` on `teams-join-from-clipboard.py --plugin`. Before anything is opened, that process checks the clipboard panel's list. An invite deleted from the panel is not opened, including when other programs can still paste the old copy.

That process reads clipboard text, at most 64 KiB, with a three-second deadline. A larger clipboard is refused and not parsed. It tries `wl-paste` for `text/plain`, then one untyped `wl-paste` read when that returns nothing. Images and other non-text are not parsed. The notification then says the clipboard could not be read. The clipboard text is not passed as a program argument.

The join link is then an argument to the meeting app, which is how teams-for-linux (`--url`) and Zoom (the link itself) accept a meeting. Teams `p` and Zoom `pwd` are removed before that command is started, so the passcode is not on the client's command line while the meeting stays open. That argument is not written to a file and not shown in the notification. The app is a fixed path from the table above, or `flatpak run` of the matching Flatpak id. It is not chosen from `PATH` or from an environment variable.

The plugin makes no network request and writes no file. teams-for-linux then loads the Teams host in the link. Zoom loads the `zoom.us` host in the link. Those requests belong to the meeting app.

If the helper is still running after 12 seconds, the widget stops it and notifies: The join helper did not finish. The meeting app, once started, is left running.

## Terminal

The same parser runs without the bar:

```bash
python3 teams-join-from-clipboard.py --dry-run --file fixtures/required-invite.txt
python3 test_parse.py
```

`--dry-run`, `--print-url`, and `--json` print the join link on the terminal. That link has no Teams `p` or Zoom `pwd` parameter. `--dry-run` and `--json` also print a passcode when one was written in the invite. The bar does not use those flags. It runs `--plugin`, which prints only a short status line with the app name and the reason.

`fixtures/required-invite.txt` is the Outlook-shaped invite the tests have to keep opening through its `meetup-join` link.

## Compatibility

Parser tests were run with Python 3.14 on this machine, and the GitHub workflow runs them on Python 3.12. `omarchy plugin validate` passed on Omarchy 4.0.4, and the widget was enabled on that system. A click in the live bar was not part of the automated checks.

## License

[MIT](LICENSE). Copyright 2026 Dylan Coleman.
