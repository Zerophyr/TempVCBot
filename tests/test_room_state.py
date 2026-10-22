import importlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import discord

try:
    state = importlib.import_module('room_state')
except ModuleNotFoundError:
    state = None


@dataclass(frozen=True)
class Target:
    id: int

    @property
    def permissions(self):
        return discord.Permissions.none()


class Guild:
    id = 1
    default_role = Target(1)
    me = Target(9)
    roles = [Target(2), Target(3)]
    members = {10: Target(10), 20: Target(20), 30: Target(30)}

    def get_role(self, ident):
        return next((role for role in self.roles if role.id == ident), None)

    def get_member(self, ident):
        return self.members.get(ident)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state, 'Persistent room-state and privacy policy are not implemented')
        self.room = state.Room(channel_id=100, guild_id=1, creator_id=10, host_id=10)

    def test_creator_remains_distinct_from_host(self):
        self.room.host_id = 20
        self.assertEqual(self.room.creator_id, 10)

    def test_earliest_present_member_is_selected_when_host_leaves(self):
        for ident in [10, 30, 20]:
            self.room.join(ident)
        self.room.leave(10)
        self.assertEqual(self.room.choose_host({20, 30}), 30)

    def test_rejoining_member_gets_new_arrival_order(self):
        for ident in [10, 20, 30]:
            self.room.join(ident)
        self.room.leave(20)
        self.room.join(20)
        self.assertEqual(self.room.choose_host({20, 30}), 30)

    def test_creator_return_has_priority_over_current_host(self):
        self.room.host_id = 20
        self.room.join(20)
        self.room.join(10)
        self.assertEqual(self.room.choose_host({10, 20}), 10)

    def test_current_host_is_retained_when_creator_absent(self):
        self.room.join(30)
        self.room.join(20)
        self.room.host_id = 20
        self.assertEqual(self.room.choose_host({20, 30}), 20)

    def test_empty_room_has_no_successor(self):
        self.assertIsNone(self.room.choose_host(set()))

    def test_store_round_trip_preserves_waiters_order_and_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            store = state.RoomStore(Path(directory) / 'rooms.json')
            self.room.waiting.add(20)
            self.room.join(20)
            self.room.panel_id = 500
            store.rooms[100] = self.room
            store.save()
            loaded = state.RoomStore(store.path).rooms[100]
            self.assertEqual(loaded.waiting, {20})
            self.assertEqual(loaded.join_order, {20: 1})
            self.assertEqual(loaded.panel_id, 500)
            self.assertEqual(loaded.creator_id, 10)
            self.assertEqual(loaded.host_id, 10)

    def test_invalid_state_is_reported_instead_of_silently_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rooms.json'
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                state.RoomStore(path)


if __name__ == '__main__':
    unittest.main()
