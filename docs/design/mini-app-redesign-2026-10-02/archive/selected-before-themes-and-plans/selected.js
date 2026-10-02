/* Isolated design fixture. No Telegram SDK, authenticated API, OAuth or sender. */
const icons={search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',plus:'<path d="M12 5v14M5 12h14"/>',arrow:'<path d="m9 5 7 7-7 7"/>',home:'<path d="m3 10 9-7 9 7v10H6V10"/><path d="M10 20v-7h4v7"/>',people:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',person:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/>',channel:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',posts:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>',video:'<rect x="3" y="6" width="12" height="12" rx="3"/><path d="m15 10 6-3v10l-6-3"/>',folder:'<path d="M3 7V5h6l2 3h10v12H3z"/>',check:'<path d="m5 12 4 4L19 6"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',warning:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>'};
const icon=(name,extra='')=>`<svg viewBox="0 0 24 24" aria-hidden="true" ${extra}>${icons[name]}</svg>`;
const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sample=[
 {name:'Twitch',login:'twitch',image:'assets/twitch.png',status:'live',category:'Just Chatting',notify:true,video:true},
 {name:'Александр и его невероятные приключения после полуночи',login:'alexander_after_midnight',initial:'А',status:'live',category:'Baldur’s Gate 3',notify:true,video:true},
 {name:'Monstercat',login:'monstercat',image:'assets/monstercat.png',status:'offline',notify:true,video:true},
 {name:'Катерина играет в длинные сюжетные игры без спешки',login:'katerina_story_games',initial:'К',status:'offline',notify:false,video:false},
 {name:'Михаил — ночные эфиры и разговоры о кино',login:'mikhail_night_streams',initial:'М',status:'stale',notify:true,video:false},
 {name:'ОченьДлинноеИмяСтримераБезПробеловДляПроверкиМноготочия',login:'very_long_streamer',status:'stale',notify:true,video:false},
];
const catalog=[...sample,{name:'София. Новые игры и разговоры после эфира',login:'sofia_new_games',initial:'С',status:'offline',notify:true,video:false}];
const query=new URLSearchParams(location.search), isSingle=query.get('single')==='1';
let gallery=query.get('view')||'main', singleScreen=query.get('screen')||'viewer';
let singleMode=singleScreen.startsWith('channel')?'streamer':'viewer';
const displayByKey=new Map(), channelOverrides=new Map(), checkRequests=new Map();
const model={viewer:query.get('viewer')||'filled',channel:query.get('channel')||'rights',plan:query.get('plan')||'plus',twitchLinked:true,search:'',draft:'',result:null,lookupError:'',lookupBusy:false,lookupVersion:0,selectionPending:false};
let subscriptions=[];
function resetSubscriptions(){subscriptions=model.viewer==='empty'?[]:sample.map(s=>({...s}));if(model.viewer==='large')subscriptions.push(...Array.from({length:194},(_,i)=>({name:`Длинное имя стримера ${i+7} — игры, разговоры и вечерние приключения`,login:`demo_streamer_${i+7}`,initial:String(i+7),status:i%5===0?'stale':'offline',notify:i%7!==0,video:false})));}
resetSubscriptions();
let themeChoice='light';try{themeChoice=localStorage.getItem('tsb_selected_design_theme')||'light';}catch{}
if(!['light','dark','telegram'].includes(themeChoice))themeChoice='light';
if(['light','dark','telegram'].includes(query.get('theme')))themeChoice=query.get('theme');
const systemTheme=matchMedia('(prefers-color-scheme: dark)');
let telegramTheme=query.get('telegram')||(systemTheme.matches?'dark':'light');
const routes=new Map(), scrolls=new Map(), failures=new Map();
if(query.get('route')==='subscription')routes.set('profile',{kind:'subscription'});
const root=document.getElementById('concepts'), dialog=document.getElementById('detail');
if(isSingle)document.body.classList.add('single');
function applyTheme(){document.documentElement.dataset.theme=themeChoice==='telegram'?telegramTheme:themeChoice;}
applyTheme();
systemTheme.addEventListener('change',event=>{telegramTheme=event.matches?'dark':'light';document.getElementById('telegram-scenario').value=telegramTheme;if(themeChoice==='telegram')applyTheme();});
function avatar(s){return `<span class="avatar" aria-hidden="true">${s.image?`<img src="${s.image}" alt="" loading="lazy" onerror="this.parentNode.textContent='${s.initial||s.name[0]}';">`:s.initial||icon('person')}</span>`;}
function mode(kind){return `<header class="screen-header"><div class="mode" role="group" aria-label="Режим приложения"><button data-mode="viewer" aria-pressed="${kind==='viewer'}">Зритель</button><button data-mode="streamer" aria-pressed="${kind==='streamer'}">Стример</button></div></header>`;}
function tabs(kind,active){const entries=kind==='viewer'?[['home','Главная'],['people','Стримеры'],['person','Профиль']]:[['channel','Мой канал'],['posts','Посты'],['person','Профиль']];return `<nav class="tabbar" aria-label="Разделы">${entries.map(([glyph,label],i)=>`<button data-tab="${glyph}" ${i===active?'aria-current="page"':''}>${icon(glyph)}${label}</button>`).join('')}</nav>`;}
function row(s){
 const state=s.status==='live'?'В эфире':s.status==='offline'?'Не в эфире':'Нет свежих данных';
 const selected=s.video&&model.plan==='plus';
 const visual=s.status==='live'?(s.category||'Категория не указана'):state;
 const accessible=[s.name,state,s.status==='live'?s.category:'',selected?'Видео выбрано; готовность не подтверждена':'Фото',s.notify?'Уведомления включены':'Уведомления на паузе','Открыть настройки'].filter(Boolean).join('. ');
 return `<button class="stream-row" data-streamer="${s.login}" aria-label="${esc(accessible)}">${avatar(s)}<span class="row-copy"><strong>${esc(s.name)}</strong><span class="status" aria-hidden="true">${esc(visual)}${selected?' · Видео выбрано':''}</span>${!s.notify?'<span class="pause" aria-hidden="true">Уведомления на паузе</span>':''}</span>${icon('arrow','class="chevron"')}</button>`;
}
function list(){const q=model.search.toLocaleLowerCase('ru');const matches=subscriptions.filter(s=>(s.name+' '+s.login).toLocaleLowerCase('ru').includes(q));if(!matches.length)return '<div class="empty-search"><h2>Нет совпадений</h2><p>Попробуй другое имя или очисти поиск.</p><button class="primary" data-clear>Очистить поиск</button></div>';return [['live','В эфире'],['offline','Не в эфире'],['stale','Нет свежих данных']].map(([status,label])=>{const items=matches.filter(s=>s.status===status);return items.length?`<section data-group="${status}"><div class="section-label"><b>${label}</b><span class="numbers">${items.length}</span></div><div class="group">${items.map(row).join('')}</div></section>`:'';}).join('');}
function addForm(){return `<form class="add-form" data-add-form><label class="field">Ник или ссылка Twitch<span class="input-wrap"><input name="twitch" type="text" autocomplete="off" spellcheck="false" placeholder="Например, twitch.tv/twitch" value="${esc(model.draft)}" aria-label="Ник или ссылка Twitch"></span></label><button class="primary" type="submit" ${model.lookupBusy?'aria-disabled="true"':''}>${model.lookupBusy?'Ищем стримера…':'Найти стримера'}</button><div class="lookup-status" aria-live="polite">${model.lookupError?`<p class="notice error">${esc(model.lookupError)}</p>`:''}${model.result?`<div class="result"><div class="identity-row">${avatar(model.result)}<div class="row-copy"><strong>${esc(model.result.name)}</strong><span class="status">${esc(model.result.login)}</span></div></div>${subscriptions.some(s=>s.login===model.result.login)?'<p class="notice">Уже в твоём списке</p>':'<button class="primary" type="button" data-confirm-add>Добавить стримера</button>'}</div>`:''}</div></form>`;}
function streamerDetail(s){return `<h1 tabindex="-1">Стример</h1><div class="detail-name"><h2>${esc(s.name)}</h2><p class="muted">${esc(s.login)}</p></div><div class="group detail-info"><p>${s.status==='stale'?'Нет свежих данных о трансляции':s.status==='live'?'В эфире':'Не в эфире'}</p><p>Уведомления ${s.notify?'включены':'на паузе'}</p><button class="text-button" data-toggle-notify="${s.login}">${s.notify?'Приостановить уведомления':'Включить уведомления'}</button><p>Превью: ${s.video&&model.plan==='plus'?'Видео выбрано · готовность не подтверждена':'Фото'}</p><button class="text-button" data-dialog="streamer-options">Фильтры и напоминания</button></div><button class="secondary detail-return" data-back>Вернуться к списку</button>`;}
function viewerTools(){
 const plus=model.plan==='plus', selected=subscriptions.filter(s=>s.video).length;
 const count=model.plan==='free'&&subscriptions.length>50?'50 активных · '+subscriptions.length+' сохранено':subscriptions.length+' из '+(plus?200:50);
 return `<div class="list-tools"><div class="tool-actions"><button class="text-button" data-dialog="folders">${icon('folder')}Папки</button><button class="text-button" data-dialog="video" aria-label="${plus?'Видеопревью: выбрано '+selected+' из 5':'О видеопревью в Viewer Plus'}">${icon('video')}${plus?'Видео '+selected+'/5':'Видео · Plus'}</button></div><span class="small muted numbers">${count}</span></div>`;
}
function viewer(key){
 const route=routes.get(key);
 if(route?.kind==='detail'){const s=subscriptions.find(item=>item.login===route.login);if(s)return `${mode('viewer')}<main class="screen-content" data-route="detail">${streamerDetail(s)}</main>${tabs('viewer',1)}`;}
 if(route?.kind==='add')return `${mode('viewer')}<main class="screen-content" data-route="add"><div class="title-row"><h1 tabindex="-1">Добавить стримера</h1></div>${addForm()}<button class="secondary detail-return" data-back>Вернуться к списку</button></main>${tabs('viewer',1)}`;
 const empty=!subscriptions.length;
 return `${mode('viewer')}<main class="screen-content" data-route="list"><div class="title-row"><h1 tabindex="-1">Стримеры</h1>${empty?'':`<button class="text-button" data-add>${icon('plus')}Добавить</button>`}</div>${empty?`${viewerTools()}<div class="empty-intro"><h2>Кого будем смотреть?</h2><p>Добавь первого стримера. Сообщу в Telegram, когда он начнёт эфир.</p></div>${addForm()}<p class="notice">Уведомления и фото бесплатны.<br>До 50 стримеров без подписки.</p>`:`<label class="field">Поиск по моим стримерам<span class="input-wrap">${icon('search')}<input type="search" data-search aria-label="Поиск по моим стримерам" placeholder="Имя или ник" value="${esc(model.search)}"></span></label>${viewerTools()}<div data-list>${list()}</div>`}</main>${tabs('viewer',1)}`;
}
const botName=()=>'<button class="bot-name" data-copy-bot aria-label="Скопировать имя @TwitchSignalTestbot">@TwitchSignalTestbot</button>';
const channelFixture=query.get('channelLink')==='public'?{type:'channel',verified:true,title:'Telegram',url:'https://t.me/telegram'}:query.get('channelLink')==='invalid'?{type:'channel',verified:true,title:'Эфиры Александра',url:'javascript:alert(1)'}:{type:'channel',verified:false,title:'Эфиры Александра и разговоры после полуночи',url:null};
function validatedChannelLink(meta){
 if(meta?.type!=='channel'||meta.verified!==true||typeof meta.url!=='string')return null;
 try{const url=new URL(meta.url);return url.protocol==='https:'&&url.hostname==='t.me'&&!url.username&&!url.password&&!url.port&&!url.search&&!url.hash&&/^\/[a-zA-Z][a-zA-Z0-9_]{4,31}$/.test(url.pathname)?url.href:null;}catch{return null;}
}
const permissionStates={
 absent:{tone:'warning',title:'Добавь бота в канал',text:'Бот ещё не состоит в выбранном канале.',instruction:()=>`<h3>В настройках канала</h3><p>Администраторы → Добавить администратора → найди ${botName()}.</p><p>Включи «Публикация сообщений» и сохрани.</p>`,button:'Проверить подключение'},
 member:{tone:'warning',title:'Назначь бота администратором',text:'Бот уже в канале, но пока не может публиковать.',instruction:()=>`<h3>Измени роль бота</h3><p>Настройки канала → Администраторы → Добавить администратора → ${botName()}.</p><p>Разреши «Публикация сообщений» и сохрани.</p>`,button:'Проверить подключение'},
 rights:{tone:'warning',title:'Разреши публикации',text:'Бот — администратор. Не хватает только права публикации.',instruction:()=>`<h3>Измени разрешение</h3><p>Настройки канала → Администраторы → ${botName()}.</p><p>Включи «Публикация сообщений» и сохрани.</p>`,button:'Проверить подключение'},
 checking:{tone:'neutral',title:'Проверяем права',text:'Ждём ответа Telegram. Подключение пока не подтверждено.',instruction:()=>'<h3>Выбранный канал сохранён</h3><p>Проверяем роль бота и право публикации. Отдельный пост сейчас не отправляется.</p>',button:'Проверяем права…'},
 ready:{tone:'success',title:'Канал подключён',text:'Проверка прав завершена. Публикации ещё выключены.',instruction:()=>'<h3>Сначала посмотри пост</h3><p>Покажем стандартный пост о начале эфира. Публикации включишь отдельным действием.</p>',button:'Посмотреть пример поста'},
 network:{tone:'error',title:'Не удалось проверить права',text:'Telegram не ответил. Это не подтверждает отсутствие прав.',instruction:()=>'<h3>Повтори проверку</h3><p>Канал сохранили. Проверь соединение и попробуй ещё раз.</p>',button:'Повторить проверку'},
 none:{tone:'neutral',title:'Выбери Telegram-канал',text:'Туда бот будет публиковать сообщения о твоих эфирах.',instruction:()=>'<h3>Следующий шаг</h3><p>Выбери канал, которым ты управляешь. Затем проверим права бота.</p>',button:'Выбрать канал'},
 cancel:{tone:'neutral',title:'Выбор канала отменён',text:'Twitch остаётся подключённым. Можно повторить выбор.',instruction:()=>'<h3>Продолжи, когда будет удобно</h3><p>Добавленные ранее каналы и группы сохранились.</p>',button:'Выбрать канал'},
 expired:{tone:'warning',title:'Запрос выбора устарел',text:'Создай новый запрос. Twitch подключать заново не нужно.',instruction:()=>'<h3>Повтори выбор канала</h3><p>Сохранённые подключения остаются на месте.</p>',button:'Выбрать канал'},
};
function channel(key,initialState){
 if(!model.twitchLinked)return `${mode('streamer')}<main class="screen-content"><h1 tabindex="-1">Мой канал</h1><div class="empty-intro"><h2>Подключи Twitch</h2><p>Подключение не подтверждено. Выбор Telegram-канала будет следующим шагом.</p></div><button class="primary" data-dialog="oauth-retry">Подключить Twitch</button></main>${tabs('streamer',0)}`;
 const route=routes.get(key);
 if(route?.kind==='post')return `${mode('streamer')}<main class="screen-content" data-route="post"><div class="title-row"><h1 tabindex="-1">Пример поста</h1></div><div class="group detail-info"><h2>Twitch начинает эфир</h2><p class="notice">Условный пример стандартного уведомления. Сообщение не отправлено.</p></div><p class="notice">В приложении здесь будет явное включение публикаций после проверки прав.</p><button class="secondary" data-back>Вернуться к подключению</button></main>${tabs('streamer',0)}`;
 if(route?.kind==='fallback')return `${mode('streamer')}<main class="screen-content" data-route="fallback"><h1 tabindex="-1">Выбор через переписку</h1><p class="notice">Пример перехода, без отправки сообщения.</p><div class="instruction"><p>В переписке с ${botName()} появится кнопка выбора. Выбери канал и вернись в приложение.</p><button class="primary" data-demo-chat-pick>Выбрать Telegram-канал</button></div><p class="notice" data-fallback-status>${model.selectionPending?'Канал выбран. Можно вернуться.':'Выбор ещё не завершён.'}</p><button class="secondary" data-cancel-selection>Отменить</button><p class="copy-status" role="status" data-copy-status></p></main><div class="connection-actions"><button class="primary" data-return-selection ${model.selectionPending?'':'disabled'}>Вернуться к подключению</button></div>${tabs('streamer',0)}`;
 const state=channelOverrides.get(key)||initialState, config=permissionStates[state]||permissionStates.rights;
 const selected=!['none','cancel','expired'].includes(state), link=selected?validatedChannelLink(channelFixture):null;
 const glyph=state==='ready'?'check':state==='checking'?'clock':config.tone==='neutral'?'channel':'warning';
 return `${mode('streamer')}<main class="screen-content" data-route="channel" data-channel-state="${state}"><h1 tabindex="-1">Telegram-канал</h1><div class="steps" aria-label="Twitch подключён. Текущий шаг: Telegram-канал. Затем публикации."><span>${icon('check')}Twitch</span>${icon('arrow','class="chevron"')}<span class="current">Telegram</span>${icon('arrow','class="chevron"')}<span>Публикации</span></div><div class="group"><div class="identity-row">${avatar(sample[0])}<span class="row-copy"><strong>Twitch</strong><span class="status">Аккаунт подключён</span></span>${icon('check')}</div>${selected?`<div class="identity-row"><span class="avatar channel">${icon('channel')}</span><span class="row-copy"><strong>${esc(channelFixture.title)}</strong><span class="status">Telegram-канал выбран</span>${link?`<a class="text-button open-channel" href="${esc(link)}" target="_blank" rel="noopener noreferrer">Открыть канал</a>`:''}</span></div>`:''}</div><section class="permission" data-tone="${config.tone}" aria-live="polite"><h2>${icon(glyph)}${config.title}</h2><p>${config.text}</p></section><div class="instruction">${config.instruction()}</div><p class="copy-status" role="status" data-copy-status></p>${selected?'<button class="secondary select-another" data-select-channel>Выбрать другой канал</button>':''}<p class="notice">Стандартные публикации бесплатны</p></main><div class="connection-actions"><button class="primary" data-channel-action="${state}" ${state==='checking'?'aria-disabled="true"':''}>${config.button}</button></div>${tabs('streamer',0)}`;
}

