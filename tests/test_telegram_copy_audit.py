"""Presentation hierarchy keeps catalog truth and native Telegram entities."""
import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.telegram_plus import benefits, product_view
from tests.test_telegram_home_copy import Caption


class CopyAuditTests(unittest.TestCase):
    def test_tariff_benefits_use_four_catalog_groups_and_preserve_details(self):
        from bot.plan_catalog import catalog_payload
        catalog = catalog_payload()
        for product in catalog['products']:
            with self.subTest(product=product['product_id']):
                caption = Caption(benefits(product))
                self.assertEqual(caption.quotes, 4)
                for block in product['benefit_blocks']:
                    self.assertIn(block['title'], caption.bold)
                plain = ''.join(caption.plain)
                for feature_id in product['feature_ids']:
                    self.assertIn(catalog['features'][feature_id]['description'], plain)
                self.assertNotIn('• ', plain)

    def test_help_topics_have_section_titles_and_keep_free_limits_and_exclusions(self):
        from bot.handlers.telegram_help import TOPICS, help_screen
        caption = Caption(help_screen()[0])
        self.assertIn('Помощь', caption.bold)
        self.assertGreaterEqual(len(caption.bold), 4)
        for name, (_title, body) in TOPICS.items():
            with self.subTest(topic=name):
                topic = Caption(body)
                self.assertGreaterEqual(len(topic.bold), 2)
                self.assertGreaterEqual(topic.quotes, 1)
        self.assertIn('Личные оповещения о начале эфира, рейдах', TOPICS['quiet'][1])
        self.assertIn('Рейды также учитывают тихие часы', TOPICS['quiet'][1])
        self.assertIn('Для отдельных стримеров можно включить исключение', TOPICS['quiet'][1])
        self.assertIn('Публикации в Telegram-каналах идут по настройкам канала', TOPICS['quiet'][1])
        self.assertNotIn('Рейды не входят', TOPICS['quiet'][1])
        self.assertIn('24 часов', TOPICS['reports'][1])
        self.assertIn('не требуют покупки тарифа', TOPICS['reports'][1])
        self.assertIn('5 минут', TOPICS['import'][1])

    def test_streamer_intro_separates_identity_connections_and_next_step(self):
        from bot.handlers.telegram_streamer import cb_streamer
        from tests.test_telegram_navigation import message
        async def check():
            msg=message()
            db=SimpleNamespace(get_streamer_identity=AsyncMock(return_value=('11','<alpha>')),
                               list_streamer_communities=AsyncMock(return_value=[(-100,'<channel>',False)]))
            callback=SimpleNamespace(message=msg,from_user=msg.from_user,answer=AsyncMock())
            await cb_streamer(callback,db)
            caption=Caption(msg.edit_text.await_args.args[0])
            self.assertEqual(caption.quotes,1)
            self.assertIn('Ваш Twitch-канал',caption.bold)
            self.assertIn('<alpha>',caption.bold)
            self.assertIn('Telegram-подключения',caption.bold)
            self.assertIn('<channel>',''.join(caption.plain))
            self.assertNotIn('Публикации включены',''.join(caption.plain))
        asyncio.run(check())

    def test_legacy_first_add_distinguishes_live_post_and_private_report(self):
        from bot.handlers.streams import _added_channel_summary
        from tests.test_telegram_navigation import message
        async def check():
            db=SimpleNamespace(is_telegram_channel=AsyncMock(return_value=False))
            text=await _added_channel_summary(message(),db,'alpha',True)
            caption=Caption(text)
            self.assertEqual(caption.quotes,1)
            self.assertIn('Начало эфира',caption.bold)
            self.assertIn('После эфира',caption.bold)
            self.assertIn('В общий чат итоговая статистика не публикуется.',''.join(caption.plain))
        asyncio.run(check())

    def test_report_metrics_are_grouped_without_hiding_missing_data(self):
        from bot.handlers.streams import _build_report_summary
        text=_build_report_summary('alpha','<Название>','1 ч',100,50,None,10,False,
                                   [('reader',12)],[],[],None,
                                   started_at='2026-10-03T10:00:00Z',ended_at=1791025200)
        caption=Caption(text)
        self.assertEqual(caption.quotes,1)
        plain=''.join(caption.plain)
        for field in ('Длительность: 1 ч','Пик зрителей: 100','Среднее число зрителей: 50',
                      'Данные чата: неполные','Начало: 03.10.2026, 10:00 UTC'):
            self.assertIn(field,plain)
        self.assertIn('Топ чатеров',caption.bold)
        self.assertNotIn('Новых фолловеров:',plain)

    def test_quiet_hours_current_state_uses_one_block(self):
        from bot.handlers.streams import _quiet_hours_screen_text_and_keyboard
        async def check():
            db=SimpleNamespace(get_quiet_hours=AsyncMock(return_value=(1320,420,180,True)))
            text,_=await _quiet_hours_screen_text_and_keyboard(101,db)
            caption=Caption(text)
            self.assertEqual(caption.quotes,1)
            self.assertIn('Сейчас у вас',''.join(caption.plain))
        asyncio.run(check())

    def test_welcome_does_not_repeat_banner_brand_and_has_heading(self):
        from bot.telegram_ui import HOME_TEXT
        caption=Caption(HOME_TEXT)
        self.assertIn('Оповещения о Twitch',caption.bold)
        self.assertNotIn('TwitchSignalBot',''.join(caption.plain))

    def test_channel_settings_separate_alerts_and_reports(self):
        from bot.handlers.streams import _render_channel_card
        async def check():
            db=SimpleNamespace(list_channels_with_routing=AsyncMock(return_value=[
                ('alpha',True,False,None,'short',False,False,False,False,True)]),
                is_telegram_channel=AsyncMock(return_value=False),get_stats_recipient=AsyncMock(return_value=None),
                resolve_post_recipient=AsyncMock(return_value=101),get_quiet_hours=AsyncMock(return_value=None),
                get_user_token=AsyncMock(return_value=None))
            text,_=await _render_channel_card(101,'alpha',db,list_back_callback='menu:list')
            caption=Caption(text)
            self.assertGreaterEqual(caption.quotes,1)
            self.assertIn('Оповещения',caption.bold)
            self.assertIn('Отчёты',caption.bold)
            self.assertIn('HTML',''.join(caption.plain))
            self.assertIn('Число фолловеров недоступно',''.join(caption.plain))
        asyncio.run(check())
