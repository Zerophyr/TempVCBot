# TempVCBot — public rooms preview

**Unreleased local branch. Do not deploy until the user explicitly approves.**

This branch replaces private rooms and room moderation with public voice rooms,
simple host settings and opted-in availability notifications. The existing
Hetzner deployment and live Discord dashboard have not been changed.

Based on [RitvikRRy/TempVCBot](https://github.com/RitvikRRy/TempVCBot), upstream
commit `bb25cd267808fd0aac112a631c61945d1e4aa4d1`.

## Member workflow in this preview

1. Open **#voice-dashboard** and select **Create a room**. Every new room is
   public to members with normal voice access and has **no user limit**.
2. If already in voice, the bot moves you into your new room. Otherwise open
   the room link and join within **60 seconds** before an unused room expires.
3. Use **Manage my room**, `/room`, or the room chat panel for settings. Only
   the current host or server staff can rename a room or change its size.
4. Everyone with normal voice access can read and write in the room's chat,
   join voice and use webcam/video. No invitation is needed. The bot has no
   private/invite-only modes, Create with friends, invitation-access controls,
   kick, disconnect, block, unblock or ban commands.

For regular hosts, the optional participant limit accepts **0 (unlimited)** or **5–99**. Setting
a lower limit never disconnects people already inside. Discord members with
privileged permissions may bypass a voice channel's native limit.

## Server staff always retain moderation authority

Server owners, admins and moderators can override room settings without taking
ownership: **Find a room → choose room → Manage room**, or `/room` while in it.
Staff may set **1–99** participants or **0 (unlimited)**. Ordinary hosts cannot
use the staff exemption. Permission checks run again on each action, including
modal submission, so a demoted staff member cannot reuse old controls.

Staff authority is recognized by server ownership or existing native Discord
permissions: Administrator, Manage Server, Manage Channels, Move/Mute/Deafen
Members, Kick Members, Ban Members, or Timeout Members. A role's name alone
does not grant authority. The bot grants no new move/mute/kick/ban permissions.

Admins/mods can remove people manually using Discord according to their native
permissions and role hierarchy. Room access policy does not deny those powers;
staff roles also retain access to newly created public rooms. Handover does
not reset permission overwrites. After the one-time public-room migration,
restarts preserve staff's manual room limits and moderation overwrites.
The bot checks effective access before moving a creator into a room, so it
does not bypass a moderator's explicit Connect denial.
Native moderation still follows [Discord's permission hierarchy](https://docs.discord.com/developers/topics/permissions).

## Full rooms and one-time notifications

Use **Find a room** in the dashboard or `/rooms`. The member picker lists
current occupancy and availability, with pages of up to 25 rooms. You can
also select **Join / Notify me** on a room's chat panel.

- If there is space, select **Join voice room** to open the room in Discord.
- If full, select **Notify me when a place opens**. This explicitly opts you
  into a **single DM** for that room. Enable DMs from server members.
- Reopen the room card and select **Cancel my notification**, or use
  `/unnotify channel: <room>`, to cancel. Only your own opt-in is affected.
- `/notify channel: <room>` is an alternative way to subscribe to a full
  managed room. An already available room shows a join link instead.

Discord does not expose a supported failed-join event or let bots alter the
native “channel full” dialog. A failed native join therefore cannot trigger a
bot prompt automatically. Use the bot's room checker when a room is full.
If capacity changes after a check, select **Check again**.
This design follows Discord's documented
[voice-state events](https://docs.discord.com/developers/events/gateway-events#voice-state-update).

Notifications are not reservations or a join queue: current subscribers are
notified once when space is available, and places may fill again. The bot
never moves subscribers automatically. Repeat subscriptions are deduplicated.
Opt-ins survive restart and host handover; joining the target room or deleting
it clears the relevant opt-ins. Departures, limit increases/removal, channel
updates and restart recovery all check availability.

Users who leave the server or lose room access are skipped. Closed DMs or an
unsuccessful delivery consume that one-shot notification without repeated
attempts. Delivery is best effort: a process crash after claiming a notification
can lose that attempt. Another user's failed DM does not stop other deliveries.

## Ownership and cleanup

- A creator can have one room per server. Temporarily hosting another room
  does not consume this slot.
- After a **10-second grace** when the host leaves, the earliest-joined human
  still present becomes host. Rejoining gives a new arrival order; mute,
  deafen and video changes do not change it.
- The original creator regains hosting when they return. Room settings and
  notification subscriptions are preserved.
- Rooms close **10 seconds** after everyone leaves. A never-joined room has
  an initial **60-second** join window. Room chat disappears with the channel.
- The permanent dashboard and room panels have no ten-minute timeout.
  Personal menus can be reopened anytime. They reuse replies and are cleaned
  up on room deletion, replacement, graceful shutdown, or before their
  interaction token expires. Old replies from a previous process can require
  manual dismissal; interaction tokens are never persisted.

## Commands in this preview

| Command | Purpose |
|---|---|
| `/createvc` | Create a public unlimited room or return to your room |
| `/room` | Open settings for the room you host |
| `/rooms` | Find public rooms and check availability |
| `/notify channel:` | Opt into one DM when a full managed room has space |
| `/unnotify channel:` | Cancel your own availability notification |
| `/setup` | Admin: configure the managed category and dashboard |

The old `/invite` and `/kick` commands will be removed by command sync **only
when this version is eventually started after deployment approval**.

## Local development and tests

Python 3.12+ and discord.py 2.7.1. All tests use temporary state and mocked
Discord boundaries; no token, gateway login, live DM or server access is needed.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q TempVCBot.py room_state.py room_service.py control_messages.py
git diff --check
```

Never start `TempVCBot.py` or Compose against production credentials as a test.
Constructing `RoomBot` does not connect; the script entry point does.

Modules: `room_state.py` stores ownership, permission policy and opt-ins;
`room_service.py` applies Discord actions and lifecycle; `TempVCBot.py`
provides persistent views, menus and commands; `control_messages.py` manages
in-memory private-response cleanup.

## Future rollout and migration — not performed

The existing standalone deployment is `/opt/tempvcbot` on Hetzner, outside
Coolify. Production previously used `tempvcbot:voice-dashboard-v3`; the new
Compose image name is `tempvcbot:public-rooms-v4`, so the preview cannot replace
the old image tag merely by building it. No image has been built on the server
or container restarted for this change.

After explicit approval, inspect the then-current container, active rooms and
configuration, and back up source, Compose and **all production data** before
deployment. Keep `/opt/tempvcbot/secrets` untouched. The token is read from
`DISCORD_TOKEN_FILE`; never put it in source, logs or an image.

- `config.json`: managed category, dashboard and pinned launcher IDs. Existing
  dashboard IDs/history are reused; dashboard revision becomes 4.
- `rooms.json`: schema 3 stores creator, host, arrival order, panel, timestamps,
  waiting member IDs and a one-time public-policy migration flag. Reading a schema-2 file converts it in memory without
  writing during load. Saving writes schema 3 and omits removed privacy,
  invitation and block fields.
- At the first future startup, active old rooms receive public overwrites, removing old
  per-member restrictions. Legacy limits 1–4 become 5; 0 and 5–99 are preserved.
  Channel IDs, host/creator, panel IDs, arrival order and timestamps are retained.
  The migration flag is then persisted. Subsequent restarts and host handovers
  retain native moderation overwrites and staff-set limits rather than resetting them.
- `channels.json`: legacy ownership is imported only if rooms.json is absent.
- `health.json`: initialization/gateway readiness, room count, private replies
  and notification opt-ins. Failed initialization cannot report ready.

The retired **➕ Create VC** entry is not recreated. Archived entry chat stays
hidden to preserve history; it is not an active room or a private-room feature.

Rollback to the old private-room version requires its original schema-2 data
backup. The new schema intentionally cannot restore removed privacy or block
rules; never downgrade against schema-3 state without a reviewed recovery plan.

See [implementation plan](docs/plans/2026-10-22-public-rooms.md) and
[member guide preview](docs/public-rooms-user-guide.md).
