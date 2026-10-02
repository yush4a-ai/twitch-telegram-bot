/* Local, illustrative prototype. It has no SDK, credentials, API or bot sender. */
const icons = {
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>',
  home: '<path d="m3 10 9-7 9 7v10H6V10"/><path d="M10 20v-7h4v7"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/>',
  channel: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',
  posts: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>',
  video: '<rect x="3" y="6" width="12" height="12" rx="3"/><path d="m15 10 6-3v10l-6-3"/>',
  folder: '<path d="M3 7V5h6l2 3h10v12H3z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  warning: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/>',
};
const icon = (name, extra = '') => `<svg viewBox="0 0 24 24" aria-hidden="true" ${extra}>${icons[name]}</svg>`;
const escapeText = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const demoStreamers = [
  {name:'Александр и его невероятные приключения', login:'alexander_and_his_adventures', initials:'А', tone:'', live:true, category:'Just Chatting', video:true},
  {name:'Катерина играет в длинные сюжетные игры', login:'katerina_story_games', initials:'К', tone:'cool', live:true, category:'Baldur’s Gate 3', video:true},
  {name:'Михаил — ночные эфиры без спешки', login:'mikhail_after_midnight', initials:'М', tone:'light', live:false, video:true},
  {name:'ОченьДлинноеИмяСтримераБезПробелов', login:'very_long_streamer_name', initials:'', tone:'missing', live:false, video:false},
  {name:'София. Разговоры о кино и играх', login:'sofia_cinema_and_games', initials:'С', tone:'cool', live:false, video:false, paused:true},
];
const params = new URLSearchParams(location.search);
let direction = params.get('variant') === 'b' ? 'b' : 'a';
let scenario = params.get('state') === 'alternate' ? 'alternate' : 'main';
const singleScreen = params.get('single') === '1' ? params.get('screen') || 'streamers' : null;
if (singleScreen) document.body.classList.add('single');
const concepts = document.getElementById('concepts');
const dialog = document.getElementById('detail');

