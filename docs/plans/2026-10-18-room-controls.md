# Persistent Voice Room Controls Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the approved Discord flow: hidden rooms, persistent controls, host-only invitations, chat access and automatic host succession with creator priority.

**Architecture:** Separate persisted room state and permission policy from Discord operations and UI. Keep the existing token file, deployment directory, health checks, entry channel and audience roles. Use one durable, pinned launcher in a bot-only text channel plus a panel in each voice room; personal menus can always be reopened. Live testing showed Discord rejects pinning voice-channel chat messages (API error 50019), so only the text-channel launcher is pinned.

**Tech Stack:** Python 3.12 deployment / Python 3.13 development, discord.py 2.7.1, unittest, Docker Compose, atomic JSON state.

---

## Accepted design

- Join `➕ Create VC` or use the launcher. Default: hidden; creator and invitees can view, connect, speak, use video and write/read chat.
- Modes: hidden, visible invite-only, public (existing server voice audience only). A mode change retains explicit invitations and blocks. Administrators bypass Discord channel restrictions.
- Store original creator separately from active host. After a 10-second departure grace, earliest-joined human still present becomes host. Rejoining resets a member's arrival order. Creator return restores ownership immediately. Creator keeps access and cannot be blocked by a temporary host.
- Delete empty rooms after a 10-second grace; allow 60 seconds to join rooms created outside voice. Rejoining the entry channel returns creators to their existing room.
- Current host alone can invite, revoke, block/unblock, change privacy/name/limit. Recheck authorization on submission as well as initial button clicks. Stale menus never grant former hosts access.
- Permanent generic `#voice-controls` launcher reveals no private room names. Manage opens a private dashboard; `/room` recovers it. Pin a durable ordinary dashboard in each room's text chat. Re-register components after restart and repair missing panels.
- Invite via multi-user picker; grant visibility, connection and chat together. Show a room link and an optional explicit notify-invitees choice; successful access does not depend on DM delivery. Revoke removes explicit invitation; block additionally prevents re-entry and removes visibility, except Discord administrators. Legacy `/invite` and `/kick` remain, with clear disconnect-vs-block semantics.
- Room chat writable by everyone with room access. Generic controls channel stays bot-only. User names are plain text without unsolicited mentions.

## Task 1: Preserve baseline; implement room-state and permission policy

Files: `room_state.py`, `tests/test_room_state.py`, existing deployment files.

1. Back up current uncommitted deployment baseline; develop in the user's requested checkout on `feat/room-controls`.
2. Write tests for hidden visibility, role precedence, invited chat/video access, blocks, host selection, creator return and atomic reload.
3. Run tests and confirm missing-behavior failures.
4. Implement minimal dataclass/store and permission builder; run tests.

## Task 2: Discord service and event lifecycle

Files: `room_service.py`, `tests/test_room_service.py`.

1. Test duplicate creation, initial hidden overwrites, join ordering, stale host authorization, restricted invite targets, handover, cleanup, limit/name validation, missing resources and legacy migration.
2. Implement per-room locks and cancellable delayed reconciliation. Save state after each successful change; handle Discord failures without announcing success.
3. Keep live rooms through restart, migrate legacy ownership without overwriting files, and recreate missing controls.
4. Verify with local async tests using Discord boundary mocks.

## Task 3: Persistent UI and commands

Files: `TempVCBot.py`, `tests/test_controls.py`.

1. Test stable persistent component IDs, host-only actions, multi-user invitation, modal reauthorization and launcher routing.
2. Implement launcher, dashboard, privacy/member selectors, name/limit modal and error feedback; remove DM reaction dependency.
3. Register views in setup_hook and expose setup/createvc/room/invite/kick commands. Setup manages only bot channels and existing entry configuration.
4. Run full tests, syntax checks and diff validation.

## Task 4: Package, deploy and verify

Files: `Dockerfile`, `.dockerignore`, `compose.yaml`, `README.md`.

1. Document behavior and operating commands; include modules in image and update local image tag.
2. Inspect current host/container/config/state, create target-specific backups, and build candidate image without starting a second bot gateway client.
3. Run isolated tests and migration checks in candidate image with temporary data and no token.
4. Replace only tempvcbot, preserve protected token and data, then verify gateway health, component registration, launcher and effective channel permissions.
5. Exercise available live interactions without moving/disconnecting other real members; report any multi-user/camera scenarios not live-tested.
6. Commit reviewed source on feature branch; no secret/runtime data. Record exact verification and rollback path.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q TempVCBot.py room_state.py room_service.py
git diff --check
```

Expected: all tests pass; no syntax or whitespace errors. Live deployment additionally requires healthy gateway, correct server/channel IDs and persistent launcher readback.