function setting(label,value,action){return `<button class="setting" data-dialog="${action}"><span>${label}</span><span class="value">${value}${icon('arrow','class="chevron"')}</span></button>`;}
function subscription(key,kind){
 const plus=model.plan==='plus';
 return `${mode(kind)}<main class="screen-content" data-route="subscription"><div class="title-row"><h1 tabindex="-1">Управление подпиской</h1></div><button class="text-button" data-back>Вернуться в профиль</button><section class="group detail-info subscription-plan"><div class="plan-heading"><h2>${plus?'Viewer Plus':'Free'}</h2><span>${plus?'Активен':'Бесплатно'}</span></div><p class="notice">${plus?'Тестовый доступ до 9 октября 2026. Без оплаты и автосписания.':'Подписка Viewer Plus не активна. Бесплатные уведомления и фото доступны.'}</p><div class="plan-stats"><span>Стримеры</span><span>${Math.min(subscriptions.length,plus?200:50)} / ${plus?200:50}</span>${plus?`<span>Видео выбрано</span><span>${subscriptions.filter(s=>s.video).length} / 5</span>`:''}</div></section><details class="subscription-features"><summary>Возможности Viewer Plus</summary><p>До 200 стримеров, фильтры, смена категории, напоминания 15/30 минут, папки, история и 5 выбранных видеопревью, включая тех, кто не в эфире.</p></details><section class="operation-history"><h2>Мои операции</h2><div class="group detail-info"><p>${plus?'2 октября 2026 · тестовый доступ активирован':'Операций пока нет'}</p></div></section><p class="notice">Streamer Plus — отдельная подписка для оформления публикаций.</p></main>${tabs(kind,2)}`;
}
function profile(kind='viewer',key='profile'){
 if(routes.get(key)?.kind==='subscription')return subscription(key,kind);
 const plus=model.plan==='plus';
 return `${mode(kind)}<main class="screen-content" data-route="profile"><div class="title-row"><h1 tabindex="-1">Профиль</h1></div><div class="profile-identity"><span class="avatar">АЛ</span><div class="row-copy"><strong>Александра Лебедева‑Константинопольская</strong><p>@alexandra_streams</p></div></div><div class="group"><div class="plan"><div class="plan-heading"><h2>${plus?'Viewer Plus':'Free'}</h2><span>${plus?'Активен':'Бесплатно'}</span></div>${plus?'<p>Тестовый доступ до 9 октября 2026</p>':''}<div class="plan-stats"><span>Стримеры</span><span>${Math.min(subscriptions.length,plus?200:50)} / ${plus?200:50}</span>${plus?`<span>Видео выбрано</span><span>${subscriptions.filter(s=>s.video).length} / 5</span>`:''}</div>${setting('Управление подпиской','','subscription')}</div>${setting('Streamer Plus','Не активен','streamer-plus')}</div><div class="section-label"><b>Настройки</b></div><div class="group"><div class="theme-control"><h3>Оформление</h3><div class="theme-choices" role="group" aria-label="Оформление">${[['light','Светлая'],['dark','Тёмная'],['telegram','Как в Telegram']].map(([id,label])=>`<button data-theme-choice="${id}" aria-pressed="${themeChoice===id}">${label}</button>`).join('')}</div></div>${setting('Тихие часы','23:00–08:00','quiet')}</div><div class="section-label"><b>Мои данные</b></div><div class="group">${setting('Итоги эфиров','','reports')}${setting('История событий',plus?'':'Plus','history')}</div><div class="section-label"><b>Поддержка</b></div><div class="group">${setting('Помощь','','help')}</div></main>${tabs(kind,2)}`;
}
function oauth(kind){const titles={success:'Twitch подключён',error:'Не удалось подключить Twitch',cancel:'Подключение отменено'};const text={success:'Теперь подключи Telegram-канал, куда бот будет публиковать сообщения о начале эфира.',error:'Проверка подключения не завершилась. Попробуй ещё раз или вернись в приложение.',cancel:'Twitch не подключён. Сохранённые настройки не изменились.'};return `<main class="screen-content"><div class="oauth-top">${icon('channel')}TwitchSignal</div><h1 tabindex="-1">${titles[kind]}</h1><p>${text[kind]}</p>${kind==='success'?`<div class="identity-row">${avatar(sample[0])}<span class="row-copy"><strong>Twitch</strong><span class="status">Подтверждённый аккаунт · пример</span></span>${icon('check')}</div>`:''}</main><div class="oauth-actions"><button class="primary" data-oauth-return="${kind}">Вернуться в приложение</button>${kind==='error'?'<button class="secondary" data-dialog="oauth-retry">Попробовать снова</button>':''}</div>`;}
function focusToken(element=document.activeElement){
 const screen=element?.closest?.('.screen');if(!screen)return null;
 const attributes=['data-streamer','data-dialog','data-theme-choice','data-tab','data-mode','data-add','data-back','data-channel-action','data-select-channel','data-copy-bot','data-search','data-confirm-add'];
 let selector=null;
 for(const attr of attributes)if(element.hasAttribute(attr)){selector=attr==='data-channel-action'?'[data-channel-action]':`[${attr}="${CSS.escape(element.getAttribute(attr))}"]`;break;}
 if(!selector&&element.matches('input[name]'))selector=`input[name="${CSS.escape(element.name)}"]`;
 if(!selector&&element.matches('button[type=submit]'))selector='button[type=submit]';
 if(!selector)return null;
 return {key:screen.dataset.key,route:element.closest('.screen-content')?.dataset.route||null,selector};
}
function restoreFocus(token){
 if(!token)return false;
 const screen=root.querySelector(`.screen[data-key="${CSS.escape(token.key)}"]`), element=screen?.querySelector(token.selector);
 if(!element||element.disabled)return false;
 if(token.route&&(element.closest('.screen-content')?.dataset.route||null)!==token.route)return false;
 element.focus({preventScroll:true});return true;
}
function focusHeading(key){const h=root.querySelector(`.screen[data-key="${CSS.escape(key)}"] h1`);h?.focus({preventScroll:true});}

