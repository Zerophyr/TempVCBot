# Remaining features — public rooms preview

**Implemented locally; not pushed or deployed. Awaiting explicit rollout approval.**

| Feature | Remaining behavior |
|---|---|
| Create a room | Public to the normal voice audience; unlimited participants by default. |
| Join and discovery | Find a room shows occupancy; Join / Notify me checks current availability. |
| Chat and video | Members with room access can write in chat, speak and use webcam/video. |
| Host settings | Rename; optional limit of 5–99, or 0 for unlimited. |
| Staff priority | Owners/admins/mods can override settings without taking ownership; staff limits may be 1–99 or unlimited. |
| Manual moderation | Staff retain native Discord removal, move, mute, kick and ban rights according to their actual permissions and role hierarchy. |
| Availability alerts | Explicit one-time DM opt-in for a full room; cancellable, deduplicated, and restored after restart. No place is reserved or automatic move made. |
| Ownership transfer | After a 10-second grace when the host leaves, the earliest-joined remaining human becomes host. The original creator regains hosting on return. |
| Permanent controls | Pinned dashboard and room chat panels; personal menus can be reopened anytime. |
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

Staff use **Find a room → choose room → Manage room**, or `/room` while inside
that room. All settings actions recheck authority. A moderator's Connect denial
is respected, and host handover cannot clear manual moderation overwrites.

Discord's native “channel full” dialog cannot be modified. The availability
option is provided through the bot's room checker or `/notify`. DMs must be
enabled; delivery is best effort and a notification does not reserve a slot.

No live server checks, command sync, Discord moderation actions, DMs or
deployment have been performed for this preview. Runtime behavior has been
verified with automated tests using mocked Discord operations.
