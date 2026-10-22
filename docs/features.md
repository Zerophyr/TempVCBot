# Features — public voice rooms

**Deployed to DeutschDen after explicit approval. Runtime commit: `/opt/tempvcbot/SOURCE_COMMIT`.**

| Feature | Remaining behavior |
|---|---|
| Create a room | Public to the normal voice audience; unlimited participants by default. |
| Join and discovery | /rooms shows occupancy; Join / Notify me checks current availability. The dashboard has only Create a room, Manage my room and Help. |
| Chat and video | Members with room access can write in chat, speak and use webcam/video. |
| Host settings | Rename; optional limit of 5–99, or 0 for unlimited. |
| Staff priority | Owners/admins/mods can override settings without taking ownership; staff limits may be 1–99 or unlimited. |
| Manual moderation | Staff retain native Discord removal, move, mute, kick and ban rights according to their actual permissions and role hierarchy. |
| Availability alerts | Explicit one-time DM opt-in for a full room; cancellable, deduplicated, and restored after restart. No place is reserved or automatic move made. |
| Ownership transfer | After a 10-second grace when the host leaves, the earliest-joined remaining human becomes host. The original creator regains hosting on return. |
| Visible owner | Current owner mention and user ID in the room chat card; owner name and ID in native channel status, updated on handover and creator return. |
| Change accountability | Successful bot name/limit edits announce actor ID and before/after values in room chat; Discord audit-log reasons also identify the actor and current host. Staff overrides identify staff separately. |
| Permanent controls | Pinned dashboard and persistent room chat panels; personal menus can be reopened anytime. |
| One room per creator | Temporarily hosting another person's room does not consume that slot. Existing rooms are reused, respecting effective access and capacity. |
| Cleanup | Empty rooms close after 10 seconds; never-joined rooms have 60 seconds. Associated chat, temporary controls and notification opt-ins are cleaned up. |
| Recovery | Ownership, arrival order, panel IDs and notifications persist. One-time legacy migration converts active rooms to public; later restarts preserve manual staff settings. |

Commands: `/createvc`, `/room`, `/rooms`, `/notify channel:`,
`/unnotify channel:`, and staff setup via `/setup` (Manage Channels required).

Removed: private/invite-only modes, Create with friends, all bot invitation
features and `/invite`, plus room-owner kick/disconnect/block/unblock controls
and `/kick`. Native Discord staff moderation is independent of these removals.

Staff identification uses existing server ownership or Administrator, Manage
Server, Manage Channels, Move/Mute/Deafen Members, Kick/Ban Members, or Timeout
Members permissions. A role name is not an authorization check. Being a room
host grants no Discord moderation permission.

Staff use **/rooms → choose room → Manage room**, or `/room` while inside
that room. All settings actions recheck authority. A moderator's Connect denial
is respected, and host handover cannot clear manual moderation overwrites.

Discord's native “channel full” dialog cannot be modified. The availability
option is provided through the bot's room checker or `/notify`. DMs must be
enabled; delivery is best effort and a notification does not reserve a slot.

All 134 automated tests pass locally and in the Linux ARM64 image. Live checks
verified the dashboard, command sync, a disposable public room, chat/video
permissions, ownership indicators and attributed name/limit changes. The test
room was removed; no users were moved or DMs sent. Notification delivery and
multi-user voice handover retain automated coverage; they were not exercised
with real participants during deployment.
