"""Telegram Home typography must preserve counts, escaping and honest status."""
import unittest
from html.parser import HTMLParser
from urllib.parse import urlsplit, quote

from bot.telegram_home import HomeState, build_home


class Caption(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.plain = []
        self.bold = []
        self.italic = []
        self.quotes = 0
        self.links = []
        self.feed(source)
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in ('b', 'i', 'blockquote', 'a')
        if tag == 'a':
            assert len(attrs) == 1 and attrs[0][0] == 'href'
            parsed = urlsplit(attrs[0][1])
            assert parsed.scheme == 'https' and parsed.netloc == 'www.twitch.tv'
            self.links.append(attrs[0][1])
        else:
            assert not attrs
        if tag == 'blockquote':
            assert 'blockquote' not in self.stack
            self.quotes += 1
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.plain.append(data)
        if 'b' in self.stack:
            self.bold.append(data)
        if 'i' in self.stack:
            self.italic.append(data)


class HomeCopyTests(unittest.TestCase):
    def test_owner_example_has_hierarchy_without_repeated_brand_or_add_hint(self):
        live = (('derzko69', 'Just Chatting'), ('dmitry_lixxx', 'Counter-Strike'),
                ('hesoyamof1974', 'Delta Force'), *[(f'other{i}', None) for i in range(4)])
        caption = Caption(build_home(HomeState(41, live, True, 26, True, 1)).text)
        self.assertEqual(caption.bold, ['Твои оповещения', '41', '26 из 41', 'В эфире: 7',
                                       'derzko69', 'dmitry_lixxx', 'hesoyamof1974',
                                       'Твой Twitch подключён', '1'])
        self.assertEqual(caption.quotes, 1)
        self.assertEqual(caption.links, ['https://www.twitch.tv/derzko69',
                                        'https://www.twitch.tv/dmitry_lixxx',
                                        'https://www.twitch.tv/hesoyamof1974'])
        self.assertIn('По последней проверке', ''.join(caption.plain))
        self.assertIn('Just Chatting', ''.join(caption.plain))
        self.assertNotIn('Just Chatting', caption.italic)
        text = ''.join(caption.plain)
        self.assertIn('И ещё 4 в эфире', text)
        self.assertIn('Права и публикации: «Я стример».', text)
        self.assertNotIn('TwitchSignalBot', text)
        self.assertNotIn('Добавить стримера можно', text)
        self.assertNotIn('other0', text)

    def test_unknown_and_muted_remain_explicit_and_not_offline(self):
        caption = Caption(build_home(HomeState(tracked=41, notifications=0, live_known=False)).text)
        text = ''.join(caption.plain)
        self.assertIn('Оповещения о старте выключены.', text)
        self.assertIn('Статус эфиров пока недоступен', text)
        self.assertNotIn('эфиров нет', text)
        self.assertNotIn('В эфире: 0', text)

    def test_long_untrusted_text_is_literal_and_fits_caption_after_entities(self):
        login = '<b>fake & name</b>' * 10
        category = '<i>fake & category</i>' * 15
        caption = Caption(build_home(HomeState(200, tuple((login, category) for _ in range(5)),
                                               True, 200, True, 99)).text)
        self.assertEqual(caption.bold.count(login[:60]), 3)
        self.assertEqual(''.join(caption.plain).count(category[:100]), 3)
        self.assertEqual(caption.links, ['https://www.twitch.tv/' + quote(login, safe='')] * 3)
        self.assertLessEqual(len(''.join(caption.plain).encode('utf-16-le')) // 2, 1024)
        self.assertIn('И ещё 2 в эфире', ''.join(caption.plain))

    def test_verified_empty_list_has_compact_home_and_incomplete_connection(self):
        caption = Caption(build_home(HomeState(verified=True, communities=0)).text)
        text = ''.join(caption.plain)
        self.assertNotIn('TwitchSignalBot', text)
        self.assertIn('Пока никого не отслеживаешь.', text)
        self.assertIn('Telegram-канал пока не выбран', text)
        self.assertNotIn('публикации включены', text)

    def test_link_cannot_escape_twitch_origin_or_inject_a_caption_entity(self):
        hostile = '//evil.example/?q="</a><a href="javascript:alert(1)">'
        caption = Caption(build_home(HomeState(tracked=1, live=((hostile, '</blockquote><b>fake'),))).text)
        self.assertEqual(caption.quotes, 1)
        self.assertEqual(caption.links, ['https://www.twitch.tv/' + quote(hostile, safe='')])
        self.assertIn('</blockquote><b>fake', ''.join(caption.plain))
        self.assertNotIn('fake', caption.bold)
