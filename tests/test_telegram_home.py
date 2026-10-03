"""Server Home state and real aiogram method dispatch through a local transport."""
import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, Chat, User, InlineKeyboardMarkup

from bot.database import Database
from bot.handlers.streams import cmd_start, cb_menu_home
from tests.test_telegram_navigation import CONFIG


class MenuTransport(BaseSession):
    def __init__(self):
        super().__init__(); self.calls=[]; self.messages={}; self.next_id=10; self.fail_edit=False

    async def close(self): pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name=method.__api_method__
        if name.startswith('editMessage'):
            if self.fail_edit:
                self.fail_edit=False
                raise TelegramBadRequest(method=method,message='Bad Request: message to edit not found')
            old=self.messages[(method.chat_id,method.message_id)]
            if name=='editMessageText' and old.photo:
                raise TelegramBadRequest(method=method,message='Bad Request: there is no text in the message to edit')
            values=old.model_dump()
            if name=='editMessageMedia':
                values.update(text=None,photo=[{'file_id':'banner-'+str(bot.id),'file_unique_id':'banner','width':1000,'height':500}],caption=method.media.caption)
            elif name=='editMessageCaption': values['caption']=method.caption
            elif name=='editMessageText': values['text']=method.text
            values['reply_markup']=method.reply_markup
            result=Message.model_validate(values).as_(bot)
        else:
            self.next_id+=1
            values=dict(message_id=self.next_id,date=datetime.now(timezone.utc),chat=Chat(id=method.chat_id,type='private'),
                        from_user=User(id=bot.id,is_bot=True,first_name='Bot'))
            if name=='sendPhoto':
                values.update(caption=method.caption,photo=[{'file_id':'banner-'+str(bot.id),'file_unique_id':'banner','width':1000,'height':500}])
            else: values['text']=method.text
            if isinstance(method.reply_markup,InlineKeyboardMarkup): values['reply_markup']=method.reply_markup
            result=Message(**values).as_(bot)
        self.messages[(result.chat.id,result.message_id)]=result
        return result


class MenuClient:
    """Only the binding/call protocol, with no token or HTTP client."""
    def __init__(self,bot_id,session):
        self.id=bot_id;self.session=session

    async def __call__(self,method,request_timeout=None):
        return await self.session.make_request(self,method,request_timeout)


class SmartHomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from bot import telegram_home
        telegram_home._menus.clear()
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.db=Database(str(Path(temp.name)/'home.db'));await self.db.connect();self.addAsyncCleanup(self.db.close)
        self.transport=MenuTransport();self.bot=MenuClient(777,self.transport);self.addAsyncCleanup(self.bot.session.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=777,chat_id=101,user_id=101))

    def incoming(self,actor=101,chat=None):
        return Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=chat or actor,type='private'),
                       from_user=User(id=actor,is_bot=False,first_name='Test'),text='Меню').as_(self.bot)

    async def state_for(self,actor=101):
        from bot.telegram_home import load_home_state, build_home
        return build_home(await load_home_state(self.db,actor))

    async def test_zero_subscriptions_banner_and_official_styles(self):
        view=await self.state_for()
        self.assertTrue(view.banner)
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual([m.__api_method__ for m in self.transport.calls],['sendMessage','sendPhoto'])
        sent=self.transport.calls[-1]
        self.assertEqual(sent.caption,'TwitchSignalBot\n\nСледи за стримерами или подключи свой канал.')
        buttons=[r[0] for r in sent.reply_markup.inline_keyboard]
        self.assertEqual([b.text for b in buttons],['Открыть приложение','➕ Добавить оповещения','🎥 Я стример','Ещё'])
        self.assertEqual([b.style for b in buttons],['primary','success',None,None])
        from bot.telegram_home import BANNER_PATH
        self.assertTrue(BANNER_PATH.is_file());self.assertLess(BANNER_PATH.stat().st_size,10*1024*1024)

    async def test_subscriptions_live_zero_one_many_cap_and_escaping(self):
        for i in range(6): await self.db.add_channel(101,f'live{i}')
        view=await self.state_for()
        self.assertFalse(view.banner)
        self.assertEqual(view.text,'TwitchSignalBot\n\nОтслеживаешь: 6\nСейчас никто не в эфире.\n\nЯ сообщу, когда кто-нибудь начнёт стрим.')
        await self.db.conn.execute("UPDATE tracked_channels SET is_live=1 WHERE chat_id=101 AND twitch_login='live0'")
        await self.db.conn.commit()
        view=await self.state_for();self.assertIn('Сейчас в эфире: 1',view.text);self.assertEqual(view.text.count('live0'),1)
        await self.db.conn.execute('UPDATE tracked_channels SET is_live=1 WHERE chat_id=101');await self.db.conn.commit()
        view=await self.state_for();self.assertIn('Сейчас в эфире: 6',view.text);self.assertIn('И ещё 3 в эфире',view.text)
        for i in range(3): self.assertIn(f'live{i}',view.text)
        for i in range(3,6): self.assertNotIn(f'live{i}',view.text)
        from bot.telegram_home import HomeState,build_home
        text=build_home(HomeState(tracked=1,live=(('longname','<Category & other>'),),verified=True)).text
        self.assertIn('longname · &lt;Category &amp; other&gt;',text)

    async def test_verified_only_compact_status_never_claims_publishing(self):
        await self.db.save_user_token('alpha','11','access','refresh',time.time()+300)
        self.assertTrue((await self.state_for()).banner)
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        view=await self.state_for();self.assertFalse(view.banner);self.assertIn('Твой Twitch подключён',view.text)
        self.assertNotIn('публикации включены',view.text)

    async def test_cross_user_state_and_forged_private_callback_are_denied(self):
        await self.db.add_channel(202,'privateother')
        await self.db.link_streamer_identity(202,'22','privateother',verified_at=time.time())
        self.assertNotIn('privateother',(await self.state_for(101)).text)
        await cmd_start(self.incoming(actor=202,chat=101),self.state,self.db,CONFIG)
        self.assertEqual(self.transport.calls,[])
        callback=SimpleNamespace(message=self.incoming(),from_user=SimpleNamespace(id=202),answer=unittest.mock.AsyncMock())
        await cb_menu_home(callback,self.state,CONFIG,self.db)
        self.assertEqual(self.transport.calls,[])
        callback.answer.assert_awaited_once()

    async def test_menu_reuses_message_caption_then_text_and_fallback_once(self):
        from bot.handlers.navigation import cb_more
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        photo=list(self.transport.messages.values())[-1]
        callback=SimpleNamespace(message=photo,from_user=SimpleNamespace(id=101),answer=unittest.mock.AsyncMock())
        await cb_more(callback,CONFIG)
        self.assertEqual(self.transport.calls[-1].__api_method__,'editMessageCaption')
        await cb_menu_home(callback,self.state,CONFIG,self.db)
        for _ in range(6): await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual(sum(m.__api_method__.startswith('send') for m in self.transport.calls),2)
        await self.db.add_channel(101,'alpha')
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual(sum(m.__api_method__=='sendMessage' for m in self.transport.calls),2)
        for _ in range(5): await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual(sum(m.__api_method__=='sendMessage' for m in self.transport.calls),2)
        self.transport.fail_edit=True
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual(sum(m.__api_method__=='sendMessage' for m in self.transport.calls),3)
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        self.assertEqual(sum(m.__api_method__=='sendMessage' for m in self.transport.calls),3)

    async def test_concurrent_menu_and_per_bot_file_id_reuse(self):
        await asyncio.gather(*(cmd_start(self.incoming(),self.state,self.db,CONFIG) for _ in range(8)))
        self.assertEqual(sum(m.__api_method__.startswith('send') for m in self.transport.calls),2)
        await cmd_start(self.incoming(202),self.state,self.db,CONFIG)
        second=[m for m in self.transport.calls if m.__api_method__=='sendPhoto'][-1]
        self.assertEqual(second.photo,'banner-777')
        other_transport=MenuTransport()
        other=MenuClient(888,other_transport);self.addAsyncCleanup(other.session.close)
        await cmd_start(self.incoming(303).as_(other),self.state,self.db,CONFIG)
        self.assertNotEqual(other_transport.calls[-1].photo,'banner-777')

    async def test_menu_store_is_bounded(self):
        from bot.telegram_home import store_for
        store=store_for(self.incoming())
        for i in range(1100): store.remember(self.incoming(i+1))
        self.assertEqual(len(store.messages),1024)
        self.assertEqual(store.get(1),(None,False))

    async def test_menu_restores_reply_keyboard_after_channel_selector_with_cached_home(self):
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        await self.state.set_data({'telegram_community_intent':'pending-selector'})
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        restored=[m for m in self.transport.calls if m.__api_method__=='sendMessage' and m.text=='Выбор канала отменён.']
        self.assertEqual(len(restored),1)
        self.assertEqual([[b.text for b in row] for row in restored[0].reply_markup.keyboard],[['Меню']])
        self.assertEqual(sum(m.__api_method__=='sendPhoto' for m in self.transport.calls),1)

    async def test_home_replaces_unrelated_photo_and_caption_excludes_text_only_options(self):
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        photo=list(self.transport.messages.values())[-1]
        old=photo.model_copy(update={'photo':[photo.photo[0].model_copy(update={'file_id':'stream-thumbnail'})]}).as_(self.bot)
        self.transport.messages[(old.chat.id,old.message_id)]=old
        callback=SimpleNamespace(message=old,from_user=SimpleNamespace(id=101),answer=unittest.mock.AsyncMock())
        await cb_menu_home(callback,self.state,CONFIG,self.db)
        self.assertEqual(self.transport.calls[-1].__api_method__,'editMessageMedia')
        self.assertEqual(self.transport.calls[-1].media.media,'banner-777')
        from bot.telegram_home import edit_menu
        await edit_menu(photo,'Old live list',disable_web_page_preview=True)
        self.assertEqual(self.transport.calls[-1].__api_method__,'editMessageCaption')
        self.assertNotIn('disable_web_page_preview',self.transport.calls[-1].model_dump(exclude_none=True))

    async def test_long_legacy_text_from_photo_fallback_not_truncated(self):
        from bot.telegram_home import edit_menu
        await cmd_start(self.incoming(),self.state,self.db,CONFIG)
        photo=list(self.transport.messages.values())[-1]
        content='Сохранённый отчёт. '*150
        await edit_menu(photo,content)
        self.assertEqual(self.transport.calls[-2].text,content)
        self.assertEqual(self.transport.calls[-1].__api_method__,'editMessageReplyMarkup')

    async def test_not_modified_no_duplicate_and_unexpected_errors_not_hidden(self):
        from bot.telegram_home import edit_menu
        msg=SimpleNamespace(photo=None,animation=None,video=None,document=None,audio=None,caption=None,
                            edit_text=unittest.mock.AsyncMock(),answer=unittest.mock.AsyncMock())
        method=SimpleNamespace()
        msg.edit_text.side_effect=TelegramBadRequest(method=method,message='Bad Request: message is not modified')
        await edit_menu(msg,'text');msg.answer.assert_not_awaited()
        msg.edit_text.side_effect=TelegramBadRequest(method=method,message='Bad Request: BUTTON_DATA_INVALID')
        with self.assertRaises(TelegramBadRequest): await edit_menu(msg,'text')
        msg.answer.assert_not_awaited()