function mode(kind) {
  return `<header class="screen-header"><div class="mode" role="group" aria-label="Режим приложения"><button aria-pressed="${kind==='viewer'}" data-demo="viewer-mode">Зритель</button><button aria-pressed="${kind==='streamer'}" data-demo="streamer-mode">Стример</button></div></header>`;
}
function tabs(kind, active) {
  const entries = kind === 'viewer' ? [['home','Главная'],['people','Стримеры'],['person','Профиль']] : [['channel','Мой канал'],['posts','Посты'],['person','Профиль']];
  return `<nav class="tabbar" aria-label="Разделы ${kind==='viewer'?'зрителя':'стримера'}">${entries.map(([glyph,label],i)=>`<button ${i===active?'aria-current="page"':''} data-demo="tab-${glyph}">${icon(glyph)}${label}</button>`).join('')}</nav>`;
}
function avatar(person) {
  return `<span class="avatar ${person.tone}" aria-hidden="true">${person.initials || icon('person')}</span>`;
}
function streamerRows(rows) {
  return rows.map(s=>`<button class="stream-row" data-streamer="${s.login}">${avatar(s)}<span class="row-copy"><strong>${escapeText(s.name)}</strong><span class="status">${s.live?`<span class="live">В эфире</span><span class="dot"></span><span>${s.category}</span>`:`<span>${s.paused?'Уведомления на паузе':'Не в эфире'}</span>${s.video?'<span class="dot"></span><span class="video">Видео</span>':''}`}</span></span>${icon('arrow','class="chevron"')}</button>`).join('');
}
function streamerList(rows) {
  if(direction==='a') return streamerRows(rows);
  const live=rows.filter(s=>s.live), offline=rows.filter(s=>!s.live);
  return `${live.length?'<p class="section-label">В эфире · '+live.length+'</p>'+streamerRows(live):''}${offline.length?'<p class="section-label">Не в эфире · '+offline.length+'</p>'+streamerRows(offline):''}`;
}
function streamers() {
  const empty = scenario === 'alternate';
  const title = `<div class="title-row"><h1>Стримеры</h1>${empty?'':`<button class="text-button" data-demo="add">${icon('plus')}Добавить</button>`}</div>`;
  const search = `<label class="search">${icon('search')}<input type="search" aria-label="Ник или ссылка Twitch" placeholder="Ник или ссылка Twitch" data-search></label>`;
  const body = empty ? `<div class="empty-content">${icon('people')}<h2>Кого будем смотреть?</h2><p>Добавь первого стримера. Я сообщу в Telegram, когда он выйдет в эфир.</p><button class="primary" data-demo="add">Найти стримера</button><p class="helper">Фото и уведомления бесплатны.<br>Можно отслеживать до 50 стримеров.</p></div>` : `${search}${direction==='a'?'<div class="toolbar"><button class="text-button" data-demo="folders">Все стримеры '+icon('arrow','class="chevron"')+'</button><span class="small muted">5 из 200</span></div><div class="group" data-list>'+streamerRows(demoStreamers)+'</div>':'<div data-list><p class="section-label">В эфире · 2</p>'+streamerRows(demoStreamers.filter(s=>s.live))+'<p class="section-label">Не в эфире · 3</p>'+streamerRows(demoStreamers.filter(s=>!s.live))+'</div>'}<div class="video-summary"><span>${icon('video')}<span>Видеопревью <b>3 из 5</b></span></span><button class="text-button" data-demo="video">Выбрать</button></div>${direction==='a'?'<p class="helper">Нажми на стримера, чтобы настроить уведомления и превью.</p>':'<div class="toolbar"><button class="text-button" data-demo="folders">'+icon('folder')+'Папки</button><span class="small muted">5 из 200 стримеров</span></div>'}`;
  return `${mode('viewer')}<main class="screen-content">${title}${body}</main>${tabs('viewer',1)}`;
}
function channel() {
  const missingBot = scenario === 'alternate';
  const steps = `<div class="step-indicator" aria-label="Второй из трёх шагов"><span>${icon('check')}Twitch</span>${icon('arrow','class="chevron"')}<span class="current">Telegram</span>${icon('arrow','class="chevron"')}<span>Публикации</span></div>`;
  const identity = `<div class="group"><div class="identity-row"><span class="avatar cool" aria-hidden="true">А</span><span class="row-copy"><strong>Александр и его невероятные приключения</strong><span class="status">Twitch подключён</span></span>${icon('check')}</div><div class="identity-row"><span class="avatar light" aria-hidden="true">${icon('channel')}</span><span class="row-copy"><strong>Эфиры Александра и разговоры после полуночи</strong><span class="status">Telegram-канал выбран</span></span></div></div>`;
  const status = missingBot ? `<h2>${icon('warning')}Добавь бота в канал</h2><p>Бот пока не администратор выбранного канала.</p>` : `<h2>${icon('warning')}Нужно право публикации</h2><p>У бота нет разрешения отправлять сообщения в этот канал.</p>`;
  const instructions = direction==='a' ? `<div class="instructions"><h3>В настройках канала</h3><p>Администраторы → Добавить администратора → выбери бота:</p><button class="bot-name" data-copy>@TwitchSignalTestbot</button><div class="rights"><strong>Включи «Публикация сообщений»</strong><p class="small muted">Для уведомлений о начале эфира.</p></div></div>` : `<div class="instructions"><h3>1. Добавь бота</h3><p>Открой настройки канала → Администраторы → Добавить администратора.</p><button class="bot-name" data-copy>@TwitchSignalTestbot</button><div class="rights"><h3>2. Разреши публикации</h3><p>Включи «Публикация сообщений», сохрани права и вернись сюда.</p></div></div>`;
  return `${mode('streamer')}<main class="screen-content"><h1>Подключение<br>Telegram-канала</h1>${steps}${identity}<div class="permission-status" aria-live="polite">${status}</div>${instructions}<p class="next-hint">После проверки покажем пример поста. Ты сам включишь публикации.</p></main><div class="connection-actions"><p class="helper">Стандартные публикации бесплатны</p><button class="primary" data-check>Проверить подключение</button><button class="secondary-button" data-demo="select-channel">Выбрать другой канал</button></div>${tabs('streamer',0)}`;
}
function setting(label, value, action, description='') {
  return `<button class="setting-row" data-demo="${action}"><span>${label}${description?`<small>${description}</small>`:''}</span><span class="value">${value}${icon('arrow','class="chevron"')}</span></button>`;
}
function themeControl() {
  const options = [['light','Светлая'],['dark','Тёмная'],['telegram','Как в Telegram']];
  if(direction==='b') return `<div class="theme-control"><div class="theme-options" role="group" aria-label="Оформление">${options.map(([id,label])=>`<button class="theme-option" data-theme-choice="${id}" aria-pressed="${id==='light'}">${label}${id==='light'?icon('check'):''}</button>`).join('')}</div></div>`;
  return `<div class="theme-control"><h3>Оформление</h3><div class="choice" role="group" aria-label="Оформление">${options.map(([id,label])=>`<button data-theme-choice="${id}" aria-pressed="${id==='light'}">${label}</button>`).join('')}</div><p>Светлая тема выбрана для этого примера.</p></div>`;
}
function profile() {
  const active=scenario==='main';
  const identity=`<div class="profile-identity"><span class="avatar cool" aria-hidden="true">АЛ</span><div class="row-copy"><strong>Александра Лебедева‑Константинопольская</strong><p>@alexandra_streams_and_cinema</p></div></div>`;
  const plan=`<div class="plan"><div class="plan-header"><h2>${active?'Viewer Plus':'Free'}</h2><span class="plan-status">${active?'Активен':'Бесплатно'}</span></div><p class="small muted">${active?'Тестовый доступ до 9 октября 2026':'Уведомления и фото без подписки'}</p>${active?'<div class="plan-detail"><span>Отслеживаемые стримеры</span><span>5 / 200</span><span>Выбранные видеопревью</span><span>3 / 5</span></div><p class="plan-features">Фильтры, смена категории, напоминания, папки и история событий.</p>':'<div class="plan-detail"><span>Отслеживаемые стримеры</span><span>0 / 50</span></div><p class="plan-features">С Viewer Plus можно отслеживать до 200 стримеров. Ещё доступны фильтры, напоминания и 5 видеопревью.</p>'}<button class="text-button" data-demo="subscription">${active?'Доступ и история операций':'Возможности Viewer Plus'}${icon('arrow','class="chevron"')}</button></div>`;
  return `${mode('viewer')}<main class="screen-content"><div class="title-row"><h1>Профиль</h1></div>${identity}${direction==='a'?'<div class="group">'+plan+setting('Streamer Plus','Не активен','streamer-plus')+'</div><p class="section-label">Настройки</p><div class="group">'+themeControl()+setting('Тихие часы','23:00–08:00','quiet')+'</div>':plan+setting('Streamer Plus','Не активен','streamer-plus')+'<p class="section-label">Оформление</p>'+themeControl()+setting('Тихие часы','23:00–08:00','quiet')}<p class="section-label">Мои данные</p><div class="group">${setting('Отчёты и HTML-экспорт','','reports')}${setting('История событий',active?'':'Plus','history')}${setting('Помощь','','help')}</div></main>${tabs('viewer',2)}`;
}
function render() {
  const descriptions={a:'Компактные строки на общей поверхности. Подписка и настройки собраны в короткие группы.',b:'Белая поверхность без контейнеров. Эфиры сгруппированы по состоянию, подключение идёт по шагам.'};
  const names={a:'А · Собранный',b:'Б · Открытый'};
  const screens=[['streamers','Стримеры',streamers],['channel','Подключение Telegram-канала',channel],['profile','Профиль и подписка',profile]].filter(([id])=>!singleScreen||id===singleScreen);
  concepts.innerHTML=`<section class="board direction-${direction}"><div class="board-heading"><h2>${names[direction]}</h2><p>${descriptions[direction]} Все данные ниже условные.</p></div><div class="screen-grid">${screens.map(([id,label,view])=>`<article class="preview"><div class="screen-label"><strong>${label}</strong><span>390 px</span></div><div class="screen" data-screen="${id}">${view()}</div></article>`).join('')}</div></section>`;
  document.querySelectorAll('[data-direction]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.direction===direction)));
  document.querySelectorAll('[data-scenario]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scenario===scenario)));
}
const demoDetails={
  'viewer-mode':['Режим зрителя','Главная, стримеры и профиль. Переключение режима не выдаёт Plus. В этом концепте показаны три выбранных экрана.'],
  'streamer-mode':['Режим стримера','Мой канал, посты и профиль. Здесь показан шаг подключения Telegram-канала.'],
  add:['Добавить стримера','В приложении здесь откроется поиск по нику или Twitch-ссылке. Концепт не обращается к Twitch.'],
  folders:['Папки','Список папок и их правила откроются отдельным экраном. Создание папки больше не занимает верх списка стримеров.'],
  video:['Видеопревью · Viewer Plus','В этом примере выбраны 3 из 5 стримеров, включая тех, кто не в эфире. Для шестого потребуется выбрать замену.'],
  subscription:['Подписка и операции','Пользователь видит свой план, срок и собственные операции. Подтверждение и отзыв тестового доступа находятся в отдельном защищённом инструменте владельца.'],
  'streamer-plus':['Streamer Plus','Оформление, кнопки, живое превью и статистика подтверждённых публикаций. Стандартное подключение Telegram-канала бесплатно.'],
  quiet:['Тихие часы','Существующая бесплатная настройка. В данном примере: с 23:00 до 08:00.'],
  reports:['Отчёты и HTML-экспорт','Существующие отчёты и HTML-экспорт сохраняют прежние права. Перенос расширенной аналитики ещё не входит в редизайн.'],
  history:['История событий','Личная история исходов уведомлений доступна с Viewer Plus. Пример не содержит реальных событий.'],
  help:['Помощь','Здесь будут инструкции по уведомлениям, подключению и восстановлению доступа.'],
  'select-channel':['Выбор канала','В рабочем приложении Telegram предложит только каналы. Этот макет не открывает Telegram и не создаёт запрос выбора.'],
};
function showDetail(title, text) {dialog.querySelector('.dialog-content').innerHTML=`<h2>${escapeText(title)}</h2><p>${escapeText(text)}</p><p class="small muted">Локальный концепт. Действие не изменяет данные бота.</p>`; dialog.showModal();}
document.addEventListener('click',event=>{
  const button=event.target.closest('button'); if(!button)return;
  if(button.dataset.direction){direction=button.dataset.direction;render();return;}
  if(button.dataset.scenario){scenario=button.dataset.scenario;render();return;}
  if(button.hasAttribute('data-close')){dialog.close();return;}
  if(button.dataset.streamer){const s=demoStreamers.find(item=>item.login===button.dataset.streamer);showDetail(s.name,`Полный логин: ${s.login}. Здесь откроются уведомления, фильтры и выбор превью этого стримера.`);return;}
  if(button.hasAttribute('data-check')){
    button.disabled=true;button.textContent='Проверяем права…';
    setTimeout(()=>{button.disabled=false;button.textContent='Проверить ещё раз';button.closest('.screen').querySelector('.permission-status').innerHTML=`<h2>${icon('warning')}Прав пока не хватает</h2><p>В этом примере право публикации выключено. Включи его в настройках канала и повтори проверку.</p>`;},500);return;
  }
  if(button.hasAttribute('data-copy')){showDetail('@TwitchSignalTestbot','Имя бота можно скопировать из этого текста. Реальных переходов в Telegram в концепте нет.');return;}
  if(button.dataset.themeChoice){showDetail('Оформление',`Выбрано «${button.textContent.trim()}». В этом этапе сравниваем два светлых направления; остальные темы подготовим после выбора дизайна.`);return;}
  if(button.dataset.demo){const details=demoDetails[button.dataset.demo]||['Раздел приложения','В этом этапе показаны три ключевых экрана. Остальные разделы оформим после выбора направления.'];showDetail(...details);}
});
document.addEventListener('input',event=>{
  if(!event.target.matches('[data-search]'))return;
  const query=event.target.value.trim().toLocaleLowerCase('ru');
  const matches=demoStreamers.filter(s=>(s.name+' '+s.login).toLocaleLowerCase('ru').includes(query));
  const target=event.target.closest('.screen').querySelector('[data-list]');
  target.innerHTML=matches.length?streamerList(matches):'<div class="empty-content"><h2>В твоём списке нет совпадений</h2><p>Попробуй другой ник или очисти поиск.</p><button class="primary" data-clear-search>Очистить поиск</button></div>';
});
document.addEventListener('click',event=>{if(event.target.closest('[data-clear-search]')){const field=event.target.closest('.screen').querySelector('[data-search]');field.value='';field.dispatchEvent(new Event('input',{bubbles:true}));field.focus();}});
render();

