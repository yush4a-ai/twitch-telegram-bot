"""Render actual read-only handlers through fake sender and isolated in-memory DB."""
import asyncio
from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from bot.database import Database
from bot.handlers import telegram_plus, telegram_help, telegram_streamer, streams
from bot.telegram_home import HomeState,build_home
from bot.telegram_ui import HOME_TEXT
from bot.viewer_trial import ViewerTrialService
from tests.test_telegram_navigation import CONFIG,message

OUT=Path(__file__).resolve().parent

async def collect():
    db=Database(':memory:');await db.connect()
    state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
    frames=[]
    def add(title,text,keyboard=None,image=None):
        buttons=[{'text':b.text,'style':b.style} for row in getattr(keyboard,'inline_keyboard',[]) for b in row]
        frames.append(dict(title=title,text=text,buttons=buttons,image=image))
    async def capture(title,handler,data,*args,**kwargs):
        msg=message();callback=SimpleNamespace(message=msg,from_user=msg.from_user,data=data,answer=AsyncMock())
        await handler(callback,*args,**kwargs)
        call=msg.edit_text.await_args or msg.answer.await_args
        add(title,call.args[0],call.kwargs.get('reply_markup'))
    try:
        add('Первый вход',HOME_TEXT,image='telegram-welcome.png')
        live=(('derzko69','Just Chatting'),('dmitry_lixxx','Counter-Strike'),('hesoyamof1974','Delta Force'),*((f'other{i}',None) for i in range(4)))
        add('Главная',build_home(HomeState(41,live,True,26,True,1)).text,image='telegram-home.png')
        await capture('Тариф зрителя',telegram_plus.cb_plus,'plus:show:viewer_plus',state,db,CONFIG)
        await capture('Тариф стримера',telegram_plus.cb_plus,'plus:show:streamer_plus',state,db,CONFIG)
        start=datetime(2026,10,2,5,28,tzinfo=timezone(timedelta(hours=3))).timestamp()
        await ViewerTrialService(db).start(101,now=start)
        await capture('Моя подписка',telegram_plus.cb_plus,'menu:plus',state,db,CONFIG)
        await capture('Способы оплаты',telegram_plus.cb_buy,'plus:buy:viewer_plus',state,db)
        text,kb=telegram_help.help_screen(CONFIG);add('Помощь',text,kb)
        await capture('Команды',telegram_help.cb_commands,'help:commands',CONFIG)
        for key,(title,_body) in telegram_help.TOPICS.items():
            await capture(title,telegram_help.cb_help_topic,'help:topic:'+key)
        await capture('Подключение Twitch',telegram_streamer.cb_streamer,'menu:streamer',db,CONFIG)
        await db.link_streamer_identity(101,'11','long_streamer_name_2026',verified_at=time.time())
        await capture('Мой Twitch',telegram_streamer.cb_streamer,'menu:streamer',db,CONFIG)
        await capture('Подключение Telegram',telegram_streamer.cb_channel_help,'streamer:channelhelp',state,db)
        quiet_db=SimpleNamespace(get_quiet_hours=AsyncMock(return_value=(1320,420,180,True)))
        text,kb=await streams._quiet_hours_screen_text_and_keyboard(101,quiet_db);add('Тихие часы',text,kb)
        card_db=SimpleNamespace(list_channels_with_routing=AsyncMock(return_value=[('alpha',True,False,None,'short',False,False,False,False,True)]),
            is_telegram_channel=AsyncMock(return_value=False),get_stats_recipient=AsyncMock(return_value=None),
            resolve_post_recipient=AsyncMock(return_value=101),get_quiet_hours=AsyncMock(return_value=None),get_user_token=AsyncMock(return_value=None))
        text,kb=await streams._render_channel_card(101,'alpha',card_db,list_back_callback='menu:list');add('Настройки стримера',text,kb)
        add('Отчёт',streams._build_report_summary('streamer_with_a_long_login','Очень длинное название эфира с игрой, обсуждениями и дополнительными подробностями','3 ч 24 мин',1500,842,None,72,False,[('long_viewer_name',31)],[],[],None,started_at='2026-10-03T10:00:00Z',ended_at=datetime(2026,10,3,13,24,tzinfo=timezone.utc).timestamp()))
        add('Недоступный статус',build_home(HomeState(tracked=41,notifications=0,live_known=False)).text,image='telegram-home.png')
    finally:await db.close()
    return frames

