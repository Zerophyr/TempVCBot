# Voice Dashboard Refinements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the legacy entry channel with a Voice Dashboard, allow guest selection before creation, and clean up temporary personal controls.

**Architecture:** Keep the permanent ordinary launcher and room panels. Track private response deletion callbacks only in memory, reuse private component messages, remove responses on room deletion, and expire them before Discord's 15-minute token deadline. Persist room membership as before; selected initial guests are included in channel overwrites at creation.

**Tech Stack:** Python, discord.py 2.7.1, unittest, Docker Compose, atomic JSON.

## Changes and verification

1. Write failing tests for room-response cleanup, replacement without deleting the edited message, timeout cleanup, creation with guests, validation before channel creation, and legacy entry retirement without recreating it.
2. Add `control_messages.py` with bounded response lifetimes and cleanup on room removal/shutdown. Update `respond` to edit private component responses and keep public launcher/room panels untouched. Update action callbacks with room associations.
3. Add a persistent Create with friends launcher action and a member picker bound to the creating user. Validate every guest as a non-bot server member and grant initial visibility/voice/chat access atomically during room creation. Keep quick solo creation.
4. Rename the existing channel to `voice-dashboard` and refresh the existing pinned launcher. Remove legacy join-to-create behavior. Retire only the configured, matching legacy entry channel: delete only when empty with no chat history, otherwise preserve it as a hidden archive; defer while occupied.
5. Stop persistent room views and clean tracked replies when a room is deleted, including external channel deletion. Never retain interaction tokens in files. Old private replies from older processes cannot be retroactively removed with the bot token.
6. Run the complete suite, syntax/diff checks and a token-pattern check. Inspect live state; back up exact deployment files/data; build and test the image without production credentials; update only tempvcbot.
7. Read back dashboard name/buttons, legacy entry state, hidden member access and health. Exercise available live controls without inviting or disconnecting unrelated real members. Commit/push to the existing feature branch and record limitations.

## Commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q TempVCBot.py room_state.py room_service.py control_messages.py
git diff --check
```

Expected: all tests pass; permanent controls survive restart, selected guests can see/join/chat, other audience members cannot see hidden rooms, and closing rooms removes tracked controls without deleting the launcher.
