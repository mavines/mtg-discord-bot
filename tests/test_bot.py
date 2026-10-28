import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord


ROOT = Path(__file__).resolve().parents[1]


class DraftBotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous)
        Path('card-list.txt').write_text('Lightning Bolt\nBlack Lotus\nBlack Knight\n', encoding='utf-8')
        spec = importlib.util.spec_from_file_location('draft_bot', ROOT / 'bot.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=123, name='Tester', discriminator='0'),
            channel_id=456,
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def register(self):
        await self.module.register.callback(self.interaction)

    async def test_slash_command_schema(self):
        commands = self.module.bot.tree.get_commands()
        self.assertEqual({command.name for command in commands},
                         {'register', 'start_draft', 'draft', 'clear', 'search'})
        for command in commands:
            payload = command.to_dict(self.module.bot.tree)
            self.assertTrue(payload['description'])
        self.assertTrue(self.module.draft.to_dict(self.module.bot.tree)['options'][0]['autocomplete'])
        self.assertEqual(self.module.start_draft.default_permissions.manage_guild, True)
        self.assertFalse(self.module.bot.intents.message_content)
        self.assertFalse(self.module.bot.intents.members)

    async def test_registration_persists_string_id(self):
        await self.register()
        self.assertEqual(self.module.get_user(123)['team-name'], 'Tester')
        saved = json.loads(Path('users.db').read_text())
        self.assertEqual(saved['123']['discord-id'], 123)
        await self.register()
        self.assertIn('already registered', self.interaction.response.send_message.call_args.args[0])

    async def test_queue_partial_match_and_clear(self):
        await self.register()
        await self.module.draft.callback(self.interaction, 'lightning')
        self.assertEqual(self.module.get_user(123)['wanted-cards'], ['lightning bolt'])
        await self.module.clear.callback(self.interaction)
        self.assertEqual(self.module.get_user(123)['wanted-cards'], [])

    async def test_queue_rejects_ambiguous_and_taken_cards(self):
        await self.register()
        await self.module.draft.callback(self.interaction, 'black')
        self.assertEqual(self.module.get_user(123)['wanted-cards'], [])
        self.assertIn('Multiple matching', self.interaction.response.send_message.call_args.args[0])
        self.module.get_card('lightning bolt')['taken'] = True
        await self.module.draft.callback(self.interaction, 'lightning bolt')
        self.assertEqual(self.module.get_user(123)['wanted-cards'], [])

    async def test_search_and_autocomplete(self):
        await self.module.search.callback(self.interaction, 'black')
        self.assertIn('Black Lotus', self.interaction.response.send_message.call_args.args[0])
        choices = await self.module.card_autocomplete(self.interaction, 'light')
        self.assertEqual([choice.value for choice in choices], ['Lightning Bolt'])

    async def test_start_requires_players(self):
        await self.module.start_draft.callback(self.interaction)
        self.assertFalse(self.module.draft_data.get('has-started'))
        self.interaction.response.defer.assert_not_awaited()

    async def test_start_and_pick_without_google(self):
        await self.register()
        await self.module.draft.callback(self.interaction, 'lightning bolt')
        channel = SimpleNamespace(send=AsyncMock())
        with patch.object(self.module, 'get_google_sheet', side_effect=AssertionError('Google must be optional')):
            await self.module.start_draft.callback(self.interaction)
            with patch.object(self.module.bot, 'get_channel', return_value=channel):
                await self.module.attempt_draft.coro()
        self.assertTrue(self.module.get_card('lightning bolt')['taken'])
        self.assertEqual(self.module.get_pick_number(), 1)
        self.assertEqual(self.module.get_user(123)['drafted-cards'], ['lightning bolt'])
        channel.send.assert_awaited_once()

    async def test_failed_google_setup_does_not_start_draft(self):
        await self.register()
        with patch.object(self.module.gspread, 'service_account', side_effect=RuntimeError('Google unavailable')):
            with self.assertRaises(RuntimeError):
                await self.module.start_draft.callback(self.interaction, 'test-sheet')
        self.assertFalse(self.module.draft_data.get('has-started'))
        self.interaction.response.defer.assert_awaited_once()

    async def test_snake_order(self):
        self.module.draft_data.set('pick-order', ['a', 'b', 'c'])
        indices = []
        for pick_number in range(6):
            self.module.draft_data.set('pick-number', pick_number)
            indices.append(self.module.get_current_index())
        self.assertEqual(indices, [0, 1, 2, 2, 1, 0])

    async def test_setup_syncs_global_commands_once(self):
        with patch.dict(os.environ, {'DISCORD_GUILD_ID': ''}), \
                patch.object(self.module.bot.tree, 'sync', new_callable=AsyncMock) as sync, \
                patch.object(self.module.attempt_draft, 'start') as start:
            await self.module.bot.setup_hook()
        sync.assert_awaited_once_with()
        start.assert_called_once_with()

    async def test_setup_syncs_development_guild(self):
        with patch.dict(os.environ, {'DISCORD_GUILD_ID': '789'}), \
                patch.object(self.module.bot.tree, 'sync', new_callable=AsyncMock) as sync, \
                patch.object(self.module.attempt_draft, 'start'):
            await self.module.bot.setup_hook()
        self.assertEqual(sync.call_args.kwargs['guild'].id, 789)


if __name__ == '__main__':
    unittest.main()