frames=asyncio.run(collect())
(OUT/'PREVIEW-CAPTIONS.json').write_text(json.dumps({'source':'actual runtime builders/handlers; fake sender; isolated in-memory database','native':'NOT TESTED','frames':frames},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
css='''body{margin:0;padding:20px;background:#0e1621;color:#fff;font:16px/1.45 "Segoe UI",sans-serif}body.light{background:#f1f3f5;color:#152330}h1{font-size:22px;margin:0 0 6px}header p{font-size:14px;color:#aebdca;margin:0 0 16px}.light header p{color:#465867}nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}nav button,select{font:inherit;padding:8px 12px;border:1px solid #536575;border-radius:8px;color:inherit;background:transparent}nav button[aria-pressed=true]{background:#2b6b82;color:white}main{max-width:620px;margin:auto}.card{background:#192936;border-radius:12px;overflow:hidden}.light .card{background:white}.card img{width:100%;display:block}.caption{white-space:pre-wrap;padding:16px;overflow-wrap:anywhere}blockquote{margin:6px 0;padding:10px 12px;border-left:3px solid #6ab3f3;background:#243b4e;border-radius:4px}.light blockquote{background:#e9f3fb;border-color:#287fbb}a{color:#82c8ff;text-decoration:none}.light a{color:#0868b2}b{font-weight:700}a:hover{text-decoration:underline}a:focus-visible,button:focus-visible{outline:2px solid #6ab3f3;outline-offset:3px}.buttons{display:grid;gap:4px;margin-top:4px}.buttons span{text-align:center;padding:12px;border-radius:8px;background:#233646}.light .buttons span{background:#dce8f1}.buttons span.primary{background:#2b6b82;color:#fff}.buttons span.success{background:#317a40;color:#fff}.buttons span.danger{background:#af3446;color:#fff}[hidden]{display:none!important}@media(max-width:400px){body{padding:12px}nav{gap:6px}nav button{padding:8px}}'''
nav=[];sections=[]
for i,frame in enumerate(frames):
    nav.append(f'<button type="button" data-screen="{i}" aria-pressed="{str(i==4).lower()}">{html.escape(frame["title"])}</button>')
    image=f'<img src="../../../bot/assets/{frame["image"]}" alt="Одобренная иллюстрация TwitchSignalBot">' if frame['image'] else ''
    buttons=''.join('<span class="'+html.escape(b['style'] or '')+'">'+html.escape(b['text'])+'</span>' for b in frame['buttons'])
    sections.append(f'<section data-view="{i}"'+(' hidden' if i!=4 else '')+'><article class="card">'+image+'<div class="caption">'+frame['text']+'</div></article><div class="buttons">'+buttons+'</div></section>')
script="""document.querySelector('#theme').onchange=e=>document.body.classList.toggle('light',e.target.value==='light');for(const button of document.querySelectorAll('[data-screen]'))button.onclick=()=>{for(const item of document.querySelectorAll('[data-screen]'))item.setAttribute('aria-pressed',String(item===button));for(const view of document.querySelectorAll('[data-view]'))view.hidden=view.dataset.view!==button.dataset.screen;};"""
(OUT/'preview.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Тексты TwitchSignalBot</title><style>'+css+'</style><header><h1>Тексты TwitchSignalBot</h1><p>Макет с демонстрационными данными. Точный текст из приложения; цвет и шрифт нативных блоков определит Telegram.</p><label>Тема <select id="theme"><option value="dark">Тёмная</option><option value="light">Светлая</option></select></label></header><nav>'+''.join(nav)+'</nav><main>'+''.join(sections)+'</main><script>'+script+'</script></html>',encoding='utf-8')
print(json.dumps({'frames':len(frames),'native':'NOT TESTED','messages_sent':0,'external_calls':0,'database':'isolated in-memory fixture'}))
