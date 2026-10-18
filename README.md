# TempVCBot — DeutschDen fork

Create a hidden voice room in one click, invite friends, and manage it using Discord buttons. Based on [RitvikRRy/TempVCBot](https://github.com/RitvikRRy/TempVCBot), upstream commit `bb25cd267808fd0aac112a631c61945d1e4aa4d1`.

## For members

1. Join **➕ Create VC** under **Voice Channels**, or click **Create a room** in **#voice-controls**.
2. Your room starts **hidden**. If you are already in voice, the bot moves you into it. Otherwise open the provided room link and join within 60 seconds.
3. Open the room's text chat for its management panel. You can also use **Manage my room** in #voice-controls or `/room` at any time. The launcher is pinned in #voice-controls; Discord does not support pinning messages in voice-channel chat.

Everyone with room access can read and write in its chat, speak and use webcam/video. Discord server administrators retain access to restricted rooms.

### Controls (current host only)

- **Invite friends:** search for and select up to ten server members, then **Invite selected**. This grants visibility, voice and chat access together. Share the room link, or explicitly choose **Invite + notify by DM**. Failed DM delivery does not undo an invitation.
- **Privacy:** **Hidden** means only you and invitees can see/join; **Visible · invite-only** lets the normal voice audience see/chat but requires an invitation to join voice; **Public** lets that audience join freely. Existing participants retain access when a room is locked or hidden.
- **Manage people:** **Disconnect** removes someone from voice but permits rejoining; **Block** removes visibility/access and disconnects them; **Revoke invitation** removes explicit access (public access still applies); **Unblock** removes the block, after which hidden rooms require another invitation. The creator, current host and server administrators are protected from room-level removal through this panel.
- **Rename:** a single-line name, 1–100 characters.
- **User limit:** 0–99; 0 means unlimited. Discord privileged users can bypass channel limits.

The permanent launcher and ordinary room panels have no ten-minute timeout and are restored after bot restart. Personal menus are short-lived; reopen Manage my room or `/room` whenever needed. There is no dependency on enabling direct messages.

### Ownership and cleanup

The **creator** remains associated with their room. The **host** is the person currently allowed to manage it.

- When the host leaves, after a 10-second grace the earliest-joined human still present becomes host.
- Returning members get a new arrival order. Mute/deafen/camera changes do not change arrival order.
- When the original creator rejoins, ownership immediately returns to them. Room settings, invitations and blocks survive handover.
- Joining ➕ again returns a creator to their existing room. A person may create one room per server; temporarily hosting another person's room does not consume that slot.
- Rooms are deleted 10 seconds after the last member leaves. A never-joined room created outside voice has an initial 60-second join window.

### Slash commands

| Command | Purpose |
|---|---|
| `/createvc` | Create or return to your room |
| `/room` | Open controls for the room you host |
| `/invite user_to_invite: @Member` | Grant room and chat access |
| `/kick user_to_kick: @Member` | Disconnect; use Manage people to block |
| `/setup` | Admin configuration; requires Manage Channels |

## Server setup

Use the **bot** and **applications.commands** scopes and enable **Server Members Intent**. Message Content and Presence intents are not required.

The bot needs View Channels, Manage Channels, Manage Roles, Move Members, Connect, Speak, Voice Activity, Video, Send Messages, Read Message History, Embed Links, Attach Files, Add Reactions, Use Application Commands and **Pin Messages**. Administrator is not required. It must have access to its managed category.

`/setup` retains the configured entry voice channel and creates the bot-only #voice-controls launcher in **Temporary Channels**, the category managed by the bot. This avoids broadening bot access to unrelated existing voice channels. Audience roles can view/use the launcher but cannot post there. Members can still write in their room chats.

## Development

Python 3.12+ and discord.py 2.7.1.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Do not start a second gateway client using the production token while the server bot is running. Tests run without a token or Discord connection.

Modules: `room_state.py` contains persisted ownership and permission policy; `room_service.py` manages Discord actions and lifecycle; `TempVCBot.py` contains persistent views, menus and commands. Each action checks the current host again, so stale controls cannot grant a former host access.

## Deployment and persistence

The existing Hetzner deployment is standalone Docker Compose at `/opt/tempvcbot`, outside the Coolify dashboard. No ports are exposed. It uses UID/GID 10001, a read-only root, resource limits and rotating logs.

```sh
cd /opt/tempvcbot
docker compose build bot
docker compose up -d --no-build
docker compose ps
docker compose logs --tail 100 bot
```

Keep `/opt/tempvcbot/data` and `/opt/tempvcbot/secrets` when updating. The token is read from `DISCORD_TOKEN_FILE` (default `/run/secrets/discord_token`); never commit it or put it in the image. Configuration and runtime state are excluded from Git and Docker build context.

- `config.json`: category, entry, control channel and launcher message IDs.
- `rooms.json`: version 2 state, with creator, current host, mode, invitation/block lists, join sequence and panel ID.
- `channels.json`: preserved legacy ownership file. Imported once if rooms.json does not yet exist; active legacy rooms retain their existing public/visible mode.
- `health.json`: gateway readiness, initialization and heartbeat. Initialization failures prevent a false healthy status.

On restart, persisted join order is retained for members still present. New arrivals while the bot was offline have no exact observed join time; their fallback order is deterministic. Deleted panels are recreated when controls reopen or the bot restarts. Deleted rooms are removed from tracking.

Before the initial v2 deployment, source/configuration/data were backed up to `/opt/tempvcbot/backups/before-room-controls-20261018`. For rollback, stop the container, retain a separate snapshot of current data, restore that backup's source/Compose files and data, then run `docker compose up -d --no-build` with the original `tempvcbot:bb25cd2-local` image. A rollback to v1 cannot preserve all v2 features; inspect active rooms first.
