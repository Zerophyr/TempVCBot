# Public rooms and availability alerts — local implementation only

Status: IMPLEMENTED LOCALLY, NOT DEPLOYED. No server, live Discord configuration, gateway client,
remote push, or production data changes are authorized in this task.

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
native client “channel full” dialog. Provide Find a room on the dashboard,
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
