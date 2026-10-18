"""Durable room ownership and Discord permission policy, without gateway state."""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import discord

MODES = {
    'hidden': ('👻 Hidden', 'You and invited members can see, join and chat.'),
    'visible': ('🔒 Visible · invite-only', 'Voice members can see and chat; invitations are required to join.'),
    'public': ('🌐 Public', 'Members with normal voice access can see, join and chat.'),
}


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass
class Room:
    channel_id: int
    guild_id: int
    creator_id: int
    host_id: int
    mode: str = 'hidden'
    invited: set[int] = field(default_factory=set)
    blocked: set[int] = field(default_factory=set)
    join_order: dict[int, int] = field(default_factory=dict)
    sequence: int = 0
    panel_id: int | None = None
    created_at: float = field(default_factory=time.time)

    def join(self, member_id):
        if member_id not in self.join_order:
            self.sequence += 1
            self.join_order[member_id] = self.sequence

    def leave(self, member_id):
        self.join_order.pop(member_id, None)

    def choose_host(self, present):
        eligible = set(present) - self.blocked
        if self.creator_id in eligible:
            return self.creator_id
        if self.host_id in eligible:
            return self.host_id
        if not eligible:
            return None
        return min(eligible, key=lambda ident: (self.join_order.get(ident, float('inf')), ident))

    def to_dict(self):
        value = asdict(self)
        value['invited'] = sorted(self.invited)
        value['blocked'] = sorted(self.blocked)
        return value

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value['invited'] = set(map(int, value.get('invited', [])))
        value['blocked'] = set(map(int, value.get('blocked', [])))
        value['join_order'] = {int(key): int(order) for key, order in value.get('join_order', {}).items()}
        room = cls(**value)
        if room.mode not in MODES or min(room.channel_id, room.guild_id, room.creator_id, room.host_id) <= 0:
            raise ValueError('Invalid persisted room state')
        return room


class RoomStore:
    def __init__(self, path):
        self.path = Path(path)
        self.rooms = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 2:
                raise ValueError('Unsupported room state version; preserve this file before migrating')
            self.rooms = {int(key): Room.from_dict(value) for key, value in data['rooms'].items()}
            if any(key != room.channel_id for key, room in self.rooms.items()):
                raise ValueError('Room IDs do not match persisted keys')

    def save(self):
        atomic_json(self.path, {'version': 2, 'rooms': {key: room.to_dict() for key, room in self.rooms.items()}})


def member_access(allowed=True):
    return discord.PermissionOverwrite(
        view_channel=allowed, connect=allowed, speak=allowed, stream=allowed,
        use_voice_activation=allowed, send_messages=allowed,
        read_message_history=allowed, add_reactions=allowed, attach_files=allowed,
        embed_links=allowed,
    )


def build_overwrites(guild, room, audience_ids):
    if room.mode not in MODES:
        raise ValueError('Unknown room privacy mode')
    audience = [role for ident in audience_ids if (role := guild.get_role(ident)) is not None]
    visible = room.mode != 'hidden'
    public = room.mode == 'public'
    everyone = member_access(visible and not audience)
    everyone.connect = public and not audience
    overwrites = {guild.default_role: everyone}
    for role in audience:
        overwrite = member_access(visible)
        overwrite.connect = public
        overwrites[role] = overwrite
    permitted = (room.invited | {room.creator_id, room.host_id}) - room.blocked
    for ident in permitted:
        target = guild.get_member(ident) or discord.Object(id=ident, type=discord.Member)
        overwrites[target] = member_access()
    for ident in room.blocked:
        target = guild.get_member(ident) or discord.Object(id=ident, type=discord.Member)
        overwrites[target] = member_access(False)
    overwrites[guild.me] = member_access()
    return overwrites
