"""Human copy gate; identifiers, fixtures and historical archives are not copy."""

import ast
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

from bot.plan_catalog import PAYMENT_UNAVAILABLE_MESSAGE, catalog_payload


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'\b(?:demo|mock|prototype|staging)\b|тестов(?:ый|ая|ое|ые|ого|ом)\s+(?:доступ|состояние|тариф|продукт)|цена\s+для\s+макета|условная\s+цена|демонстрация\s+тарифов|пример\s+тестового\s+доступа|проверочного\s+окружения', re.I)


def copy_literals(source, suffix):
    if suffix == '.py':
        strings = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Constant) and isinstance(node.value,str)]
    elif suffix == '.html':
        class TextParser(HTMLParser):
            def __init__(self):
                super().__init__(); self.strings=[]; self.ignored=0
            def handle_starttag(self,tag,attrs):
                if tag in {'script','style'}: self.ignored+=1
            def handle_endtag(self,tag):
                if tag in {'script','style'}: self.ignored=max(0,self.ignored-1)
            def handle_data(self,value):
                if not self.ignored and value.strip(): self.strings.append(value)
        parser=TextParser();parser.feed(source);strings=parser.strings
    else:
        strings = [match[1] for match in re.findall(r"(['\"`])((?:\\.|(?!\1)[\s\S])*?)\1",source)]
    return strings


class MiniAppCopyTests(unittest.TestCase):
    def test_copy_gate_catches_fully_english_visible_labels(self):
        values=copy_literals("element('p','','Demo'); action('Mock',callback);",'.js')
        self.assertIn('Demo',values)
        self.assertIn('Mock',values)
        self.assertTrue(all(FORBIDDEN.search(value) for value in values if value in {'Demo','Mock'}))
    def test_normal_user_routes_have_no_prototype_copy_or_owner_payment_controls(self):
        paths = list((ROOT/'bot/mini_app_ui').glob('*.js'))
        paths += [ROOT/'bot/oauth_result.py', ROOT/'bot/legal_documents.py', ROOT/'bot/handlers/payments.py']
        paths += [ROOT/'bot/plan_catalog.py', ROOT/'bot/legal_ui/index.html', ROOT/'bot/mini_app_ui/index.html']
        for path in paths:
            source = path.read_text(encoding='utf-8')
            for value in copy_literals(source,path.suffix):
                with self.subTest(path=path.name,text=value[:100]):
                    self.assertIsNone(FORBIDDEN.search(value))
        for path in (ROOT/'docs/legal').glob('*.md'):
            with self.subTest(document=path.name):
                self.assertIsNone(FORBIDDEN.search(path.read_text(encoding='utf-8')))
        for path in (ROOT/'bot/mini_app_ui').glob('*.js'):
            for endpoint in ('test-checkout','test-confirm','test-refund'):
                self.assertNotIn(endpoint,path.read_text(encoding='utf-8'),path.name)

    def test_active_catalog_and_payment_message_are_exact(self):
        catalog = catalog_payload()
        self.assertEqual({row['product_id']:row['price_label'] for row in catalog['products']},
                         {'viewer_plus':'150 ₽','streamer_plus':'300 ₽'})
        self.assertEqual({row['product_id']:row['rub'] for row in catalog['products']},
                         {'viewer_plus':{'amount_minor':15000,'currency':'RUB'},
                          'streamer_plus':{'amount_minor':30000,'currency':'RUB'}})
        for row in catalog['products']:
            self.assertEqual(row['period_code'],'one_month')
            self.assertEqual(row['period_label'],'1 месяц')
            # Владелец утвердил один месяц без автопродления и цену в звёздах.
            self.assertEqual(row['period_rule'],'30_days')
            self.assertEqual(row['period_rule_version'],'2026-10-05-plus-30-days-v1')
            self.assertFalse(row['auto_renew'])
            self.assertIsNotNone(row['xtr'])
            self.assertEqual(row['xtr']['currency'],'XTR')
            self.assertGreater(row['xtr']['amount_minor'],0)
        self.assertLess(catalog['products'][0]['xtr']['amount_minor'],
                        catalog['products'][1]['xtr']['amount_minor'])
        self.assertEqual(PAYMENT_UNAVAILABLE_MESSAGE,'Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.')
        for path in (ROOT/'bot/mini_app_ui').glob('*'):
            if path.is_file() and path.suffix in {'.js','.css','.html'}:
                self.assertNotRegex(path.read_text(encoding='utf-8'),r'200\s*₽')

    def test_free_reports_and_old_bot_paths_remain_available(self):
        from main import _private_bot_commands
        commands = {command.command for command in _private_bot_commands([],owner=False,growth_enabled=False)}
        self.assertTrue({'start','import_follows','auth_twitch','streamer_connect','myid','paysupport'} <= commands)
        self.assertNotIn('admin',commands)
        self.assertIn('admin',{command.command for command in _private_bot_commands([],owner=True)})
        profile=(ROOT/'bot/mini_app_ui/profile.js').read_text(encoding='utf-8')
        self.assertIn('Отчёты об эфирах',profile)
        self.assertIn("openDetail('reports')",profile)
        reports=(ROOT/'bot/mini_app_ui/reports.js').read_text(encoding='utf-8')
        self.assertIn('Автоотчёт после эфира',reports)
        self.assertIn('Текст + HTML',reports)
        self.assertNotIn('send_message',profile)
        from bot.report import build_report_html
        self.assertTrue(callable(build_report_html))
