"""Durable public-room ownership, notification opt-ins and permission policy."""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import discord


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
    waiting: set[int] = field(default_factory=set)
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
        eligible = set(present)
        if self.creator_id in eligible:
            return self.creator_id
        if self.host_id in eligible:
            return self.host_id
        if not eligible:
            return None
        return min(eligible, key=lambda ident: (self.join_order.get(ident, float('inf')), ident))

    def to_dict(self):
        value = asdict(self)
        value['waiting'] = sorted(self.waiting)
        return value

    @classmethod
    def from_dict(cls, value, *, legacy=False):
        value = dict(value)
        if legacy:
            if value.pop('mode', 'hidden') not in {'hidden', 'visible', 'public'}:
                raise ValueError('Invalid legacy room privacy mode')
            value.pop('invited', None)
            value.pop('blocked', None)
        value['waiting'] = set(map(int, value.get('waiting', [])))
        value['join_order'] = {int(key): int(order) for key, order in value.get('join_order', {}).items()}
        room = cls(**value)
        if min(room.channel_id, room.guild_id, room.creator_id, room.host_id, *room.waiting) <= 0:
            raise ValueError('Invalid persisted room state')
        return room


class RoomStore:
    def __init__(self, path):
        self.path = Path(path)
        self.rooms = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            version = data.get('version')
            if version not in {2, 3}:
                raise ValueError('Unsupported room state version; preserve this file before migrating')
            self.rooms = {int(key): Room.from_dict(value, legacy=version == 2) for key, value in data['rooms'].items()}
            if any(key != room.channel_id for key, room in self.rooms.items()):
                raise ValueError('Room IDs do not match persisted keys')

    def save(self):
        atomic_json(self.path, {'version': 3, 'rooms': {key: room.to_dict() for key, room in self.rooms.items()}})


def member_access(allowed=True):
    return discord.PermissionOverwrite(
        view_channel=allowed, connect=allowed, speak=allowed, stream=allowed,
        use_voice_activation=allowed, send_messages=allowed,
        read_message_history=allowed, add_reactions=allowed, attach_files=allowed,
        embed_links=allowed,
    )


def build_overwrites(guild, room, audience_ids):
    audience = [role for ident in audience_ids if (role := guild.get_role(ident)) is not None]
    overwrites = {guild.default_role: member_access(not audience)}
    for role in audience:
        overwrites[role] = member_access()
    for ident in {room.creator_id, room.host_id}:
        target = guild.get_member(ident) or discord.Object(id=ident, type=discord.Member)
        overwrites[target] = member_access()
    overwrites[guild.me] = member_access()
    return overwrites
