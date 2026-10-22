# Voice rooms — preview of the upcoming update

**Not live yet. These instructions apply after the update is approved and deployed.**

## Start a room

1. Open **#voice-dashboard** under **Temporary Channels**.
2. Click **Create a room**.
3. Your room is **public** and has **no user limit**. Anyone with normal voice
   access can join, chat and use webcam/video. You don't need to invite them.
4. If already in voice, you are moved into your new room. Otherwise open the
   room link and join within **60 seconds**, before the unused room closes.

## Join someone else's room

Open **Find a room** in the dashboard, or use `/rooms`. Pick a room to check
whether it has space, then select **Join voice room**. You can also use
**Join / Notify me** in that room's chat panel.

If it is full, select **Notify me when a place opens**. The bot will send you
**one DM** when space becomes available. Enable DMs from server members.

The notification doesn't reserve a place, and you won't be moved automatically.
Open the link in the DM to join. If it fills again, you can subscribe again.
Cancel using **Cancel my notification**, or `/unnotify channel: <room>`.

Discord's normal “channel full” message cannot show the bot's buttons. If you
try joining directly and it is full, open **Find a room** or use
`/notify channel: <room>`. **Check again** refreshes availability if it changes
while you have the menu open.

## Adjust your room

The **current host** can use **Manage my room**, `/room`, or the room chat panel:

- **Rename:** choose a room name.
- **User limit:** enter **5–99** for a limit, or **0** for unlimited. Lowering a
  limit does not remove people already inside.

There are no private rooms, invitation requirements, kick or block controls.
Admins and server moderators always retain their normal Discord moderation
authority, including manually removing people according to their permissions.
They can override a room's settings without becoming host: open **Find a room**,
choose the room, then **Manage room**. Staff can set **1–99** participants or
**0** for unlimited. The five-person minimum applies to regular hosts.
The bot preserves manual staff settings and moderation changes across host
changes and restarts after the initial update.

## Who is responsible for a room?

Look for **👑 Current room owner** in the room's chat control card. It shows
the owner and their user ID. The voice channel's status also shows the owner,
so you can find them without scrolling through chat. Both update when hosting
changes or the creator returns.

When someone renames a room or changes its size through the bot, a message
in room chat shows **who changed it**, their user ID, and the old and new
settings. Staff changes identify the staff member as well as the current
owner. These messages don't ping anyone. The bot also records the acting user
ID in Discord's audit-log reason so moderators can trace a name change.

These chat messages disappear with the temporary room. The owner card is
the bot's current reference; server staff can override the native status.

## Leave and cleanup

If the host leaves, the earliest-joined person still present becomes host
after **10 seconds**. When the original creator returns, hosting returns to
them. Room settings stay the same.

When everyone leaves, the room and its chat close after **10 seconds**. Its
temporary controls and outstanding notifications are cleaned up too. Save
anything you want to keep from the chat before leaving.

Each person can create one room at a time. If a personal menu disappears or
stops responding, reopen it through **#voice-dashboard**.
