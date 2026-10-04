"""Show current runtime More rows; no Telegram, credentials or database."""
import html
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT))
from bot.telegram_ui import more_keyboard

out=Path(__file__).resolve().parent
rows=[[(b.text,b.callback_data) for b in row] for row in more_keyboard().inline_keyboard]
owner_rows=[[(b.text,b.callback_data or 'owner-only web_app') for b in row]
            for row in more_keyboard(admin_url='https://staging.example.test/admin').inline_keyboard]
def render(groups):
    return ''.join('<div class="row">'+''.join('<button data-action="'+html.escape(action)+'">'+html.escape(text)+'</button>'
                  for text,action in row)+'</div>' for row in groups)
content='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ещё — TwitchSignalBot</title><style>
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:#eef2f5;color:#152330;font:16px/1.4 "Segoe UI",sans-serif}
body.dark{background:#0e1621;color:#fff}main{max-width:460px;margin:auto}h1{font-size:24px;margin:24px 0 16px}
p{font-size:13px;opacity:.75}.row{display:flex;gap:6px;margin-bottom:6px}.row button{flex:1;min-width:0;background:#d9e4ec;color:inherit;border:0;border-radius:9px;padding:14px 6px;font:600 14px/1.4 "Segoe UI",sans-serif;cursor:pointer}
.dark .row button{background:#1e2c3a}.row button:focus-visible{outline:2px solid #2485cf;outline-offset:2px}label{display:inline-flex;gap:6px;margin-right:12px}output{display:block;margin-top:20px;font-size:13px}
</style><main><p>Локальный макет раскладки. Текст и порядок кнопок из кода бота. Это не снимок Telegram.</p>
<label>Тема <select id="theme"><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
<label>Пользователь <select id="role"><option value="viewer">Зритель</option><option value="owner">Владелец</option></select></label>
<h1>Ещё</h1><section id="viewer">'''+render(rows)+'''</section><section id="owner" hidden>'''+render(owner_rows)+'''</section><output aria-live="polite">Кнопки здесь показывают путь; сообщения в Telegram не отправляются.</output>
<script>document.querySelector('#theme').onchange=e=>document.body.classList.toggle('dark',e.target.value==='dark');document.querySelector('#role').onchange=e=>{for(const id of ['viewer','owner'])document.querySelector('#'+id).hidden=id!==e.target.value};for(const button of document.querySelectorAll('[data-action]'))button.onclick=()=>{document.querySelector('output').textContent='Переход в боте: '+button.dataset.action};</script></main></html>'''
(out/'more-preview.html').write_text(content,encoding='utf-8')
(out/'MORE-ROWS.json').write_text(json.dumps({'viewer':rows,'owner':owner_rows,'native':'NOT TESTED','messages_sent':0},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('More preview created; no outbound, native NOT TESTED')
