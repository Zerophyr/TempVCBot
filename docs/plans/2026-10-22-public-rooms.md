# Public rooms and availability alerts

Status: DEPLOYED on 22 October 2026 after the user's explicit deployment and
push instruction. Runtime source is recorded in `/opt/tempvcbot/SOURCE_COMMIT`. The original implementation was
held locally until that approval; the requirements below record that workflow.

## Requested behavior

- All new rooms are public to the configured normal voice audience, with an
  explicit default limit of 0 (unlimited). Remove private/invite-only modes,
  per-room invitation access, disconnect, block and unblock features.
- The current host can rename and set a limit: 0 or 5–99. Keep ownership
  transfer, creator return, chat/video access, lifecycle and control cleanup.
- Anybody with normal room access can check availability and opt into a
  one-time DM when a full room has space. Cancellation, restart persistence,
  deduplication and deletion cleanup are required. A notification is not a
  reservation, and the bot does not move a waiting user into the room.

## Discord constraint and chosen interaction

Discord's documented VOICE_STATE_UPDATE reports changes of voice state; it
does not provide a supported failed-join hook. The bot cannot modify the
native client “channel full” dialog. Provide /rooms for browsing,
Join / Notify me on room panels, and /notify for a full managed room. Each
check uses current occupancy. Notification opt-in is explicit and user-bound.

## Compatibility and eventual rollout

Read v2 rooms into v3 public state without writing during loading. Preserve
creator, host, ordering, room/panel IDs and timestamps; discard removed access
rules. On a future authorized rollout, restore public channel overwrites and
raise legacy limits 1–4 to 5, retaining unlimited and valid limits. Preserve
an exact backup of production data before any migration. Existing archived
entry chat stays hidden and is never treated as an active private room.

Replace old dashboard buttons and remove old slash commands during that
future rollout. Keep new changes on a local branch until the user approves
deployment. Test all Discord operations with mocks; do not use the live token.

## Validation

Run baseline, then regressions for public permissions, explicitly unlimited
creation, limits, removed actions, migration, host handover, cleanup, opt-in
alerts on departures/limit changes/reconnect, cancellations, failed DMs,
access revocation, simultaneous subscriptions and stale UI interactions.
Review source/config diffs and update member instructions for this unreleased
version. No Docker/Compose startup or remote application command sync.

## Staff-authority refinement

Server owners and members with native server/voice moderation permissions can
override host settings without taking ownership. Regular hosts accept limits
0 or 5–99; staff accept 0 or 1–99. Native Discord removal/move/mute/kick/ban
authority remains with existing server permissions and role hierarchy.

Public access is applied once per room. Persist the completed migration flag,
retain manual moderation overwrites and limits on subsequent restarts, and
stop rewriting channel permissions during host handover. Check effective
Connect/View access before automatically moving a creator into any room.
The existing category and archived history remain unchanged.

## Predeployment review corrections and ownership visibility

Preserve temporary migration provenance until a room's public policy is
successfully applied. Remove only the known old bot values; retain unrelated
native overwrites and deviations, including moderator Connect/Speak denials.
Identical overlapping values cannot be attributed retrospectively and need
review before the eventual rollout. Unknown overwrites are kept conservatively.

Check the live cached capacity before consuming every subscriber's opt-in;
do not use detached REST edit results for a delivery batch. Buffer and replay
startup voice transitions, then arm lifecycle checks before readiness.

Show the current owner and stable ID in the room chat card and native voice
channel status. Staff retain a status override. Announce handovers and
successful bot name/limit changes with actor ID and before/after values,
without pings. Include actor and host IDs in Discord audit-log reasons.
Owner status and announcements are best effort if staff restrict the bot;
room chat remains temporary and no separate permanent moderation log is added.

The dashboard room-finder button was removed at the user's request. Its three
buttons are Create a room, Manage my room and Help; /rooms remains available.
