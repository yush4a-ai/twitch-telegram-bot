"""Pinned staging, pure read-only copy builders; no database mutations/sends."""
import os,urllib.request,urllib.parse,importlib.util,json
from pathlib import Path
ROOT=Path(r'C:/Users/yusha/Desktop/cloude/TG-BOT.(TwtichSignal)')
pm=urllib.request.getproxies();proxy=pm.get('https');p=urllib.parse.urlsplit(proxy or '')
assert p.hostname in ('127.0.0.1','localhost','::1') and not p.username and not p.password
os.environ['HTTPS_PROXY']=proxy;os.environ['HTTP_PROXY']=pm.get('http',proxy)
spec=importlib.util.spec_from_file_location('ops',ROOT/'docs/audits/telegram-menu-recovery-2026-10-04/twitchsignal-ui-ops.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
before=ops.status();sha=ops._capture(['git','rev-parse','HEAD'])
assert before['staging'][0]['cliMessage']=='staging '+sha
result=ops.remote('''
from bot.telegram_ui import more_keyboard,menu_keyboard
rows=more_keyboard().inline_keyboard
assert [[b.text for b in row] for row in rows]==[['📡 Мои стримеры','🔴 Сейчас в эфире'],['🔔 Настройки','💬 Telegram-каналы'],['⭐ Тариф','❓ Помощь'],['📊 Отчёты'],['← На главную']]
assert [[b.callback_data for b in row] for row in rows]==[['menu:list','menu:live'],['menu:quiet_hours','menu:manage_group'],['menu:plus','menu:help'],['menu:report'],['menu:home']]
keyboard=menu_keyboard()
assert [[b.text for b in row] for row in keyboard.keyboard]==[['Меню']]
assert keyboard.is_persistent is True and keyboard.resize_keyboard is True and keyboard.one_time_keyboard is False
from bot.handlers.telegram_plus import benefits,product_view
from bot.telegram_home import build_home,HomeState
from bot.handlers.streams import _build_report_summary
from bot.plan_catalog import catalog_payload
catalog=catalog_payload()
for p in catalog['products']:
 text=benefits(p)
 assert text.count('<blockquote>')==4 and text.count('</blockquote>')==4
 assert all(b['title'] in text for b in p['benefit_blocks'])
 assert all(catalog['features'][f]['description'] in text for f in p['feature_ids'])
home=build_home(HomeState(41,(('alpha','Game'),),True,26,True,1)).text
assert '<blockquote>' in home and 'https://www.twitch.tv/alpha' in home and '26 из 41' in home
unknown=build_home(HomeState(tracked=41,notifications=0,live_known=False)).text
assert 'Статус эфиров пока недоступен.' in unknown and 'эфиров нет' not in unknown and 'Оповещения о старте выключены.' in unknown
report=_build_report_summary('alpha','<title>','1 ч',100,50,None,10,False,[],[],[],None)
assert '<blockquote>Длительность: 1 ч' in report and '&lt;title&gt;' in report and 'Данные чата: неполные' in report
print('TS_QA_JSON='+json.dumps({'status':'PASS','catalog_groups':[4,4],'menu_markup':'PASS','more_rows':'PASS','existing_live_callback':'menu:live','home':'PASS','unknown':'PASS','report':'PASS','database_mutations':0,'messages_sent':0,'external_payment_calls':0}))
''')
after=ops.status();assert after==before
result.update(sha=sha,deployment=before['staging'][0]['id'],scope='Pure builders on deployed runtime; not native Telegram or actual delivery')
ops.save('STAGING-menu-behavior.json',result)
