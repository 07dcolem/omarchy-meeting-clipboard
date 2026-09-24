# omarchy-meeting-clipboard

Extract a Microsoft Teams meeting ID, passcode, and join URL from clipboard / email / calendar text, then launch [teams-for-linux](https://github.com/IsmaelMartinez/teams-for-linux).

Standalone script first. Omarchy plugin wrap is next — see [HANDOFF.md](HANDOFF.md).

## Script

```bash
python3 teams-join-from-clipboard.py              # clipboard
python3 teams-join-from-clipboard.py --dry-run
python3 teams-join-from-clipboard.py --file fixtures/required-invite.txt --dry-run
echo "$INVITE" | python3 teams-join-from-clipboard.py --stdin --json
```

Clipboard backends: `wl-paste`, then `xclip`, then `xsel`.

Launch:

```bash
teams-for-linux --url '<join-url>'
```

Override the client with `TEAMS_FOR_LINUX_CMD`.

## Required invite (must keep working)

```
Microsoft Teams Need help?<https://aka.ms/JoinTeamsMeeting?omkt=en-US>
Join the meeting now<https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZGE4ZmY1MGYtMzAxMS00OGE2LThkMWYtYWI1OTUxNGUyMDgy%40thread.v2/0?context=%7b%22Tid%22%3a%2289522aa1-1984-4d7a-96ba-2ac240623b6b%22%2c%22Oid%22%3a%22d79442ba-06b6-42e7-ac83-78e61f2b0fd7%22%7d>
Meeting ID: 412 829 079 975 3
Passcode: Sd94S45U
```

## Recorded output

`python3 test_parse.py`:

```
ok   required-outlook-angle-brackets
ok   id-and-pass-only
ok   short-meet-url-with-p
ok   meetup-join-bare
ok   german-labels
ok   dash-separated-id
```

`python3 teams-join-from-clipboard.py --dry-run --file fixtures/required-invite.txt`:

```
source:     meetup-join-url
meeting_id: 4128290799753
passcode:   Sd94S45U
join_url:   https://teams.microsoft.com/l/meetup-join/19%3ameeting_ZGE4ZmY1MGYtMzAxMS00OGE2LThkMWYtYWI1OTUxNGUyMDgy%40thread.v2/0?context=%7b%22Tid%22%3a%2289522aa1-1984-4d7a-96ba-2ac240623b6b%22%2c%22Oid%22%3a%22d79442ba-06b6-42e7-ac83-78e61f2b0fd7%22%7d
```

Same files under `output/`.
