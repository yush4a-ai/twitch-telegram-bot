import ast
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from aiogram.types import Message, Chat, User, CallbackQuery, InaccessibleMessage

from bot.database import Database
from bot.handlers import streams
from bot.middlewares import ThrottleMiddleware, CallbackGuardMiddleware
from tests.test_telegram_navigation import message, CONFIG


class TelegramCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');await self.db.connect();self.addAsyncCleanup(self.db.close)

    def cb(self,data,chat=101,actor=101,kind='private'):
        return SimpleNamespace(data=data,message=message(actor,chat,kind),from_user=SimpleNamespace(id=actor),
                               answer=AsyncMock(),bot=SimpleNamespace())

    async def test_about_has_three_short_facts_app_and_back(self):
        cb=self.cb('menu:about')
        await streams.cb_menu_about(cb,CONFIG)
        call=cb.message.edit_text.await_args
        self.assertLess(len(call.args[0]),400)
        for fact in ('начале','публик','приложени'):self.assertIn(fact,call.args[0].lower())
        buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertEqual([b.text for b in buttons],['Открыть приложение','← Назад'])
        self.assertEqual(buttons[0].web_app.url,CONFIG.oauth_public_base_url+'/app')
        self.assertEqual(buttons[-1].callback_data,'menu:more')

    async def test_help_is_separate_with_actual_support_and_ready_legal_only(self):
        from bot.handlers.telegram_help import cb_help
        cb=self.cb('menu:help');await cb_help(cb,CONFIG)
        call=cb.message.edit_text.await_args
        self.assertIn('Помощь',call.args[0])
        self.assertNotIn('support@',call.args[0])
        self.assertFalse(any(b.url and '/legal/' in b.url for row in call.kwargs['reply_markup'].inline_keyboard for b in row))
        configured=SimpleNamespace(**vars(CONFIG),support_username='signal_support',support_email='help@example.test')
        await cb_help(cb,configured)
        call=cb.message.edit_text.await_args
        self.assertIn('help@example.test',call.args[0])
        self.assertTrue(any(b.url=='https://t.me/signal_support' for row in call.kwargs['reply_markup'].inline_keyboard for b in row))
        # Telegram inline URL buttons support HTTP/tg, not mailto.
        self.assertFalse(any(b.url and b.url.startswith('mailto:') for row in call.kwargs['reply_markup'].inline_keyboard for b in row))

    async def test_reports_keep_html_and_group_linking_inside_reports(self):
        await self.db.add_channel(-1001,'alpha')
        cb=self.cb('menu:report',-1001,101,'supergroup')
        await streams.cb_menu_report(cb,self.db)
        buttons=[b for row in cb.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertTrue(any(b.callback_data=='report:alpha' for b in buttons))
        self.assertTrue(any(b.callback_data=='menu:link_stats' for b in buttons))
        self.assertEqual(buttons[-1].callback_data,'menu:more')
        self.assertTrue(callable(streams.build_report_html))
        self.assertEqual(await self.db.get_report_format(-1001,'alpha'),'brief')
        await self.db.set_report_format(-1001,'alpha','full')
        self.assertEqual(await self.db.get_report_format(-1001,'alpha'),'full')

    async def test_free_quiet_presets_custom_back_no_plus_required(self):
        await self.db.set_utc_offset(101,180)
        text,kb=await streams._quiet_hours_screen_text_and_keyboard(101,self.db)
        actions={b.callback_data for row in kb.inline_keyboard for b in row}
        self.assertTrue({'qhpreset:22:7','qhpreset:23:8','qhpreset:0:9','qh:custom','menu:more'}<=actions)
        self.assertFalse(await self.db.has_viewer_plus(101))

    async def test_menu_bypasses_input_throttle_to_cancel_immediately(self):
        middleware=ThrottleMiddleware()
        middleware._last_action[101]=time.monotonic()
        handler=AsyncMock()
        msg=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=101,type='private'),
                    from_user=User(id=101,is_bot=False,first_name='Test'),text='Меню')
        await middleware(handler,msg,{'event_from_user':msg.from_user})
        handler.assert_awaited_once()

    async def test_malformed_old_group_callback_has_recovery_and_no_mutation(self):
        cb=self.cb('managegroup:garbage')
        await streams.cb_manage_group(cb,self.db)
        cb.message.edit_text.assert_not_awaited();cb.answer.assert_awaited_once()
        self.assertIn('Меню',cb.answer.await_args.args[0])

    async def test_old_inaccessible_callback_recovery_mentions_menu(self):
        cb=CallbackQuery(id='old',from_user=User(id=101,is_bot=False,first_name='Test'),chat_instance='old',
                         data='menu:list',message=InaccessibleMessage(chat=Chat(id=101,type='private'),message_id=1,date=0))
        handler=AsyncMock()
        with patch.object(CallbackQuery,'answer',new_callable=AsyncMock) as answer:
            await CallbackGuardMiddleware()(handler,cb,{})
            self.assertIn('Меню',answer.await_args.args[0])
        handler.assert_not_awaited()

    async def test_group_member_cannot_forge_free_quiet_setting_callbacks(self):
        cb=self.cb('qhpreset:22:7',-1001,101,'supergroup')
        cb.bot.get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member'))
        await self.db.set_utc_offset(-1001,180)
        before=self.db.conn.total_changes
        await streams.cb_quiet_hours_preset(cb,self.db)
        self.assertEqual(self.db.conn.total_changes,before)
        self.assertIsNone(await self.db.get_quiet_hours(-1001))

    async def test_more_back_clears_unfinished_quiet_input(self):
        from bot.handlers.navigation import cb_more
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from aiogram.fsm.storage.memory import MemoryStorage
        state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        await state.set_state(streams.QuietHoursSetup.waiting_for_custom_time)
        await cb_more(self.cb('menu:more'),CONFIG,state,self.db)
        self.assertIsNone(await state.get_state())

    def test_all_baseline_callback_and_command_handlers_remain_registered(self):
        baseline=json.loads(Path('docs/audits/telegram-ui-2026-10-03/BASE-UI-INVENTORY.json').read_text(encoding='utf-8'))
        for route in baseline['callback_routes']:
            self.assertTrue(any(h.callback.__name__==route['handler'] for h in streams.router.callback_query.handlers),route['handler'])
        tree=ast.parse(Path('bot/handlers/streams.py').read_text(encoding='utf-8'))
        filters={node.name:ast.unparse(node.decorator_list[0]) for node in ast.walk(tree)
                 if isinstance(node,ast.AsyncFunctionDef) and node.decorator_list}
        for route in baseline['callback_routes']:
            self.assertEqual(filters[route['handler']],route['filter'])
        # Explicit decorated commands stay in their original source, including legacy commands.
        for command in baseline['commands']:
            source=Path(command['file']).read_text(encoding='utf-8-sig')
            self.assertIn('Command("'+command['command']+'")',source)

    def test_new_human_copy_has_no_internal_terms_or_encoding_damage(self):
        import re
        forbidden=re.compile(r'\b(callback|placement|entitlement|ledger|provider|fixture|mock|staging)\b',re.I)
        for filename in ('bot/telegram_ui.py','bot/handlers/navigation.py','bot/handlers/telegram_add.py',
                         'bot/handlers/telegram_streamer.py','bot/handlers/telegram_plus.py','bot/handlers/telegram_help.py'):
            tree=ast.parse(Path(filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.search('[А-Яа-я]',node.value):
                    self.assertIsNone(forbidden.search(node.value),(filename,node.value))
                    self.assertNotRegex(node.value,r'Р[Ўўњќ]|С[ЏЂЎѓ]')