function captureScrolls(){document.querySelectorAll('.screen').forEach(s=>{const content=s.querySelector('.screen-content'),route=content?.dataset.route||'main';scrolls.set(s.dataset.key+':'+route,content?.scrollTop||0);});}
function mainView(key){const display=displayByKey.get(key)||{type:key,kind:isSingle?singleMode:key==='channel'?'streamer':'viewer'};return display.type==='viewer'?viewer(key):display.type==='channel'?channel(key,model.channel):profile(display.kind,key);}
function showMainScreen(target,key,kind){gallery='main';if(isSingle){singleScreen=target;singleMode=kind;}else displayByKey.set(key,{type:target,kind});render();focusHeading(isSingle?singleScreen:key);}
function returnToChannel(){channelOverrides.clear();checkRequests.clear();gallery='main';singleScreen='channel';singleMode='streamer';displayByKey.clear();routes.delete('channel');render(['channel']);focusHeading('channel');}
function render(resetKeys=[]){const beforeFocus=focusToken();captureScrolls();let entries=gallery==='oauth'?[['oauth-success','Успех',()=>oauth('success')],['oauth-error','Ошибка',()=>oauth('error')],['oauth-cancel','Отмена',()=>oauth('cancel')]]:gallery==='channel-states'?[['channel-absent','Бот не добавлен',()=>channel('channel-absent','absent')],['channel-member','Бот не администратор',()=>channel('channel-member','member')],['channel-rights','Нет права публикации',()=>channel('channel-rights','rights')]]:gallery==='channel-progress'?[['channel-none','Канал не выбран',()=>channel('channel-none','none')],['channel-checking','Проверяем права',()=>channel('channel-checking','checking')],['channel-cancel','Выбор отменён',()=>channel('channel-cancel','cancel')]]:gallery==='channel-results'?[['channel-ready','Всё готово',()=>channel('channel-ready','ready')],['channel-network','Ошибка связи',()=>channel('channel-network','network')],['channel-expired','Запрос устарел',()=>channel('channel-expired','expired')]]:[['viewer','Стримеры',()=>mainView('viewer')],['channel','Telegram-канал',()=>mainView('channel')],['profile','Профиль и подписка',()=>mainView('profile')]];
if(isSingle){if(!entries.some(([key])=>key===singleScreen))singleScreen=entries[0][0];entries=entries.filter(([key])=>key===singleScreen);}
root.innerHTML=`<section class="board"><div class="board-heading">Выбранный А · ${document.documentElement.dataset.theme==='dark'?'тёмная':'светлая'} тема · видимая область без системных панелей</div><div class="screen-grid">${entries.map(([key,label,view])=>`<article class="preview"><div class="screen-label"><strong>${label}</strong><span>390 × 844</span></div><div class="screen ${key.startsWith('oauth')?'oauth':''}" data-key="${key}">${view()}</div><p class="preview-note">${key.startsWith('oauth')?'Страница завершения Twitch · пример':'Прокручиваемый экран · локальные данные'}</p></article>`).join('')}</div></section>`;
document.querySelectorAll('.screen').forEach(s=>{const key=s.dataset.key,route=s.querySelector('.screen-content').dataset.route||'main';s.querySelector('.screen-content').scrollTop=resetKeys.includes(key)?0:scrolls.get(key+':'+route)||0;});
document.querySelectorAll('[data-gallery]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.gallery===gallery)));restoreFocus(beforeFocus);}
function navigate(key,route,opener=document.activeElement){
 captureScrolls();const returnFocus=focusToken(opener), previous=route.kind==='detail'?null:routes.get(key)||null;
 routes.set(key,{...route,previous,returnFocus:route.kind==='detail'?{key,route:'list',selector:`[data-streamer="${CSS.escape(route.login)}"]`}:returnFocus});render([key]);focusHeading(key);
}
function back(key){
 captureScrolls();model.lookupVersion++;model.lookupBusy=false;
 const route=routes.get(key);if(route?.previous)routes.set(key,route.previous);else routes.delete(key);
 render();if(!restoreFocus(route?.returnFocus))focusHeading(key);
}
const details={folders:['Папки','Сохранённые папки и общие правила останутся отдельным экраном Viewer Plus. Удаление папки не удаляет стримеров.'],video:['Видеопревью','До пяти выбранных стримеров, включая тех, кто не в эфире. Выбор не подтверждает готовность ролика. Шестой требует явной замены. Полный выбор будет перенесён из приложения.'],subscription:['Управление подпиской',`План, срок, возможности и история собственных операций. В этом примере тестовый доступ без оплаты. Подтверждение и отзыв доступны только в отдельном защищённом разделе владельца.`],'streamer-plus':['Streamer Plus','Отдельный продукт: оформление постов, кнопки, живое превью и подтверждённые публикации. Бесплатное подключение канала сохраняется.'],quiet:['Тихие часы','Существующая бесплатная настройка: с 23:00 до 08:00.'],reports:['Итоги эфиров','Существующие итоги и HTML-экспорт сохраняются. Новый аналитический движок не добавляется.'],history:['История событий','Собственные исходы уведомлений. Доступ остаётся по текущим правилам Viewer Plus.'],help:['Помощь','Уведомления, подключение и восстановление доступа.'], 'streamer-options':['Настройки стримера','Фильтры, категории, напоминания 15/30 минут и выбор превью сохранятся при переносе дизайна.'],'oauth-retry':['Повтор Twitch','В рабочем приложении начнётся новый защищённый запрос. Здесь OAuth не выполняется.']};
let dialogOpener=null, dialogRestore=true, interactionOpener=null;
function showDialog(title,text,extra='',options={}){
 if(!dialog.open)dialogOpener=focusToken(interactionOpener)||focusToken();
 dialog.classList.toggle('sheet',!!options.sheet);
 dialog.querySelector('.dialog-content').innerHTML=`<h2 id="dialog-title" tabindex="-1">${esc(title)}</h2><p id="dialog-description">${esc(text)}</p>${extra}<p class="notice">Локальный пример. Данные бота не меняются.</p>`;
 if(!dialog.open)dialog.showModal();dialog.querySelector('h2').focus({preventScroll:true});
}
function closeDialog(transition=false){dialogRestore=!transition;dialog.close();}
dialog.addEventListener('close',()=>{if(dialogRestore)restoreFocus(dialogOpener);dialogRestore=true;});
dialog.addEventListener('keydown',event=>{
 if(event.key!=='Tab')return;
 const items=[...dialog.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')].filter(element=>element.getClientRects().length);
 if(!items.length){event.preventDefault();dialog.querySelector('h2').focus();return;}
 const current=items.indexOf(document.activeElement);
 if(current<0||(event.shiftKey&&current===0)||(!event.shiftKey&&current===items.length-1)){
  event.preventDefault();items[event.shiftKey?items.length-1:0].focus();
 }
});
function chooseChannel(key){dialog.dataset.key=key;showDialog('Выбрать Telegram-канал','В приложении откроется встроенный выбор Telegram. Если он недоступен, перейдём в переписку с ботом.',`<button class="primary" data-native-pick>Пример встроенного выбора</button><button class="secondary" data-chat-transition>Пример перехода в переписку</button>`,{sheet:true});}

document.addEventListener('input',event=>{if(event.target.matches('[data-search]')){model.search=event.target.value.trim();const box=event.target.closest('.screen').querySelector('[data-list]');const content=box.closest('.screen-content'),top=content.scrollTop;box.innerHTML=list();content.scrollTop=top;}if(event.target.name==='twitch')model.draft=event.target.value;});
document.addEventListener('submit',async event=>{if(!event.target.matches('[data-add-form]'))return;event.preventDefault();if(model.lookupBusy)return;model.lookupError='';model.result=null;const value=model.draft.trim().replace(/^https?:\/\/(www\.)?twitch\.tv\//i,'').replace(/\/$/,'').toLowerCase();if(!/^[a-z0-9_]{3,25}$/.test(value)){model.lookupError='Введи Twitch-ник или ссылку на канал.';render();return;}model.lookupBusy=true;const version=++model.lookupVersion;const key=event.target.closest('.screen').dataset.key;render();await new Promise(resolve=>setTimeout(resolve,350));if(version!==model.lookupVersion)return;model.lookupBusy=false;if(failures.get('lookup')){failures.delete('lookup');model.lookupError='Не удалось найти стримера. Ввод сохранили — попробуй ещё раз.';}else{model.result=catalog.find(s=>s.login===value)||null;if(!model.result)model.lookupError='В этом примере стример не найден. Попробуй twitch, monstercat или sofia_new_games.';}render();});
document.addEventListener('click',async event=>{const button=event.target.closest('button');if(!button)return;interactionOpener=button;const screen=button.closest('.screen'),key=screen?.dataset.key;
if(button.dataset.gallery){gallery=button.dataset.gallery;routes.clear();displayByKey.clear();channelOverrides.clear();checkRequests.clear();render();return;}
if(button.hasAttribute('data-close')){closeDialog();return;}
if(button.dataset.themeChoice){themeChoice=button.dataset.themeChoice;try{localStorage.setItem('tsb_selected_design_theme',themeChoice);}catch{}applyTheme();render();return;}
if(button.hasAttribute('data-clear')){model.search='';render();return;}
if(button.hasAttribute('data-add')){navigate(key,{kind:'add'},button);return;}
if(button.dataset.streamer){navigate(key,{kind:'detail',login:button.dataset.streamer},button);return;}
if(button.hasAttribute('data-back')){back(key);return;}
if(button.dataset.toggleNotify){const s=subscriptions.find(item=>item.login===button.dataset.toggleNotify);s.notify=!s.notify;render();return;}
if(button.hasAttribute('data-confirm-add')){if(!model.result)return;if(subscriptions.length>=(model.plan==='free'?50:200)){model.lookupError='Достигнут предел '+(model.plan==='free'?50:200)+' активных стримеров.';render();return;}const s={...model.result};if(!subscriptions.some(item=>item.login===s.login))subscriptions.push(s);model.result=null;navigate(key,{kind:'detail',login:s.login},button);return;}
if(button.dataset.dialog){
if(button.dataset.dialog==='video'&&model.plan==='free'){showDialog('Видео в Viewer Plus','В Viewer Plus можно выбрать до пяти стримеров с видеопревью. В Free остаются фото. Выбор не означает, что видео уже подготовлено.');return;}
if(button.dataset.dialog==='subscription'){navigate(key,{kind:'subscription'},button);return;}
const [title,text]=details[button.dataset.dialog];showDialog(title,text,button.dataset.dialog==='reports'?'<button class="secondary" data-export>HTML-экспорт</button>':'');return;
}
if(button.hasAttribute('data-export')){showDialog('HTML-экспорт','В рабочем приложении откроется существующий экспорт с прежними правами. Генератор не изменён.');return;}
if(button.hasAttribute('data-select-channel')){chooseChannel(key);return;}
if(button.hasAttribute('data-native-pick')){closeDialog(true);model.channel='rights';returnToChannel();return;}
if(button.hasAttribute('data-chat-transition')){const target=dialog.dataset.key;closeDialog(true);model.selectionPending=false;navigate(target,{kind:'fallback'});return;}
if(button.hasAttribute('data-demo-chat-pick')){model.selectionPending=true;render();return;}
if(button.hasAttribute('data-return-selection')){model.channel='rights';model.selectionPending=false;returnToChannel();return;}
if(button.hasAttribute('data-cancel-selection')){model.channel='cancel';model.selectionPending=false;returnToChannel();return;}
if(button.dataset.channelAction){
 const state=button.dataset.channelAction;
 if(['none','cancel','expired'].includes(state)){chooseChannel(key);return;}
 if(state==='ready'){navigate(key,{kind:'post'},button);return;}
 if(state==='checking')return;
 const version=(checkRequests.get(key)||0)+1;checkRequests.set(key,version);
 channelOverrides.set(key,'checking');render();
 await new Promise(resolve=>setTimeout(resolve,350));
 if(checkRequests.get(key)!==version)return;
 const outcome=failures.get('check-network')?'network':state;
 failures.delete('check-network');channelOverrides.set(key,outcome);render();return;
}
if(button.hasAttribute('data-copy-bot')){
 const status=screen?.querySelector('[data-copy-status]');
 try{if(!navigator.clipboard?.writeText)throw new Error('Clipboard unavailable');await navigator.clipboard.writeText('@TwitchSignalTestbot');if(status?.isConnected){status.textContent='Имя бота скопировано';status.classList.remove('error');}}
 catch{if(status?.isConnected){status.textContent='Не удалось скопировать. Выдели имя и скопируй вручную.';status.classList.add('error');}}return;
}
if(button.dataset.oauthReturn){model.twitchLinked=button.dataset.oauthReturn==='success';model.channel='none';returnToChannel();return;}
if(button.dataset.mode){const kind=button.dataset.mode;showMainScreen(kind==='streamer'?'channel':'viewer',key,kind);return;}
if(button.dataset.tab){const target={people:'viewer',person:'profile',channel:'channel'}[button.dataset.tab];if(target){const kind=screen.querySelector('[data-mode=streamer][aria-pressed=true]')?'streamer':'viewer';showMainScreen(target,key,kind);return;}showDialog(button.dataset.tab==='posts'?'Посты':'Главная','Этот раздел будет перенесён вместе с существующими функциями после согласования дизайна. В предпросмотре доступны стримеры, подключение и профиль.');}
});
for(const [id,property] of [['viewer-scenario','viewer'],['channel-scenario','channel'],['profile-scenario','plan']]){const el=document.getElementById(id);el.value=model[property];el.addEventListener('change',()=>{model[property]=el.value;model.lookupVersion++;model.lookupBusy=false;model.result=null;routes.clear();displayByKey.clear();channelOverrides.clear();checkRequests.clear();if(property==='viewer')resetSubscriptions();render(['viewer','channel','profile']);});}
document.getElementById('telegram-scenario').value=telegramTheme;
document.getElementById('telegram-scenario').addEventListener('change',event=>{telegramTheme=event.target.value;if(themeChoice==='telegram'){applyTheme();render();}});
/* Explicit fixture controls for browser QA; not present on any product route. */
window.selectedPrototype={setFault:(kind)=>failures.set(kind,true),refresh:()=>render(),back,theme:()=>({choice:themeChoice,effective:document.documentElement.dataset.theme}),safeInsets:(top,bottom)=>{document.documentElement.style.setProperty('--safe-top',top+'px');document.documentElement.style.setProperty('--safe-bottom',bottom+'px');}};
render();

