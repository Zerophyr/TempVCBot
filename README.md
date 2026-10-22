# TempVCBot — public voice rooms

**Deployed to DeutschDen on 22 October 2026 after explicit rollout approval.**

This version replaces private rooms and room moderation with public voice rooms,
simple host settings, visible ownership and opted-in availability notifications.
The standalone Hetzner deployment uses `tempvcbot:public-rooms-v4`, with runtime
source commit `479f2127ff286000598ffaf3a1329a516b7b7bec`.

Based on [RitvikRRy/TempVCBot](https://github.com/RitvikRRy/TempVCBot), upstream
commit `bb25cd267808fd0aac112a631c61945d1e4aa4d1`.

## Member workflow

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

## Visible ownership and responsibility for changes

The room chat control card prominently shows **Current room owner**, their
mention and numeric user ID. A native voice-channel status also shows the
owner's name and ID independently of chat scrolling. Both update on handover
and creator return. Regular members cannot edit this status through native
channel controls; server staff can override it.

Successful name and participant-limit changes through the bot publish a chat
announcement with the acting member's mention and ID, the old and new values,
and the current owner. Staff overrides identify the staff member separately
from the owner. Ownership changes announce both the previous and new owner.
These messages do not ping members or `@everyone`.

Bot name/limit edits also include the acting member ID and current host ID in
the Discord audit-log reason. Native staff edits remain attributed by Discord.
Room chat and these announcements disappear when the room is deleted; they
are not a separate permanent moderation log.

The owner status uses the bot's **Set Voice Channel Status** and **Manage
Channels** permissions. New rooms and migrated rooms grant the status
permission locally to the bot, with a staff override. A moderator's explicit
bot restriction is respected. If updating the status or sending an
announcement fails, the bot logs the failure; the successful room edit is
not undone. The owner card remains the authoritative bot-owned indicator.

See Discord's [voice-channel status endpoint](https://docs.discord.com/developers/resources/channel#set-voice-channel-status).

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

## Commands

| Command | Purpose |
|---|---|
| `/createvc` | Create a public unlimited room or return to your room |
| `/room` | Open settings for the room you host |
| `/rooms` | Find public rooms and check availability |
| `/notify channel:` | Opt into one DM when a full managed room has space |
| `/unnotify channel:` | Cancel your own availability notification |
| `/setup` | Admin: configure the managed category and dashboard |

The old `/invite` and `/kick` commands were removed by command sync during
the public-room deployment.

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

## Deployment, migration and recovery

The standalone deployment is `/opt/tempvcbot` on Hetzner, outside Coolify.
Production uses `tempvcbot:public-rooms-v4`; the former
`tempvcbot:voice-dashboard-v3` image is retained for recovery. Before cutover,
the ARM64 image passed all 134 offline tests on the server. The live update
retained the existing dashboard and launcher IDs, with six current commands.

Before future updates, inspect the then-current container, active rooms and
configuration, and back up source, Compose and **all production data** before
deployment. Keep `/opt/tempvcbot/secrets` untouched. The token is read from
`DISCORD_TOKEN_FILE`; never put it in source, logs or an image.

- `config.json`: managed category, dashboard and pinned launcher IDs. Existing
  dashboard IDs/history are reused; dashboard revision becomes 4.
- `rooms.json`: schema 3 stores creator, host, arrival order, panel, timestamps,
  waiting member IDs and a one-time public-policy migration flag. Pending
  migration also retains temporary old-policy provenance across saves/restarts;
  this is discarded after successful migration. Reading a schema-2 file converts it in memory without
  writing during load. Saving writes schema 3 and omits removed privacy,
  invitation and block fields.
- On their first restore with this version, active old rooms receive public overwrites,
  removing the known old bot privacy, invitation and host-block rules.
  Unrelated moderator overwrites and values differing from that known baseline
  are preserved. A manual value identical to an old bot rule cannot be
  distinguished retrospectively; review such overlaps before rollout. If old
  provenance is unavailable, existing unknown overwrites are retained
  conservatively and may need an administrator's review.
  Legacy limits 1–4 become 5; 0 and 5–99 are preserved.
  Channel IDs, host/creator, panel IDs, arrival order and timestamps are retained.
  The migration flag is then persisted. Subsequent restarts and host handovers
  retain native moderation overwrites and staff-set limits rather than resetting them.
- `channels.json`: legacy ownership is imported only if rooms.json is absent.
- `health.json`: initialization/gateway readiness, room count, private replies
  and notification opt-ins. Failed initialization cannot report ready.

Voice transitions during initialization are buffered and their arrival order
is replayed before readiness. Lifecycle checks are armed for restored rooms;
creator return is reconciled immediately. Availability is rechecked from the
live gateway cache before each notification is consumed, including after
startup. Detached REST results are used for fresh control cards, not as a
substitute for current capacity while sending a batch of DMs.

The retired **➕ Create VC** entry is not recreated. Archived entry chat stays
hidden to preserve history; it is not an active room or a private-room feature.

The deployment backup is
`/opt/tempvcbot/backups/before-public-rooms-20261022T205822Z`. It contains the old
source, Compose, image ID and a consistent pre-migration data archive. Secrets
remain in their original mount. There were no active rooms at cutover; an
empty schema-2 file remains readable and is persisted as schema 3 on the next
room mutation.

Live REST checks created a disposable public unlimited room, verified audience
chat/video access and the owner indicator, renamed it, changed its limit to
five and back to unlimited, and verified three attributed chat announcements.
The verification room was deleted. No users were moved or DMs sent.

Rollback to the old private-room version requires its original schema-2 data
backup. The new schema intentionally cannot restore removed privacy or block
rules; never downgrade against schema-3 state without a reviewed recovery plan.

See [implementation plan](docs/plans/2026-10-22-public-rooms.md) and
[member guide](docs/public-rooms-user-guide.md).
