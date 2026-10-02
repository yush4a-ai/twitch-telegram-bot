import { ApiError } from './api.js';
import { element, panel, action, dialog, icon } from './components.js';

export function createViewerFeature(api, getRouter, telegram) {
  let disposed=false;
  let data = null;
  let loading = false;
  let loadToken=0,pendingLoad=null;
  let requested = false;
  let error = '';
  let searchDraft = '';
  let searchResults = [];
  let searchTimer = null;
  let searchController = null;
  let searchVersion = 0;
  let feedback = '';
  let lastLoadedAt = 0;
  let quietDraft = null;
  let profileFeedback = '';
  let videoFeedback = '';
  let videoSaving = false;
  let planFeedback = '';
  let reminderFeedback = '';
  let reminderSaving = false;
  let folderNameDraft = '';
  let folderFeedback = '';
  let folderSaving = false;
  let historyState = { events: [], nextBefore: null, loaded: false, loading: false, error: '' };
  let lastDetail = null;
  let listDraft = (api.storage.getItem('ts-viewer-subscription-search') || '').slice(0,200);
  const notifySaving=new Set();
  const filterDrafts = new Map();
  const folderDrafts = new Map();
  const folderMoveDrafts = new Map();
  const categoryDrafts = new Map();
  const names = new Map();
  let videoQuery=(api.storage.getItem('ts-viewer-video-search')||'').slice(0,200);
  try { searchDraft = (api.storage.getItem('ts-app-search-draft') || '').slice(0, 200); } catch {}
  const nameOf = (login) => names.get(login) || data?.subscriptions.find(row=>row.login===login)?.display_name || login;
  const current = () => getRouter().state;
  const refresh = () => {if(!disposed)getRouter().refresh();};
  async function load({fresh=false}={}) {
    if (disposed) return;
    if (loading&&!fresh) return pendingLoad;
    requested = true;
    loading = true;
    lastLoadedAt = Date.now();
    if (!data) refresh();
    const token=++loadToken;
    pendingLoad=(async()=>{
      try {
        const loaded=await api.post('/app/api/viewer/state');
        if(token===loadToken){data=loaded;error='';}
      } catch (cause) {
        if(token===loadToken)error=cause instanceof ApiError && [401,403].includes(cause.status)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : data?'Нет связи. Показываем последние загруженные данные.':'Не удалось загрузить подписки. Проверьте связь и повторите.';
      } finally { if(token===loadToken){loading=false;pendingLoad=null;refresh();} }
    })();
    return pendingLoad;
  }
  const onVisibility=() => {
    if (!document.hidden && data) void load();
  };
  document.addEventListener('visibilitychange',onVisibility);
  function heading(target, eyebrow, title, lead) {
    target.append(element('p', 'eyebrow', eyebrow), element('h1', '', title), element('p', 'lead', lead));
  }
  function banner(target, message, danger = false) {
    const note = element('p', `notice${danger ? ' error' : ''}`, message);
    note.setAttribute('role', 'status');
    target.append(note);
  }
  function renderHome(target) {
    target.append(element('h1','','Главная'));
    const tracked = data.subscriptions;
    const live = tracked.filter((row) => row.status === 'live');
    const stale = tracked.some((row) => row.status === 'stale');
    target.append(element('p','lead',`${tracked.length} из ${data.channel_limit} стримеров · ${tracked.filter(row=>row.notify_enabled&&!row.paused_by_plan).length} с уведомлениями`));
    if (!tracked.length) {
      target.append(panel('Здесь появятся эфиры', 'Добавьте стримера, чтобы получать оповещения о его эфирах.'));
    } else if (!live.length) {
      target.append(panel(stale ? 'Проверяем статус эфиров' : 'Пока нет подтверждённого эфира', 'Бот обновит статус после следующей проверки. Ваши подписки остаются в разделе «Стримеры».'));
    } else {
      const head=element('div','section-head');head.append(element('h2','','В эфире'),element('small','muted',String(live.length)));target.append(head);
      const list = element('div', 'streamer-group');
      for (const row of live) {
        const item = streamerRow(row,{home:true});
        const link = element('a', 'text-link', 'Смотреть');
        link.href = `https://www.twitch.tv/${row.login}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label',`Смотреть ${nameOf(row.login)} на Twitch`);item.append(link);
        list.append(item);
      }
      target.append(list);
    }
    const actions = element('div', 'actions');
    actions.append(action('Мои стримеры', () => getRouter().setTab('streamers'),true));
    target.append(actions);
    const upcoming=tracked.filter(row=>row.reminder?.status==='scheduled').sort((a,b)=>a.reminder.due_at-b.reminder.due_at);
    if(upcoming.length){const box=element('section','navigation-group');box.append(element('h2','group-heading','Напоминания'));for(const row of upcoming)box.append(action(`${nameOf(row.login)} · ${new Date(row.reminder.due_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}`,()=>getRouter().openDetail(row.login),true));target.append(box);}
  }
  function streamerRow(row,{home=false}={}){
    const name=nameOf(row.login),item=element('div','list-row streamer-row');item.dataset.rowKey=row.login;
    const avatar=element('span','avatar',name.slice(0,1).toUpperCase());avatar.setAttribute('aria-hidden','true');
    const open=action('',()=>getRouter().openDetail(row.login),true);open.className='streamer-open';open.setAttribute('aria-label',`Открыть ${name}`);open.dataset.focusKey=`streamer:${row.login}`;open.dataset.openStreamer='';
    const copy=element('span','row-copy');copy.append(element('strong','streamer-name',name));
    const status=element('small',`stream-status ${row.status}`,row.status==='live'?'В эфире':row.status==='stale'?'Статус уточняется':'Не в эфире');copy.append(status);
    if(row.status==='live'&&row.live?.category)copy.append(element('small','muted',row.live.category));
    if(home&&row.live?.title)copy.append(element('small','stream-title',row.live.title));
    if(row.paused_by_plan)copy.append(element('small','muted','Приостановлено по лимиту'));
    else if(!row.notify_enabled)copy.append(element('small','muted','Уведомления на паузе'));
    const folder=data.folders?.find(value=>value.id===row.folder_id);if(folder)copy.append(element('small','muted',folder.name));
    open.append(avatar,copy);item.append(open);
    if(!home){const label=element('label','quick-notify'),input=element('input','');input.type='checkbox';input.checked=row.notify_enabled;input.setAttribute('aria-label',`Уведомления ${name}`);input.dataset.focusKey=`notify:${row.login}`;
      if(notifySaving.has(row.login))input.setAttribute('aria-disabled','true');
      input.addEventListener('change',async()=>{
        if(notifySaving.has(row.login)){input.checked=row.notify_enabled;return;}
        const enabled=input.checked;notifySaving.add(row.login);input.setAttribute('aria-disabled','true');
        try{await api.post('/app/api/viewer/notify',{login:row.login,enabled});row.notify_enabled=enabled;feedback=enabled?`Уведомления ${name} включены`:`Уведомления ${name} на паузе`;}
        catch{input.checked=row.notify_enabled;feedback='Не удалось сохранить уведомления. Попробуйте ещё раз.';}
        finally{notifySaving.delete(row.login);refresh();}
      });label.append(input,icon('notification'));item.append(label);
    }
    return item;
  }
  function queueSearch(input, resultBox) {
    searchDraft = input.value;
    feedback = '';
    resultBox.parentNode.querySelector('[data-feedback]')?.remove();
    try { api.storage.setItem('ts-app-search-draft', searchDraft); } catch {}
    clearTimeout(searchTimer);
    searchController?.abort();
    const version = ++searchVersion;
    searchResults = [];
    resultBox.replaceChildren();
    if (searchDraft.trim().length < 4) return;
    searchTimer = setTimeout(async () => {
      searchController = new AbortController();
      resultBox.replaceChildren(element('p', 'muted', 'Ищем канал…'));
      try {
        const found = await api.post('/app/api/viewer/search', { query: searchDraft }, { signal: searchController.signal });
        if (version !== searchVersion) return;
        searchResults = found.results;
        for (const row of searchResults) names.set(row.login, row.display_name);
        showResults(resultBox);
      } catch (cause) {
        if (cause.name === 'AbortError' || version !== searchVersion) return;
        resultBox.replaceChildren(element('p', 'notice error', cause instanceof ApiError && cause.status === 429
          ? 'Слишком много поисковых запросов. Подождите несколько секунд.'
          : cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
            ? 'Время входа истекло. Откройте приложение из чата бота. Ник сохранён.'
          : 'Поиск пока недоступен. Проверьте ник и попробуйте ещё раз.'));
      }
    }, 350);
  }
  function showResults(box) {
    box.replaceChildren();
    if (!searchResults.length) { box.append(element('p', 'muted', 'Канал не найден. Проверьте ник Twitch.')); return; }
    for (const row of searchResults) {
      const item = element('div', 'result-row');
      const copy = element('div', 'row-copy');
      copy.append(element('strong', '', row.display_name), element('small', '', `twitch.tv/${row.login}`));
      const subscribed = data.subscriptions.some((entry) => entry.login === row.login);
      const button = action(subscribed ? 'Открыть' : 'Добавить', async () => {
        if (subscribed) { box.onDone?.();getRouter().openDetail(row.login); return; }
        button.disabled = true;
        try {
          const writeAccess = await telegram.requestWriteAccess();
          await api.post('/app/api/viewer/follow', { login: row.login });
          searchResults = [];
          searchDraft = '';
          try { api.storage.removeItem('ts-app-search-draft'); } catch {}
          feedback = writeAccess === false
            ? `${row.display_name} добавлен. Разрешите боту личные сообщения, чтобы получать оповещения.`
            : `${row.display_name} добавлен`;
          await load();
          box.onDone?.();
        } catch (cause) {
          button.disabled = false;
          feedback = cause instanceof ApiError && cause.code === 'channel_limit'
            ? 'Достигнут лимит подписок. Удалите одну подписку и повторите попытку.'
            : cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
              ? 'Время входа истекло. Откройте приложение из чата бота. Ник сохранён.'
            : 'Не удалось добавить стримера. Попробуйте ещё раз.';
          refresh();
          box.append(element('p','notice error',feedback));
        }
      }, subscribed);
      button.setAttribute('aria-label', subscribed ? `Открыть ${row.display_name}` : `Добавить ${row.display_name}`);
      item.append(copy, button);
      box.append(item);
    }
  }
  function renderStreamers(target) {
    const head=element('div','title-row');head.append(element('h1','','Стримеры'),action('Добавить стримера',openAdd,true));target.append(head);
    target.append(element('p','lead',`${data.subscriptions.length} из ${data.channel_limit} стримеров`));
    const input = element('input', 'input');
    input.type='search';input.name='subscription_search';input.maxLength=200;input.autocomplete='off';input.placeholder='Поиск по подпискам';input.setAttribute('aria-label','Поиск по подпискам');input.value=listDraft;
    const groups=element('div','subscription-groups');input.addEventListener('input',()=>{listDraft=input.value;api.storage.setItem('ts-viewer-subscription-search',listDraft);renderGroups(groups);});target.append(input);
    if (feedback) {
      const note = element('p', 'notice', feedback);
      note.setAttribute('role', 'status');
      note.setAttribute('data-feedback', '');
      target.append(note);
    }
    const tools=element('div','viewer-tools');tools.append(action(data.viewer_plus_active?`Видео · ${data.video_selection.selected_logins.length}/${data.video_selection.limit}`:'Видео · Plus',()=>getRouter().openDetail(data.viewer_plus_active?'video-selection':'subscription'),true));target.append(tools);
    renderFolders(target);
    renderGroups(groups);target.append(groups);
  }
  function renderGroups(target){
    target.replaceChildren();
    if(!data.subscriptions.length){target.append(panel('Добавьте первого стримера','Найдите его по нику Twitch или вставьте ссылку на канал.'));return;}
    const query=listDraft.trim().toLocaleLowerCase('ru-RU');
    const rows=data.subscriptions.filter(row=>`${nameOf(row.login)} ${row.login}`.toLocaleLowerCase('ru-RU').includes(query));
    if(!rows.length){target.append(panel('В подписках ничего не найдено','Измените запрос или добавьте нового стримера.'));return;}
    for(const [status,title] of [['live','В эфире'],['stale','Статус уточняется'],['offline','Не в эфире']]){
      const selected=rows.filter(row=>row.status===status);if(!selected.length)continue;
      const section=element('section','streamer-section'),heading=element('div','section-head');heading.append(element('h2','',title),element('small','muted',String(selected.length)));section.append(heading);
      const list=element('div','streamer-group');for(const row of selected)list.append(streamerRow(row));section.append(list);target.append(section);
    }
  }
  function openAdd(event){
    dialog('Добавить стримера',(content,close)=>{
      const label=element('label','search-field','Ник или ссылка Twitch'),input=element('input','input'),results=element('div','search-results');input.type='search';input.name='channel_search';input.maxLength=200;input.autocomplete='off';input.spellcheck=false;input.value=searchDraft;input.placeholder='Например, twitch.tv/alpha';label.append(input);results.setAttribute('role','status');results.onDone=close;
      input.addEventListener('input',()=>queueSearch(input,results));content.append(label,results);if(searchResults.length)showResults(results);else if(searchDraft.trim().length>=4)queueSearch(input,results);
    },{origin:event.currentTarget,onClose:()=>{clearTimeout(searchTimer);searchController?.abort();++searchVersion;}});
  }
  function videoStatus(row){
    if(row.video_delivery_status==='returning_photo')return 'Возвращаем фото в текущее сообщение';
    if(!row.video_selected&&row.video_delivery_status==='unknown')return 'Выбрано фото · доставка пока не подтверждена';
    if(!row.video_selected)return 'Фото по умолчанию';
    if(row.paused_by_plan)return 'Выбрано · подписка приостановлена по лимиту';
    if(!row.notify_enabled)return 'Выбрано · уведомления на паузе';
    const labels={video:'Видео в текущем сообщении',photo:'Сейчас фото · выбор видео сохранён',offline:'Не в эфире · место для видео занято',limited:'Перегрузка · пока показываем фото',unavailable:'Видео недоступно · пока показываем фото',preparing:'Готовим видео · пока может показываться фото',unknown:'Доставка видео пока не подтверждена',returning_photo:'Возвращаем фото в текущее сообщение'};
    return labels[row.video_delivery_status]||labels.unknown;
  }
  function replaceVideo(login,event){
    const snapshot=data.video_selection;
    dialog('Все пять мест заняты',(box,close)=>{
      box.append(element('p','muted',`Кого заменить на ${nameOf(login)}? Остальные четыре выбора сохранятся.`));
      for(const old of snapshot.selected_logins){const button=action(`Заменить ${nameOf(old)}`,()=>{const next=snapshot.selected_logins.map(value=>value===old?login:value);close();void saveVideoSelection(next,{expectedVersion:snapshot.version});},true);box.append(button);}
    },{origin:event.currentTarget,sheet:true});
  }
  function renderVideoPicker(target){
    target.append(element('h1','','Видеопревью'));
    if(!data.viewer_plus_active){target.append(panel('Фото остаётся доступно','Выбор видео сохранён. Для его использования нужен Plus.'),action('Возможности Plus',()=>getRouter().openDetail('subscription'),true));return;}
    const status=element('p','video-count',`Выбрано ${data.video_selection.selected_logins.length} из ${data.video_selection.limit}`);status.setAttribute('role','status');target.append(status,element('p','lead','Пять мест, включая стримеров вне эфира и на паузе. Статус доставки — под именем.'));
    const query=element('input','input');query.type='search';query.maxLength=200;query.value=videoQuery;query.setAttribute('aria-label','Найти стримера для видео');query.placeholder='Найти стримера';
    const list=element('div','video-list');query.addEventListener('input',()=>{videoQuery=query.value;api.storage.setItem('ts-viewer-video-search',videoQuery);renderChoices(list);});target.append(query);
    if(videoFeedback){const note=element('p','notice',videoFeedback);note.setAttribute('role','status');target.append(note);}
    renderChoices(list);target.append(list);
  }
  function renderChoices(list){
    list.replaceChildren();const query=videoQuery.trim().toLocaleLowerCase('ru-RU');
    const rows=data.subscriptions.filter(row=>`${nameOf(row.login)} ${row.login}`.toLocaleLowerCase('ru-RU').includes(query));
    if(!rows.length){list.append(panel(data.subscriptions.length?'Стример не найден':'Сначала добавьте стримера','Выбирайте видео среди своих подписок.'));return;}
    for(const row of rows){
      const label=element('label','video-choice'),input=element('input',''),copy=element('span','row-copy');label.dataset.videoLogin=row.login;label.dataset.rowKey=row.login;
      input.type='checkbox';input.checked=row.video_selected;input.dataset.focusKey=`video:${row.login}`;input.setAttribute('aria-label',`Видео ${nameOf(row.login)}`);if(videoSaving)input.setAttribute('aria-disabled','true');
      copy.append(element('strong','streamer-name',nameOf(row.login)),element('small','muted',videoStatus(row)));label.append(input,copy);
      input.addEventListener('change',event=>{
        const selected=data.video_selection.selected_logins,was=selected.includes(row.login);input.checked=was;
        if(videoSaving)return;
        if(!was&&selected.length>=data.video_selection.limit){replaceVideo(row.login,event);return;}
        void saveVideoSelection(was?selected.filter(value=>value!==row.login):[...selected,row.login]);
      });list.append(label);
    }
  }
  function renderFolders(target) {
    if (!data.viewer_plus_active && !(data.folders || []).length) return;
    const section = element('section', 'panel feature-panel');
    section.append(element('h2', '', 'Папки'));
    section.append(element('p', 'muted', data.viewer_plus_active
      ? 'Группируйте стримеров и задавайте общее правило оповещений.'
      : 'Папки сохранены. Их правила снова заработают с Viewer Plus.'));
    for (const folder of data.folders || []) {
      const count = data.subscriptions.filter((row) => row.folder_id === folder.id).length;
      section.append(action(`${folder.name} · ${count}`, () => getRouter().openDetail(`folder:${folder.id}`), true));
    }
    if (data.viewer_plus_active) {
      const field = element('div', 'search-field');
      const input = element('input', 'input');
      input.type = 'text'; input.name = 'folder_name'; input.autocomplete = 'off';
      input.maxLength = 40; input.value = folderNameDraft;
      input.setAttribute('aria-label', 'Название новой папки');
      input.addEventListener('input', () => { folderNameDraft = input.value; });
      const create = action('Создать папку', async () => {
        if (folderSaving) return;
        folderSaving = true; create.disabled = true;
        try {
          const response = await api.post('/app/api/viewer/folder/create', { name: input.value });
          folderNameDraft = ''; folderFeedback = '';
          await load();
          getRouter().openDetail(`folder:${response.folder.id}`);
        } catch (cause) {
          folderFeedback = cause instanceof ApiError && cause.code === 'folder_limit'
            ? 'Мест для папок больше нет. Удалите пустую папку и повторите.'
            : cause instanceof ApiError && cause.code === 'folder_name_taken'
            ? 'Папка с таким названием уже есть.'
            : 'Не удалось создать папку. Проверьте название и попробуйте ещё раз.';
          refresh();
        } finally { folderSaving = false; }
      });
      field.append(input, create);
      section.append(field);
    }
    if (folderFeedback) section.append(element('p', 'notice error', folderFeedback));
    target.append(section);
  }
  function renderDetail(target, login) {
    const row = data.subscriptions.find((item) => item.login === login);
    if (!row) { heading(target, 'Зритель', 'Стример не найден', 'Вернитесь к своим подпискам.'); return; }
    heading(target, 'Стример', nameOf(login), `twitch.tv/${login}`);
    const settings = element('div', 'panel settings');
    const label = element('label', 'switch-row');
    const checkbox = element('input', '');
    checkbox.type = 'checkbox'; checkbox.name = 'notify'; checkbox.checked = row.notify_enabled;
    label.append(checkbox, element('span', '', 'Уведомлять об эфирах'));
    const status = element('p', 'muted', row.notify_enabled ? 'Уведомления включены' : 'Уведомления выключены');
    checkbox.addEventListener('change', async () => {
      const next = checkbox.checked;
      checkbox.disabled = true;
      try {
        await api.post('/app/api/viewer/notify', { login, enabled: next });
        row.notify_enabled = next;
        status.textContent = next ? 'Уведомления включены' : 'Уведомления выключены';
      } catch (cause) {
        checkbox.checked = !next;
        status.textContent = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : 'Не удалось сохранить. Попробуйте ещё раз.';
      } finally { checkbox.disabled = false; }
    });
    settings.append(label, status);
    target.append(settings);
    if (data.viewer_plus_active && (data.folders || []).length) {
      const section = element('section', 'panel feature-panel');
      section.append(element('h2', '', 'Папка'));
      const selector = element('select', 'input');
      selector.setAttribute('aria-label', 'Папка стримера');
      const empty = element('option', '', 'Без папки'); empty.value = '';
      selector.append(empty);
      for (const folder of data.folders) {
        const option = element('option', '', folder.name); option.value = folder.id;
        selector.append(option);
      }
      selector.value = folderMoveDrafts.get(login) ?? row.folder_id ?? '';
      selector.addEventListener('change', () => { folderMoveDrafts.set(login, selector.value); });
      const save = action('Сохранить папку', async () => {
        if (folderSaving) return;
        folderSaving = true; save.disabled = true;
        try {
          await api.post('/app/api/viewer/folder/move', {
            login, folder_id: selector.value || null,
            expected_folder_id: row.folder_id || null,
          });
          folderMoveDrafts.delete(login);
          folderFeedback = '';
          await load();
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 409) await load();
          folderFeedback = cause instanceof ApiError && cause.status === 409
            ? 'Папка изменилась в другом окне. Проверьте выбор.'
            : 'Не удалось сохранить папку. Попробуйте ещё раз.';
          refresh();
        } finally { folderSaving = false; }
      });
      section.append(selector, save);
      if (folderFeedback) section.append(element('p', 'notice error', folderFeedback));
      target.append(section);
    }
    if (row.paused_by_plan) {
      const paused = element('div', 'panel feature-panel');
      paused.append(element('h2', '', 'Приостановлено по лимиту'));
      paused.append(element('p', 'muted', 'Подписка сохранена. Можно включить её в активные 50. Последняя активная подписка тогда приостановится.'));
      paused.append(action('Включить в активные 50', async () => {
        try {
          await api.post('/app/api/viewer/plan-activate', { login });
          planFeedback = '';
          await load();
        } catch {
          planFeedback = 'Не удалось изменить активные подписки. Попробуйте ещё раз.';
          refresh();
        }
      }));
      if (planFeedback) paused.append(element('p', 'notice error', planFeedback));
      target.append(paused);
    }
    const video = data.video_selection;
    if (data.viewer_plus_active) {
      const section = element('div', 'panel feature-panel');
      section.append(element('h2', '', 'Видеопревью'));
      section.append(element('p', 'muted', `Видеопревью: ${video.selected_logins.length} из ${video.limit}. ${row.video_selected ? 'Для этого стримера выбрано видео.' : 'Сейчас используется фото.'}`));
      if (row.video_selected) {
        const delivery = {
          video: 'Видео показывается в текущем сообщении.',
          photo: 'Сейчас используется фото. Выбор видео сохранён.',
          offline: 'Стример вне эфира. Место для видео остаётся занятым.',
          limited: 'Видеопревью временно перегружено. Пока показываем фото.',
          unavailable: 'Видеопревью временно недоступно. Пока показываем фото.',
          preparing: 'Готовим видео. Пока может показываться фото.',
          unknown: 'Проверяем видео. Пока может показываться фото.',
        };
        section.append(element('p', 'muted', delivery[row.video_delivery_status] || delivery.unknown));
      }
      if (row.video_selected) {
        section.append(action('Выключить видео', () => void saveVideoSelection(video.selected_logins.filter((value) => value !== login))));
      } else if (video.selected_logins.length < video.limit) {
        section.append(action('Выбрать видео', () => void saveVideoSelection([...video.selected_logins, login])));
      } else {
        section.append(element('p', 'muted', 'Все пять мест заняты. Выберите, кого заменить.'));
        for (const old of video.selected_logins) {
          section.append(action(`Заменить ${nameOf(old)}`, () => void saveVideoSelection(video.selected_logins.map((value) => value === old ? login : value)), true));
        }
      }
      if (videoFeedback) {
        const note = element('p', 'notice', videoFeedback);
        note.setAttribute('role', 'status');
        section.append(note);
      }
      target.append(section);
    } else {
      target.append(panel('Видеопревью · Viewer Plus', 'Фото остаётся по умолчанию. С Viewer Plus можно выбрать до пяти стримеров для видео.'));
      const access = element('div', 'actions');
      access.append(action('Посмотреть доступ', () => getRouter().openDetail('subscription'), true));
      target.append(access);
    }
    if (row.video_delivery_status === 'returning_photo') {
      target.append(element('p', 'notice', 'Возвращаем фото в текущее сообщение.'));
    }
    if (data.viewer_plus_active && (row.status === 'live' || row.reminder)) {
      const reminder = element('div', 'panel feature-panel');
      reminder.append(element('h2', '', 'Напоминание об эфире'));
      const saved = row.reminder;
      const statusText = saved?.status === 'scheduled' && Number(saved.due_at) <= Date.now() / 1000
        ? 'Срок наступил. Напоминание ожидает проверки эфира; доставка может задержаться.'
        : saved?.status === 'scheduled'
        ? `Запланировано через ${saved.delay_minutes} минут от выбора. Если эфир закончится, сообщение не придёт.`
        : saved?.status === 'sending' ? 'Отправляем напоминание. Сейчас изменить его нельзя.'
        : saved?.status === 'sent' ? 'Напоминание отправлено.'
        : saved?.status === 'cancelled' ? 'Напоминание отменено.'
        : saved?.status === 'suppressed' ? 'Напоминание не отправлено: условия изменились.'
        : saved?.status === 'unknown' ? 'Не удалось подтвердить доставку напоминания.'
        : 'Выберите время, пока стример в эфире.';
      reminder.append(element('p', 'muted', statusText));
      if (row.status === 'live' && !row.paused_by_plan && row.notify_enabled && saved?.status !== 'sending') {
        const controls = element('div', 'actions');
        for (const minutes of [15, 30]) {
          const button = action(`Через ${minutes} минут`, () => void saveReminder(login, minutes), minutes === 30);
          button.disabled = reminderSaving;
          controls.append(button);
        }
        reminder.append(controls);
      }
      if (saved?.status === 'scheduled') {
        const cancel = action('Отменить напоминание', () => void cancelReminder(login), true);
        cancel.disabled = reminderSaving;
        reminder.append(cancel);
      }
      if (reminderFeedback) reminder.append(element('p', 'notice error', reminderFeedback));
      target.append(reminder);
    } else if (row.status === 'live') {
      target.append(panel('Напоминание · Viewer Plus', 'Во время эфира можно попросить напомнить через 15 или 30 минут.'));
    }
    if (data.viewer_plus_active) {
      const rule = element('div', 'panel feature-panel');
      rule.append(element('h2', '', 'Фильтр эфиров'));
      const inherited = data.folders?.find((entry) => entry.id === row.folder_id);
      rule.append(element('p', 'muted', row.filter
        ? explainFilter(row.filter)
        : inherited ? `Правило папки «${inherited.name}»: ${explainFilter(inherited)}`
        : explainFilter(null)));
      rule.append(action('Настроить фильтр', () => getRouter().openDetail(`filter:${login}`), true));
      target.append(rule);
    } else {
      target.append(panel('Фильтр эфиров · Viewer Plus', 'Viewer Plus открывает фильтр по категориям и словам в названии.'));
    }
    renderCategoryAlert(target, row);
    const actions = element('div', 'actions');
    const twitchLink = element('a', 'button secondary', 'Открыть Twitch');
    twitchLink.href = `https://www.twitch.tv/${login}`;
    twitchLink.target = '_blank';
    twitchLink.rel = 'noopener noreferrer';
    actions.append(twitchLink);
    actions.append(action('Удалить подписку', async () => {
      const sdk = window.Telegram?.WebApp;
      const confirmed = sdk?.showConfirm
        ? await new Promise((resolve) => sdk.showConfirm(`Удалить ${nameOf(login)} из подписок?`, resolve))
        : window.confirm(`Удалить ${nameOf(login)} из подписок?`);
      if (!confirmed) return;
      try {
        const removed = await api.post('/app/api/viewer/unfollow', { login });
        data.subscriptions = data.subscriptions.filter((item) => item.login !== login);
        data.video_selection = removed.video_selection;
        getRouter().back();
      } catch (cause) {
        status.textContent = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : 'Не удалось удалить подписку. Попробуйте ещё раз.';
      }
    }, true));
    target.append(actions);
  }
  async function saveReminder(login, delayMinutes) {
    if (reminderSaving) return;
    reminderSaving = true;
    reminderFeedback = '';
    refresh();
    try {
      await api.post('/app/api/viewer/reminder', { login, delay_minutes: delayMinutes });
      await load();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'reminder_in_flight') await load();
      reminderFeedback = cause instanceof ApiError && cause.code === 'reminder_in_flight'
        ? 'Отправка уже началась. Проверьте статус через минуту.'
        : cause instanceof ApiError && cause.status === 409
        ? 'Эфир изменился. Обновите страницу и попробуйте снова.'
        : 'Не удалось сохранить напоминание. Попробуйте ещё раз.';
    } finally { reminderSaving = false; refresh(); }
  }
  async function cancelReminder(login) {
    if (reminderSaving) return;
    reminderSaving = true;
    reminderFeedback = '';
    refresh();
    try {
      await api.post('/app/api/viewer/reminder/cancel', { login });
      await load();
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'reminder_in_flight') await load();
      reminderFeedback = cause instanceof ApiError && cause.code === 'reminder_in_flight'
        ? 'Отправка уже началась. Проверьте статус через минуту.'
        : 'Не удалось отменить напоминание. Попробуйте ещё раз.';
    } finally { reminderSaving = false; refresh(); }
  }
  async function saveVideoSelection(selectedLogins,{expectedVersion=data.video_selection.version}={}) {
    if (videoSaving) return;
    videoSaving = true;
    ++loadToken;loading=false;pendingLoad=null;
    videoFeedback = 'Сохраняем выбор…';
    refresh();
    try {
      const saved = await api.post('/app/api/viewer/video-selection', {
        selected_logins: selectedLogins,
        expected_version: expectedVersion,
      });
      data.video_selection = saved;
      for (const entry of data.subscriptions) {
        entry.video_selected = saved.selected_logins.includes(entry.login);
        entry.video_effective = entry.video_selected && saved.effective_ids.includes(saved.selected_ids[saved.selected_logins.indexOf(entry.login)]);
      }
      await load({fresh:true});
      videoFeedback = 'Выбор сохранён';
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'version_conflict') {
        await load({fresh:true});
        videoFeedback = 'Выбор изменился в другой сессии. Проверьте список и повторите действие.';
      } else if (cause instanceof ApiError && cause.code === 'plus_required') {
        await load({fresh:true});
        videoFeedback = 'Доступ Viewer Plus завершился. Фото продолжает работать.';
      } else {
        videoFeedback = 'Не удалось сохранить выбор. Попробуйте ещё раз.';
      }
    } finally {
      videoSaving = false;
    }
    refresh();
  }
  function renderCategoryAlert(target, row) {
    const section = element('section', 'panel feature-panel');
    section.append(element('h2', '', 'Смена категории'));
    if (!data.viewer_plus_active) {
      section.append(element('p', 'muted', 'Отдельный сигнал доступен с Viewer Plus. Сохранённый выбор остаётся, пока доступ неактивен.'));
      target.append(section);
      return;
    }
    let draft = categoryDrafts.get(row.login);
    if (!draft) {
      draft = {
        enabled: Boolean(row.category_alert?.enabled),
        ids: [...(row.category_alert?.category_ids || [])],
        names: [...(row.category_alert?.category_names || [])],
        version: row.category_alert?.version || 0,
        query: '', results: [], busy: false, feedback: '',
      };
      categoryDrafts.set(row.login, draft);
    }
    section.append(element('p', 'muted', 'Отдельное сообщение после подтверждённой смены во время эфира. Не чаще одного за 5 минут.'));
    const label = element('label', 'switch-row');
    const checkbox = element('input', '');
    checkbox.type = 'checkbox'; checkbox.name = 'category-alert'; checkbox.checked = draft.enabled;
    checkbox.addEventListener('change', () => { draft.enabled = checkbox.checked; });
    label.append(checkbox, element('span', '', 'Уведомлять о смене категории'));
    section.append(label);
    section.append(element('p', 'muted', draft.ids.length
      ? 'Только выбранные категории' : 'Любая новая категория'));
    const chips = element('div', 'tokens');
    draft.ids.forEach((id, index) => {
      const chip = element('span', 'chip');
      chip.append(element('span', '', draft.names[index] || id));
      const remove = action('×', () => {
        draft.ids.splice(index, 1); draft.names.splice(index, 1); refresh();
      }, true);
      remove.setAttribute('aria-label', `Убрать ${draft.names[index] || id}`);
      chip.append(remove); chips.append(chip);
    });
    section.append(chips);
    const inputRow = element('div', 'token-input-row');
    const input = element('input', 'input');
    input.type = 'search'; input.name = 'category-query'; input.maxLength = 80;
    input.autocomplete = 'off'; input.placeholder = 'Найти категорию Twitch…'; input.value = draft.query;
    input.setAttribute('aria-label', 'Найти категорию Twitch');
    input.addEventListener('input', () => { draft.query = input.value; });
    inputRow.append(input, action('Найти', async () => {
      if (draft.busy || draft.query.trim().length < 2) {
        draft.feedback = 'Введите хотя бы два символа.'; refresh(); return;
      }
      draft.busy = true; draft.feedback = 'Ищем категории…'; refresh();
      try {
        const found = await api.post('/app/api/viewer/category-search', { query: draft.query });
        draft.results = found.results || [];
        draft.feedback = draft.results.length ? '' : 'Ничего не найдено.';
      } catch {
        draft.feedback = 'Поиск сейчас недоступен. Попробуйте ещё раз.';
      } finally { draft.busy = false; refresh(); }
    }, true));
    section.append(inputRow);
    const results = element('div', 'list');
    for (const item of draft.results) {
      if (draft.ids.includes(item.id)) continue;
      results.append(action(item.name, () => {
        if (draft.ids.length >= 5) {
          draft.feedback = 'Можно выбрать до пяти категорий.';
        } else {
          draft.ids.push(item.id); draft.names.push(item.name); draft.feedback = '';
        }
        refresh();
      }, true));
    }
    section.append(results);
    section.append(action('Сохранить сигнал', async () => {
      if (draft.busy) return;
      draft.busy = true; draft.feedback = 'Сохраняем…'; refresh();
      try {
        const saved = await api.post('/app/api/viewer/category-alert', {
          login: row.login, enabled: draft.enabled,
          category_ids: draft.ids, expected_version: draft.version,
        });
        row.category_alert = saved;
        draft.version = saved.version; draft.names = [...saved.category_names];
        draft.feedback = 'Настройка сохранена.';
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) {
          await load();
          draft.version = data?.subscriptions.find((item) => item.login === row.login)?.category_alert?.version || 0;
          draft.feedback = 'Настройка изменилась в другом окне. Проверьте выбор и сохраните ещё раз.';
        } else if (cause instanceof ApiError && cause.status === 403) {
          await load();
          draft.feedback = 'Доступ Viewer Plus завершился. Сигнал сейчас выключен.';
        } else {
          draft.feedback = 'Не удалось сохранить. Попробуйте ещё раз.';
        }
      } finally { draft.busy = false; refresh(); }
    }));
    if (draft.feedback) banner(section, draft.feedback, !['Настройка сохранена.', 'Ищем категории…', 'Сохраняем…'].includes(draft.feedback));
    target.append(section);
  }
  function explainFilter(rule) {
    if (!rule || (!rule.games.length && !rule.title_keywords.length && !rule.exclude_keywords.length)) {
      return 'Дополнительных условий нет. Вы получите обычное оповещение.';
    }
    const conditions = [];
    if (rule.games.length) conditions.push(`категория — ${rule.games.join(' или ')}`);
    if (rule.title_keywords.length) conditions.push(`название содержит ${rule.title_keywords.join(' или ')}`);
    const start = conditions.length
      ? `Бот пришлёт оповещение, когда ${conditions.join(' и ')}.`
      : 'Бот пришлёт обычное оповещение.';
    return rule.exclude_keywords.length
      ? `${start} Названия со словами ${rule.exclude_keywords.join(' или ')} бот пропустит.`
      : start;
  }
  function renderFilter(target, login) {
    const row = data.subscriptions.find((item) => item.login === login);
    if (!row) { heading(target, 'Зритель', 'Стример не найден', 'Вернитесь к подпискам.'); return; }
    heading(target, 'Viewer Plus', `Фильтр · ${nameOf(login)}`, 'Выберите условия для оповещений об эфирах.');
    if (!data.viewer_plus_active) {
      target.append(panel('Доступ к фильтру завершился', 'Правило сохранено. Бот не применяет его без Viewer Plus.'));
      return;
    }
    let draft = filterDrafts.get(login);
    if (!draft) {
      const saved = row.filter;
      draft = {
        version: saved?.version || 0,
        games: [...(saved?.games || [])],
        title_keywords: [...(saved?.title_keywords || [])],
        exclude_keywords: [...(saved?.exclude_keywords || [])],
        inputs: { games: '', title_keywords: '', exclude_keywords: '' },
        feedback: '',
      };
      filterDrafts.set(login, draft);
    }
    const specs = [
      ['games', 'Категория', 'Добавить категорию'],
      ['title_keywords', 'Слова в названии', 'Добавить слово'],
      ['exclude_keywords', 'Исключить слова', 'Добавить исключение'],
    ];
    for (const [key, labelText, addText] of specs) {
      const field = element('section', 'token-field panel');
      const headingNode = element('h2', '', labelText);
      const tokens = element('div', 'tokens');
      for (const term of draft[key]) {
        const chip = element('span', 'chip');
        chip.append(element('span', '', term));
        const remove = action('×', () => {
          draft[key] = draft[key].filter((item) => item !== term);
          refresh();
        }, true);
        remove.setAttribute('aria-label', `Убрать ${term} из ${labelText.toLowerCase()}`);
        chip.append(remove);
        tokens.append(chip);
      }
      const inputRow = element('div', 'token-input-row');
      const input = element('input', 'input');
      input.type = 'text'; input.name = key; input.maxLength = 40;
      input.autocomplete = 'off'; input.value = draft.inputs[key];
      input.setAttribute('aria-label', labelText);
      input.addEventListener('input', () => { draft.inputs[key] = input.value; });
      const addButton = action('Добавить', () => {
        const term = input.value.trim();
        if (term.length < 2 || term.length > 40 || draft[key].length >= 5
            || draft[key].some((item) => item.toLocaleLowerCase() === term.toLocaleLowerCase())) {
          draft.feedback = 'Для каждого поля можно выбрать до 5 разных значений длиной от 2 до 40 символов.';
          refresh();
          return;
        }
        draft[key].push(term);
        draft.inputs[key] = '';
        draft.feedback = '';
        refresh();
      }, true);
      addButton.setAttribute('aria-label', addText);
      inputRow.append(input, addButton);
      field.append(headingNode, tokens, inputRow);
      target.append(field);
    }
    const explanation = element('p', 'notice', explainFilter(draft));
    target.append(explanation);
    if (draft.feedback) banner(target, draft.feedback, draft.feedback !== 'Фильтр сохранён');
    target.append(action('Сохранить фильтр', async () => {
      try {
        const saved = await api.post('/app/api/viewer/filter', {
          login, expected_version: draft.version,
          games: draft.games, title_keywords: draft.title_keywords,
          exclude_keywords: draft.exclude_keywords,
        });
        draft.version = saved.version;
        row.filter = {
          version: saved.version, games: [...draft.games],
          title_keywords: [...draft.title_keywords], exclude_keywords: [...draft.exclude_keywords],
        };
        draft.feedback = 'Фильтр сохранён';
        refresh();
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) {
          await load();
          const currentRow = data?.subscriptions.find((item) => item.login === login);
          draft.version = currentRow?.filter?.version || 0;
          draft.feedback = 'Правило изменилось в другом окне. Проверьте значения и сохраните ещё раз.';
        } else if (cause instanceof ApiError && cause.status === 403) {
          await load();
          draft.feedback = 'Доступ Viewer Plus завершился. Правило сохранено, но сейчас не применяется.';
        } else {
          draft.feedback = 'Не удалось сохранить фильтр. Проверьте значения и попробуйте ещё раз.';
        }
        refresh();
      }
    }));
    if (row.filter) {
      target.append(action(row.folder_id ? 'Использовать правило папки' : 'Убрать личный фильтр', async () => {
        try {
          await api.post('/app/api/viewer/filter/reset', {
            login, expected_version: row.filter.version,
          });
          filterDrafts.delete(login);
          await load();
          getRouter().back();
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 409) await load();
          draft.feedback = cause instanceof ApiError && cause.status === 409
            ? 'Личный фильтр изменился в другом окне. Проверьте его перед удалением.'
            : 'Не удалось убрать личный фильтр. Попробуйте ещё раз.';
          refresh();
        }
      }, true));
    }
  }
  function renderFolder(target, folderId) {
    const folder = data.folders?.find((item) => item.id === folderId);
    if (!folder) {
      heading(target, 'Зритель', 'Папка не найдена', 'Вернитесь к списку стримеров.');
      return;
    }
    heading(target, 'Viewer Plus', folder.name, 'Папка и общее правило для её стримеров.');
    const members = data.subscriptions.filter((row) => row.folder_id === folder.id);
    const memberPanel = element('section', 'panel feature-panel');
    memberPanel.append(element('h2', '', 'Стримеры'));
    memberPanel.append(element('p', 'muted', members.length
      ? `${members.length} в папке. Перенести стримера можно в его настройках.`
      : 'Папка пока пустая. Откройте настройки стримера, чтобы добавить его.'));
    for (const member of members) {
      memberPanel.append(action(nameOf(member.login), () => getRouter().openDetail(member.login), true));
    }
    target.append(memberPanel);
    if (!data.viewer_plus_active) {
      target.append(panel('Правило сохранено', 'Без Viewer Plus оно не влияет на оповещения. Подписки и папка останутся на месте.'));
      return;
    }
    let draft = folderDrafts.get(folderId);
    if (!draft) {
      draft = {
        version: folder.version, name: folder.name,
        games: [...folder.games], title_keywords: [...folder.title_keywords],
        exclude_keywords: [...folder.exclude_keywords],
        inputs: { games: '', title_keywords: '', exclude_keywords: '' },
        feedback: '', busy: false, conflict: false,
      };
      folderDrafts.set(folderId, draft);
    }
    if (folder.version !== draft.version && !draft.busy) draft.conflict = true;
    if (draft.conflict) {
      const conflict = element('section', 'panel feature-panel');
      conflict.append(element('h2', '', 'Папка изменилась'));
      conflict.append(element('p', 'muted', 'Здесь остался ваш черновик. Загрузите текущие данные перед новой правкой.'));
      conflict.append(action('Загрузить текущую версию', () => {
        draft.version = folder.version; draft.name = folder.name;
        draft.games = [...folder.games];
        draft.title_keywords = [...folder.title_keywords];
        draft.exclude_keywords = [...folder.exclude_keywords];
        draft.inputs = { games: '', title_keywords: '', exclude_keywords: '' };
        draft.conflict = false; draft.feedback = '';
        refresh();
      }));
      target.append(conflict);
    }
    const renamePanel = element('section', 'panel feature-panel');
    renamePanel.append(element('h2', '', 'Название'));
    const nameInput = element('input', 'input');
    nameInput.name = 'folder_name'; nameInput.autocomplete = 'off';
    nameInput.value = draft.name; nameInput.maxLength = 40;
    nameInput.setAttribute('aria-label', 'Название папки');
    nameInput.addEventListener('input', () => { draft.name = nameInput.value; });
    renamePanel.append(nameInput);
    const saveName = action('Сохранить название', async () => {
      if (draft.busy || draft.conflict) return;
      draft.busy = true;
      try {
        const response = await api.post('/app/api/viewer/folder/rename', {
          folder_id: folderId, name: draft.name, expected_version: draft.version,
        });
        draft.version = response.folder.version;
        draft.feedback = 'Название сохранено';
        await load();
      } catch (cause) { await folderSaveError(cause, draft, folderId); }
      finally { draft.busy = false; refresh(); }
    });
    saveName.disabled = draft.conflict;
    renamePanel.append(saveName);
    target.append(renamePanel);
    const rule = element('section', 'panel feature-panel');
    rule.append(element('h2', '', 'Общее правило'));
    rule.append(element('p', 'muted', 'Действует, если у стримера нет собственного фильтра. Выключение уведомлений действует отдельно; папка не меняет настройки тихих часов.'));
    for (const [key, labelText] of [
      ['games', 'Категории'], ['title_keywords', 'Слова в названии'],
      ['exclude_keywords', 'Исключить слова'],
    ]) {
      const field = element('div', 'token-field');
      field.append(element('h3', '', labelText));
      const tokens = element('div', 'tokens');
      for (const term of draft[key]) {
        const chip = element('span', 'chip');
        chip.append(element('span', '', term));
        const remove = action('×', () => {
          draft[key] = draft[key].filter((value) => value !== term);
          refresh();
        }, true);
        remove.setAttribute('aria-label', `Убрать ${term} из ${labelText.toLowerCase()}`);
        chip.append(remove); tokens.append(chip);
      }
      const inputRow = element('div', 'token-input-row');
      const input = element('input', 'input');
      input.name = `folder_${key}`; input.autocomplete = 'off';
      input.maxLength = 40; input.value = draft.inputs[key];
      input.setAttribute('aria-label', labelText);
      input.addEventListener('input', () => { draft.inputs[key] = input.value; });
      const add = action('Добавить', () => {
        const term = input.value.trim();
        if (term.length < 2 || term.length > 40 || draft[key].length >= 5
            || draft[key].some((value) => value.toLocaleLowerCase() === term.toLocaleLowerCase())) {
          draft.feedback = 'Для каждого поля: до пяти разных значений от 2 до 40 символов.';
        } else {
          draft[key].push(term); draft.inputs[key] = ''; draft.feedback = '';
        }
        refresh();
      }, true);
      inputRow.append(input, add);
      field.append(tokens, inputRow);
      rule.append(field);
    }
    rule.append(element('p', 'notice', explainFilter(draft)));
    const saveRule = action('Сохранить правило', async () => {
      if (draft.busy || draft.conflict) return;
      draft.busy = true;
      try {
        const response = await api.post('/app/api/viewer/folder/rule', {
          folder_id: folderId, expected_version: draft.version,
          games: draft.games, title_keywords: draft.title_keywords,
          exclude_keywords: draft.exclude_keywords,
        });
        draft.version = response.folder.version;
        draft.feedback = 'Правило сохранено';
        await load();
      } catch (cause) { await folderSaveError(cause, draft, folderId); }
      finally { draft.busy = false; refresh(); }
    });
    saveRule.disabled = draft.conflict;
    rule.append(saveRule);
    if (draft.feedback) rule.append(element('p', 'notice', draft.feedback));
    target.append(rule);
    target.append(action('Удалить папку', async () => {
      const sdk = window.Telegram?.WebApp;
      const confirmed = sdk?.showConfirm
        ? await new Promise((resolve) => sdk.showConfirm(`Удалить папку «${folder.name}»? Подписки останутся.`, resolve))
        : window.confirm(`Удалить папку «${folder.name}»? Подписки останутся.`);
      if (!confirmed || draft.busy) return;
      draft.busy = true;
      try {
        await api.post('/app/api/viewer/folder/delete', {
          folder_id: folderId, expected_version: draft.version,
        });
        folderDrafts.delete(folderId);
        await load();
        getRouter().back();
      } catch (cause) { await folderSaveError(cause, draft, folderId); refresh(); }
      finally { draft.busy = false; }
    }, true));
  }
  async function folderSaveError(cause, draft, folderId) {
    if (cause instanceof ApiError && cause.code === 'folder_name_taken') {
      draft.feedback = 'Папка с таким названием уже есть. Выберите другое.';
    } else if (cause instanceof ApiError && cause.status === 409) {
      await load();
      draft.conflict = true;
      draft.feedback = 'Папка изменилась в другом окне. Черновик не сохранён.';
    } else if (cause instanceof ApiError && cause.status === 403) {
      await load();
      draft.feedback = 'Доступ Viewer Plus завершился. Папка сохранена, правило сейчас не действует.';
    } else {
      draft.feedback = 'Не удалось сохранить папку. Проверьте значения и попробуйте ещё раз.';
    }
  }
  const minuteText = (minute) => `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;
  const parseTime = (text) => {
    if (!/^\d{2}:\d{2}$/.test(text)) return null;
    const [hours, minutes] = text.split(':').map(Number);
    return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
  };
  function renderProfile(target) {
    heading(target, 'Зритель', 'Профиль', 'Настройки и доступ.');
    target.append(panel('Ваши возможности', `${data.subscriptions.length} из ${data.channel_limit} отслеживаемых стримеров. ${data.viewer_plus_active ? 'Viewer Plus активен.' : 'Основные оповещения доступны бесплатно.'}`));
    const access = element('div', 'actions');
    access.append(action('Доступ и история', () => getRouter().openDetail('subscription'), true));
    target.append(access);
    const historyPanel = element('section', 'panel feature-panel');
    historyPanel.append(element('h2', '', 'Личная история'));
    historyPanel.append(element('p', 'muted', data.viewer_plus_active
      ? 'Результаты оповещений об эфирах, смене категории и напоминаний.'
      : 'Лента результатов доступна с Viewer Plus. Обычные оповещения остаются бесплатными.'));
    if (data.viewer_plus_active) {
      historyPanel.append(action('Открыть историю', () => {
        historyState = { events: [], nextBefore: null, loaded: false, loading: false, error: '' };
        getRouter().openDetail('history');
      }, true));
    }
    target.append(historyPanel);
    const quiet = data.quiet_hours;
    if (!quietDraft || !quietDraft.dirty) {
      const offset = quiet?.utc_offset_minutes ?? -new Date().getTimezoneOffset();
      quietDraft = {
        start: quiet ? minuteText((quiet.start_minute + offset + 1440) % 1440) : '23:00',
        end: quiet ? minuteText((quiet.end_minute + offset + 1440) % 1440) : '08:00',
        dirty: false,
      };
    }
    const section = element('section', 'panel quiet-panel');
    section.append(element('h2', '', 'Тихие часы'));
    section.append(element('p', 'muted', 'В это время бот не присылает обычные оповещения. Время берём из часового пояса устройства при сохранении.'));
    const form = element('div', 'time-fields');
    for (const [key, labelText] of [['start', 'Начало тихих часов'], ['end', 'Конец тихих часов']]) {
      const label = element('label', '', labelText);
      const input = element('input', 'input');
      input.type = 'time'; input.name = key; input.value = quietDraft[key];
      input.addEventListener('input', () => { quietDraft[key] = input.value; quietDraft.dirty = true; });
      label.append(input);
      form.append(label);
    }
    section.append(form);
    const buttons = element('div', 'actions');
    const saveQuiet = action('Сохранить', async () => {
      const start = parseTime(quietDraft.start);
      const end = parseTime(quietDraft.end);
      if (start === null || end === null || start === end) {
        profileFeedback = 'Укажите разное время начала и конца.';
        refresh();
        return;
      }
      const offset = -new Date().getTimezoneOffset();
      try {
        const response = await api.post('/app/api/viewer/quiet-hours', {
          start_minute: (start - offset + 1440) % 1440,
          end_minute: (end - offset + 1440) % 1440,
          utc_offset_minutes: offset,
        });
        data.quiet_hours = response.quiet_hours;
        quietDraft.dirty = false;
        profileFeedback = 'Тихие часы сохранены';
      } catch { profileFeedback = 'Не удалось сохранить тихие часы. Попробуйте ещё раз.'; }
      refresh();
    });
    saveQuiet.setAttribute('aria-label', 'Сохранить тихие часы');
    buttons.append(saveQuiet);
    if (quiet) buttons.append(action('Выключить', async () => {
      try {
        await api.post('/app/api/viewer/quiet-hours', { clear: true });
        data.quiet_hours = null;
        quietDraft.dirty = false;
        profileFeedback = 'Тихие часы выключены';
      } catch { profileFeedback = 'Не удалось выключить тихие часы. Попробуйте ещё раз.'; }
      refresh();
    }, true));
    section.append(buttons);
    const digestLabel = element('label', 'switch-row');
    const digest = element('input', '');
    digest.type = 'checkbox'; digest.name = 'digest';
    digest.checked = Boolean(quiet?.digest_enabled);
    digest.disabled = !quiet;
    digestLabel.append(digest, element('span', '', 'Сводка после тихих часов'));
    section.append(digestLabel);
    const digestStatus = element('p', 'muted', quiet
      ? (quiet.digest_enabled ? 'Сводка включена' : 'Сводка выключена')
      : 'Сначала сохраните тихие часы.');
    section.append(digestStatus);
    digest.addEventListener('change', async () => {
      const enabled = digest.checked;
      digest.disabled = true;
      try {
        await api.post('/app/api/viewer/digest', { enabled });
        data.quiet_hours.digest_enabled = enabled;
        digestStatus.textContent = enabled ? 'Сводка включена' : 'Сводка выключена';
      } catch {
        digest.checked = !enabled;
        digestStatus.textContent = 'Не удалось сохранить сводку. Попробуйте ещё раз.';
      } finally { digest.disabled = false; }
    });
    if (profileFeedback) {
      const note = element('p', 'notice', profileFeedback);
      note.setAttribute('role', 'status');
      section.append(note);
      profileFeedback = '';
    }
    target.append(section);
  }
  async function loadHistory(reset = false) {
    if (historyState.loading) return;
    if (reset) historyState = { events: [], nextBefore: null, loaded: false, loading: false, error: '' };
    historyState.loading = true;
    refresh();
    try {
      const payload = await api.post('/app/api/viewer/history', {
        limit: 20, before_id: historyState.nextBefore,
      });
      historyState.events.push(...payload.events);
      historyState.nextBefore = payload.next_before_id;
      historyState.loaded = true;
      historyState.error = '';
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        historyState.events = [];
        historyState.nextBefore = null;
        if (cause.code === 'plus_required') {
          historyState.error = 'Доступ Viewer Plus завершился. Сохранённая история недоступна без него.';
          await load();
        } else {
          historyState.error = 'Сессия Telegram устарела. Закройте и откройте приложение снова.';
        }
      } else {
        historyState.error = 'Не удалось загрузить историю. Попробуйте ещё раз.';
      }
    } finally { historyState.loading = false; historyState.loaded = true; refresh(); }
  }
  function renderHistory(target) {
    heading(target, 'Зритель', 'Личная история', 'Только результаты ваших оповещений.');
    if (!data.viewer_plus_active) {
      target.append(panel('Viewer Plus неактивен', 'История сохранена до технической очистки, но сейчас недоступна.'));
      target.append(action('Посмотреть доступ', () => getRouter().openDetail('subscription'), true));
      return;
    }
    if (!historyState.loaded && !historyState.loading) void loadHistory();
    if (historyState.loading && !historyState.loaded) {
      target.append(panel('Загружаем историю…', 'Это может занять несколько секунд.'));
    }
    if (historyState.error) banner(target, historyState.error, true);
    if (historyState.loaded && !historyState.events.length && !historyState.error) {
      target.append(panel('Пока нет событий', 'Здесь появятся результаты оповещений после подтверждённых эфиров и напоминаний.'));
    }
    const list = element('div', 'list');
    for (const event of historyState.events) {
      const item = element('div', 'list-row');
      const copy = element('div', 'row-copy');
      const title = event.kind === 'go_live' ? 'Оповещение об эфире'
        : event.kind === 'viewer_category_change' ? 'Смена категории'
        : 'Напоминание об эфире';
      const result = event.outcome === 'sent' ? 'Отправлено'
        : event.outcome === 'suppressed' ? 'Не отправлено'
        : 'Доставка не подтверждена';
      copy.append(element('strong', '', `${title} · ${nameOf(event.login)}`));
      if (event.category_name) copy.append(element('small', '', event.category_name));
      copy.append(element('small', '', `${result} · ${new Date(event.happened_at * 1000).toLocaleString('ru-RU')}`));
      item.append(copy); list.append(item);
    }
    if (historyState.events.length) target.append(list);
    if (historyState.nextBefore !== null) {
      const more = action(historyState.loading ? 'Загружаем…' : 'Показать ещё', () => void loadHistory(), true);
      more.disabled = historyState.loading;
      target.append(more);
    } else if (historyState.error && !historyState.loading) {
      target.append(action('Повторить', () => void loadHistory(), true));
    }
  }
  return {
    render(target, route) {
      target.dataset.viewerState=data?'ready':error?'error':'loading';
      if (route.detail !== lastDetail) {
        videoFeedback = '';
        planFeedback = '';
        lastDetail = route.detail;
      }
      target.replaceChildren();
      if (data && !loading && Date.now() - lastLoadedAt > 30000) void load();
      if (!data) {
        if (!requested) queueMicrotask(() => { if (!requested) void load(); });
        target.append(element('p', 'eyebrow', 'Зритель'), element('h1', '', 'Загружаем подписки…'));
        if (error) {
          banner(target, error, true);
          target.append(action('Повторить загрузку', () => void load()));
        }
        return;
      }
      if (error) banner(target, error, true);
      if (route.detail === 'video-selection') renderVideoPicker(target);
      else if (route.detail === 'history') renderHistory(target);
      else if (route.detail?.startsWith('folder:')) renderFolder(target, route.detail.slice(7));
      else if (route.detail?.startsWith('filter:')) renderFilter(target, route.detail.slice(7));
      else if (route.detail) renderDetail(target, route.detail);
      else if (route.tab === 'home') renderHome(target);
      else if (route.tab === 'streamers') renderStreamers(target);
      else renderProfile(target);
    },
    refresh: load,
    dispose(){disposed=true;++loadToken;clearTimeout(searchTimer);searchController?.abort();++searchVersion;document.removeEventListener('visibilitychange',onVisibility);},
  };
}
