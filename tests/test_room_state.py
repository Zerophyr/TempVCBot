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


class Guild:
    id = 1
    default_role = Target(1)
    me = Target(9)
    roles = {2: Target(2), 3: Target(3)}
    members = {10: Target(10), 20: Target(20), 30: Target(30)}

    def get_role(self, ident):
        return self.roles.get(ident)

    def get_member(self, ident):
        return self.members.get(ident)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(state, 'Persistent room-state and privacy policy are not implemented')
        self.room = state.Room(channel_id=100, guild_id=1, creator_id=10, host_id=10)

    def test_new_rooms_are_hidden_and_creator_remains_distinct_from_host(self):
        self.assertEqual(self.room.mode, 'hidden')
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

    def test_store_round_trip_preserves_access_order_and_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            store = state.RoomStore(Path(directory) / 'rooms.json')
            self.room.invited.add(20)
            self.room.blocked.add(30)
            self.room.join(20)
            self.room.panel_id = 500
            store.rooms[100] = self.room
            store.save()
            loaded = state.RoomStore(store.path).rooms[100]
            self.assertEqual(loaded.invited, {20})
            self.assertEqual(loaded.blocked, {30})
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

    def test_hidden_denies_audience_visibility_even_if_role_has_global_view(self):
        guild = Guild()
        overwrites = state.build_overwrites(guild, self.room, [2, 3])
        self.assertFalse(overwrites[guild.default_role].view_channel)
        for role in guild.roles.values():
            self.assertFalse(overwrites[role].view_channel)
            self.assertFalse(overwrites[role].connect)

    def test_invite_grants_visibility_voice_video_and_chat(self):
        guild = Guild()
        self.room.invited.add(20)
        overwrite = state.build_overwrites(guild, self.room, [2])[guild.members[20]]
        for name in ['view_channel', 'connect', 'speak', 'stream', 'send_messages', 'read_message_history', 'use_voice_activation']:
            self.assertTrue(getattr(overwrite, name), name)

    def test_creator_and_bot_keep_hidden_room_access(self):
        guild = Guild()
        self.room.host_id = 20
        overwrites = state.build_overwrites(guild, self.room, [2])
        for target in [guild.me, guild.members[10], guild.members[20]]:
            self.assertTrue(overwrites[target].view_channel)
            self.assertTrue(overwrites[target].connect)

    def test_visible_mode_allows_view_and_chat_but_denies_role_connect(self):
        guild = Guild()
        self.room.mode = 'visible'
        overwrite = state.build_overwrites(guild, self.room, [2])[guild.roles[2]]
        self.assertTrue(overwrite.view_channel)
        self.assertTrue(overwrite.send_messages)
        self.assertFalse(overwrite.connect)

    def test_public_mode_grants_audience_connect_without_exposing_other_roles(self):
        guild = Guild()
        self.room.mode = 'public'
        overwrites = state.build_overwrites(guild, self.room, [2])
        self.assertTrue(overwrites[guild.roles[2]].connect)
        self.assertFalse(overwrites[guild.default_role].view_channel)

    def test_member_block_overrides_public_role_access(self):
        guild = Guild()
        self.room.mode = 'public'
        self.room.invited.add(20)
        self.room.blocked.add(20)
        overwrite = state.build_overwrites(guild, self.room, [2])[guild.members[20]]
        self.assertFalse(overwrite.view_channel)
        self.assertFalse(overwrite.connect)
        self.assertFalse(overwrite.send_messages)

    def test_invited_member_keeps_access_across_all_modes(self):
        guild = Guild()
        self.room.invited.add(20)
        for mode in ['hidden', 'visible', 'public']:
            self.room.mode = mode
            self.assertTrue(state.build_overwrites(guild, self.room, [2])[guild.members[20]].connect)


if __name__ == '__main__':
    unittest.main()
