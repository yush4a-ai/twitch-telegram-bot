import { ApiError } from './api.js';
import { element, panel, action, dialog, icon, navigationRow, avatar } from './components.js';

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
  let quietSaving=false;
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
  let historyToken=0,historyController=null;
  let lastDetail = null;
  let listDraft = (api.storage.getItem('ts-viewer-subscription-search') || '').slice(0,200);
  const notifySaving=new Set(),notifyPending=new Map();
  const favoriteSaving=new Set(),removeSaving=new Set();
  let mutationRevision=0,reloadPending=false;
  const mutating=()=>favoriteSaving.size||removeSaving.size||notifySaving.size||undoBusy;
  const flushReload=()=>{if(reloadPending&&!loading&&!mutating()&&!disposed)void load({fresh:true});};
  let openSwipe=null;
  let favoritesOnly=api.storage.getItem('ts-viewer-favorites-only')==='true';
  let undo=null,undoTimer=null,undoBusy=false;
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
    if(mutating()){reloadPending=true;return;}
    if (loading&&!fresh) return pendingLoad;
    reloadPending=false;
    requested = true;
    loading = true;
    lastLoadedAt = Date.now();
    if (!data) refresh();
    const token=++loadToken, revision=mutationRevision;
    pendingLoad=(async()=>{
      try {
        const loaded=await api.post('/app/api/viewer/state');
        if(!disposed&&token===loadToken){if(revision===mutationRevision&&!mutating()){data=loaded;error='';}else reloadPending=true;}
      } catch (cause) {
        if(token===loadToken)error=cause instanceof ApiError && [401,403].includes(cause.status)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : data?'Нет связи. Показываем последние загруженные данные.':'Не удалось загрузить подписки. Проверьте связь и повторите.';
      } finally { if(token===loadToken){loading=false;pendingLoad=null;refresh();flushReload();} }
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
    target.append(element('h1','home-title','Главная'));
    const tracked = data.subscriptions;
    const live = tracked.filter((row) => row.status === 'live').sort((a,b)=>Number(Boolean(b.is_favorite))-Number(Boolean(a.is_favorite)));
    const stale = tracked.some((row) => row.status === 'stale');

    if (!tracked.length) {
      const empty=panel('Ваш первый стример','Добавьте Twitch-канал. Бот пришлёт оповещение в личный чат, когда начнётся эфир.');empty.classList.add('branded-empty');const mascot=element('img','empty-mascot');mascot.src='/app/mascot-cutout.png';mascot.alt='';mascot.width=112;mascot.height=112;empty.prepend(mascot);target.append(empty);
    } else if (!live.length) {
      target.append(panel(stale ? 'Проверяем статус эфиров' : 'Пока нет подтверждённого эфира', 'Бот обновит статус после следующей проверки. Ваши подписки остаются в разделе «Стримеры».'));
    } else {
      const head=element('section','home-live-heading'),copy=element('div','home-hero-copy');copy.append(element('h2','','Кто сейчас в эфире'),element('p','','Ваши стримеры — рядом'));head.append(copy);const mascot=element('img','home-mascot');mascot.src='/app/mascot-cutout.png';mascot.alt='';mascot.width=168;mascot.height=168;head.append(mascot);const note=element('p','home-delivery-note');note.append(icon('notification'),element('span','','Оповещения в личном чате'));head.append(note);target.append(head);
      const title=element('div','section-head');title.append(element('h2','','В эфире'),element('span','muted',String(live.length)));target.append(title);
      const list = element('div', 'streamer-group home-streamers');
      for (const row of live) {
        const item = streamerRow(row,{home:true});
        const link = element('a', 'text-link watch-link', 'Смотреть');
        link.href = `https://www.twitch.tv/${row.login}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label',`Смотреть ${nameOf(row.login)} на Twitch`);item.append(link);
        list.append(item);
      }
      target.append(list);
    }
    const actions = element('div', 'actions');
    if(tracked.length){const all=action('Мои стримеры',()=>getRouter().setTab('streamers'),true);all.className='home-streamers-link';all.append(icon('chevron'));actions.append(all);}else actions.append(action('Добавить стримера',openAdd));
    target.append(actions);
    const upcoming=tracked.filter(row=>row.reminder?.status==='scheduled').sort((a,b)=>a.reminder.due_at-b.reminder.due_at);
    if(upcoming.length){const box=element('section','navigation-group');box.append(element('h2','group-heading','Напоминания'));for(const row of upcoming)box.append(action(`${nameOf(row.login)} · ${new Date(row.reminder.due_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})}`,()=>getRouter().openDetail(row.login),true));target.append(box);}
  }
  function streamerRow(row,{home=false}={}){
    const name=nameOf(row.login),item=element('div','list-row streamer-row');item.dataset.rowKey=row.login;
    const portrait=avatar(name,row.avatar_url);
    const open=action('',()=>getRouter().openDetail(row.login),true);open.className='streamer-open';open.setAttribute('aria-label',`Открыть ${name}`);open.dataset.focusKey=`streamer:${row.login}`;open.dataset.openStreamer='';
    const copy=element('span','row-copy');copy.append(element('strong','streamer-name',name));
    const status=element('small',`stream-status ${row.status}`,row.status==='live'?'В эфире':row.status==='stale'?'Статус уточняется':'Не в эфире');copy.append(status);
    if(row.status==='live'&&row.live?.category)copy.append(element('small','muted',row.live.category));
    if(home&&row.live?.title)copy.append(element('small','stream-title',`«${row.live.title}»`));
    if(row.paused_by_plan)copy.append(element('small','muted','Приостановлено по лимиту'));
    else if(!row.notify_enabled)copy.append(element('small','muted','Уведомления на паузе'));
    const folder=data.folders?.find(value=>value.id===row.folder_id);if(folder)copy.append(element('small','muted',folder.name));
    open.append(portrait,copy);item.append(open);
    if(!home){const label=element('label','quick-notify'),input=element('input','');input.type='checkbox';input.checked=notifyPending.get(row.login)??row.notify_enabled;input.setAttribute('aria-label',`Уведомления ${name}`);input.dataset.focusKey=`notify:${row.login}`;
      if(notifySaving.has(row.login)||removeSaving.has(row.login))input.setAttribute('aria-disabled','true');
      input.addEventListener('change',()=>saveNotify(row.login,input.checked));label.append(input,icon('notification'));item.append(label);
      const star=action('',async()=>{
        if(favoriteSaving.has(row.login)||removeSaving.has(row.login))return;
        favoriteSaving.add(row.login);++mutationRevision;star.disabled=true;
        try{
          const result=await api.post('/app/api/viewer/favorite',{login:row.login,is_favorite:!row.is_favorite});
          if(disposed)return;
          const currentRow=data.subscriptions.find(item=>item.login===row.login);
          if(currentRow)currentRow.is_favorite=result.is_favorite;
          feedback=result.is_favorite?`${name} добавлен в избранное`:`${name} убран из избранного`;
        }catch{if(!disposed)feedback='Не удалось сохранить избранное. Попробуйте ещё раз.';}
        finally{++mutationRevision;favoriteSaving.delete(row.login);refresh();flushReload();}
      },true);
      star.className='favorite-toggle';star.append(icon('star'));star.setAttribute('aria-pressed',String(Boolean(row.is_favorite)));star.setAttribute('aria-label',`${row.is_favorite?'Убрать':'Добавить'} ${name} ${row.is_favorite?'из избранного':'в избранное'}`);star.dataset.focusKey=`favorite:${row.login}`;star.disabled=favoriteSaving.has(row.login)||removeSaving.has(row.login);item.append(star);
      return swipeRow(item,row,name);
    }
    return item;
  }
  async function saveNotify(login,enabled){
    if(notifySaving.has(login)||removeSaving.has(login))return;
    notifySaving.add(login);notifyPending.set(login,enabled);++mutationRevision;refresh();
    try{await api.post('/app/api/viewer/notify',{login,enabled});if(disposed)return;const row=data.subscriptions.find(item=>item.login===login);if(row)row.notify_enabled=enabled;feedback=enabled?`Уведомления ${nameOf(login)} включены`:`Уведомления ${nameOf(login)} на паузе`;}
    catch{if(!disposed)feedback='Не удалось сохранить уведомления. Попробуйте ещё раз.';}
    finally{++mutationRevision;notifySaving.delete(login);notifyPending.delete(login);refresh();flushReload();}
  }
  async function removeSubscription(row,onRemoved){
    const name=nameOf(row.login);
      if(removeSaving.size||favoriteSaving.has(row.login)||notifySaving.has(row.login)||undoBusy)return false;
      removeSaving.add(row.login);++mutationRevision;refresh();let removed=false;
      try{
        const result=await api.post('/app/api/viewer/unfollow',{login:row.login});
        if(disposed)return false;
        data.subscriptions=data.subscriptions.filter(item=>item.login!==row.login);data.video_selection=result.video_selection;
        feedback=`${name} удалён из подписок`;openSwipe=null;removed=true;
        clearTimeout(undoTimer);undo=result.undo_token?{token:result.undo_token,expires:result.undo_expires_at,name,message:feedback}:null;
        if(undo)undoTimer=setTimeout(()=>{undo=null;refresh();},Math.max(0,undo.expires*1000-Date.now()));
        onRemoved?.();
      }catch{if(!disposed)feedback='Не удалось удалить подписку. Попробуйте ещё раз.';}
      finally{++mutationRevision;removeSaving.delete(row.login);refresh();flushReload();}
      if(removed)await load({fresh:true});
    return removed;
  }
  function swipeRow(item,row,name){
    const shell=element('div','swipe-shell');shell.dataset.rowKey=row.login;delete item.dataset.rowKey;
    const remove=action('Удалить',()=>removeSubscription(row));
    remove.className='swipe-delete';remove.disabled=removeSaving.has(row.login);remove.dataset.focusKey=`delete:${row.login}`;
    let start=null,horizontal=false,vertical=false,suppressClickUntil=0;
    const setOpen=value=>{shell.classList.toggle('swipe-open',value);remove.tabIndex=value?0:-1;if(value){if(openSwipe&&openSwipe!==shell){openSwipe.classList.remove('swipe-open');openSwipe.querySelector('.swipe-delete').tabIndex=-1;}openSwipe=shell;}else if(openSwipe===shell)openSwipe=null;};
    setOpen(false);
    shell.addEventListener('pointerdown',event=>{
      if(event.button>0||event.target.closest('input,.quick-notify,.favorite-toggle,.swipe-delete'))return;
      start={x:event.clientX,y:event.clientY};horizontal=false;vertical=false;
    });
    shell.addEventListener('pointermove',event=>{
      if(!start||vertical)return;
      const dx=event.clientX-start.x,dy=event.clientY-start.y;
      if(!horizontal&&Math.abs(dy)>12&&Math.abs(dy)>Math.abs(dx)){vertical=true;return;}
      if(Math.abs(dx)>16&&Math.abs(dx)>Math.abs(dy)*1.3){horizontal=true;if(event.cancelable)event.preventDefault();}
    },{passive:false});
    shell.addEventListener('pointerup',event=>{
      if(start&&horizontal&&!vertical){const dx=event.clientX-start.x;if(dx<-44)setOpen(true);else if(dx>44)setOpen(false);suppressClickUntil=performance.now()+350;}
      start=null;
    });
    shell.addEventListener('pointercancel',()=>{start=null;});
    shell.addEventListener('click',event=>{if(performance.now()<suppressClickUntil&&!event.target.closest('.swipe-delete')){event.preventDefault();event.stopImmediatePropagation();}},true);
    shell.addEventListener('keydown',event=>{if(event.key==='Escape')setOpen(false);});
    shell.append(remove,item);return shell;
  }
  function renderUndo(target){
    if(!undo||Date.now()>=undo.expires*1000)return;
    const box=element('aside','undo-notice'),text=element('p','',undo.message);text.setAttribute('role','status');
    const restore=action(undoBusy?'Возвращаем…':'Отменить',async()=>{
      if(undoBusy)return;const pending=undo;undoBusy=true;++mutationRevision;refresh();
      try{
        await api.post('/app/api/viewer/unfollow/undo',{undo_token:pending.token});
        if(disposed)return;clearTimeout(undoTimer);undo=null;feedback=`${pending.name}: подписка и настройки восстановлены`;
      }catch(cause){
        if(undo===pending)undo.message=cause?.code==='channel_limit'?'Лимит заполнен. Освободите место и повторите отмену.':cause?.status===409?'Вернуть подписку не удалось: срок отмены истёк или настройки уже изменились.':'Не удалось вернуть подписку. Проверьте связь и повторите.';
      }finally{undoBusy=false;++mutationRevision;refresh();flushReload();}
      await load({fresh:true});
    },true);restore.setAttribute('aria-label','Отменить удаление');restore.disabled=undoBusy;box.append(text,restore);target.append(box);const reserve=box.cloneNode(true);reserve.classList.add('undo-reserve');reserve.setAttribute('aria-hidden','true');reserve.inert=true;target.append(reserve);
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
          await load({fresh:true});
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
    const head=element('div','title-row');head.append(element('h1','','Стримеры'),action('Добавить стримера',openAdd));target.append(head);
    target.append(element('p','lead',`${data.subscriptions.length} из ${data.channel_limit} стримеров · ${data.subscriptions.filter(row=>row.notify_enabled&&!row.paused_by_plan).length} с уведомлениями`));
    const input = element('input', 'input');
    input.type='search';input.name='subscription_search';input.maxLength=200;input.autocomplete='off';input.placeholder='Поиск по подпискам';input.setAttribute('aria-label','Поиск по подпискам');input.value=listDraft;
    const groups=element('div','subscription-groups');input.addEventListener('input',()=>{listDraft=input.value;api.storage.setItem('ts-viewer-subscription-search',listDraft);renderGroups(groups);});target.append(input);
    if (feedback) {
      const note = element('p', 'notice', feedback);
      note.setAttribute('role', 'status');
      note.setAttribute('data-feedback', '');
      target.append(note);
    }
    const tools=element('div','viewer-tools');
    const favoriteFilter=action('Только избранные',()=>{favoritesOnly=!favoritesOnly;api.storage.setItem('ts-viewer-favorites-only',String(favoritesOnly));refresh();},true);favoriteFilter.setAttribute('aria-pressed',String(favoritesOnly));favoriteFilter.dataset.focusKey='favorites-filter';tools.append(favoriteFilter);
    tools.append(action(data.viewer_plus_active?`Видео · ${data.video_selection.selected_logins.length}/${data.video_selection.limit}`:'Видео',()=>getRouter().openDetail(data.viewer_plus_active?'video-selection':'subscription'),true));tools.append(action(data.viewer_plus_active||data.folders?.length?'Папки':'Папки',()=>getRouter().openDetail(data.viewer_plus_active||data.folders?.length?'folders':'subscription'),true));target.append(tools);
    renderGroups(groups);target.append(groups);
  }
  function renderGroups(target){
    target.replaceChildren();
    if(!data.subscriptions.length){target.append(panel('Добавьте первого стримера','Найдите его по нику Twitch или вставьте ссылку на канал.'));return;}
    const query=listDraft.trim().toLocaleLowerCase('ru-RU');
    const rows=data.subscriptions.filter(row=>(!favoritesOnly||row.is_favorite)&&`${nameOf(row.login)} ${row.login}`.toLocaleLowerCase('ru-RU').includes(query));
    if(!rows.length){target.append(panel(favoritesOnly?'Избранных не найдено':'В подписках ничего не найдено',favoritesOnly?'Измените поиск или выключите фильтр. Добавить в избранное можно звёздочкой рядом с уведомлениями.':'Измените запрос или добавьте нового стримера.'));return;}
    const statuses=[['live','В эфире'],['stale','Статус уточняется'],['offline','Не в эфире']];
    const ordered=statuses.flatMap(([status])=>rows.filter(row=>row.status===status));
    for(const [status,title] of [['favorite','Избранное'],...statuses]){
      const selected=status==='favorite'?ordered.filter(row=>row.is_favorite):rows.filter(row=>!row.is_favorite&&row.status===status);if(!selected.length)continue;
      const section=element('section','streamer-section'),heading=element('div','section-head');heading.append(element('h2','',title),element('small','muted',String(selected.length)));section.append(heading);
      const list=element('div','streamer-group');for(const row of selected)list.append(streamerRow(row));section.append(list);target.append(section);
    }
  }
  function openAdd(event){
    dialog('Добавить стримера',(content,close)=>{
      const label=element('label','search-field','Ник или ссылка Twitch'),input=element('input','input'),results=element('div','search-results');input.type='search';input.name='channel_search';input.maxLength=200;input.autocomplete='off';input.spellcheck=false;input.value=searchDraft;input.placeholder='Например, twitch.tv/alpha';label.append(input);results.setAttribute('role','status');results.onDone=close;
      input.addEventListener('input',()=>queueSearch(input,results));content.append(element('p','muted','Оповещения будут приходить в личный чат с ботом. Их можно отключить для каждого стримера.'),label,results);if(searchResults.length)showResults(results);else if(searchDraft.trim().length>=4)queueSearch(input,results);
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
    if(!data.viewer_plus_active){target.append(panel('Фото остаётся доступно','Выбор видео сохранён. Для его использования нужен Зритель Plus.'),action('Посмотреть тариф',()=>getRouter().openDetail('subscription'),true));return;}
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
    target.append(element('h1','','Папки'));
    if (!data.viewer_plus_active && !(data.folders || []).length) {target.append(panel('Папки доступны с Plus','Подписки и основные уведомления доступны бесплатно.'));return;}
    const section = element('section', 'panel feature-panel');
    section.className='settings-group feature-panel';
    section.append(element('p', 'muted', data.viewer_plus_active
      ? 'Группируйте стримеров и задавайте общее правило оповещений.'
      : 'Папки сохранены. Их правила снова заработают с тарифом «Зритель Plus».'));
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
        const origin=current(),name=input.value;
        folderSaving = true;refresh();
        try {
          const response = await api.post('/app/api/viewer/folder/create', { name });
          folderNameDraft = ''; folderFeedback = '';
          await load({fresh:true});
          if(current()===origin)getRouter().openDetail(`folder:${response.folder.id}`);
        } catch (cause) {
          folderFeedback = cause instanceof ApiError && cause.code === 'folder_limit'
            ? 'Мест для папок больше нет. Удалите пустую папку и повторите.'
            : cause instanceof ApiError && cause.code === 'folder_name_taken'
            ? 'Папка с таким названием уже есть.'
            : 'Не удалось создать папку. Проверьте название и попробуйте ещё раз.';
          refresh();
        } finally { folderSaving = false;refresh(); }
      });
      input.disabled=folderSaving;create.disabled=folderSaving;field.append(input, create);
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
    checkbox.type = 'checkbox'; checkbox.name = 'notify'; checkbox.checked = notifyPending.get(login)??row.notify_enabled;
    label.append(checkbox, element('span', '', 'Уведомлять об эфирах'));
    const status = element('p', 'muted', row.notify_enabled ? 'Уведомления включены' : 'Уведомления выключены');
    checkbox.disabled=notifySaving.has(login)||removeSaving.has(login);
    checkbox.addEventListener('change',()=>saveNotify(login,checkbox.checked));
    if(feedback)banner(target,feedback);
    settings.append(label, status);
    target.append(settings);
    if (row.paused_by_plan) {
      const paused = element('div', 'panel feature-panel');
      paused.append(element('h2', '', 'Приостановлено по лимиту'));
      paused.append(element('p', 'muted', 'Подписка сохранена. Можно включить её в активные 50. Последняя активная подписка тогда приостановится.'));
      paused.append(action('Включить в активные 50', async () => {
        try {
          await api.post('/app/api/viewer/plan-activate', { login });
          planFeedback = '';
          await load({fresh:true});
        } catch {
          planFeedback = 'Не удалось изменить активные подписки. Попробуйте ещё раз.';
          refresh();
        }
      }));
      if (planFeedback) paused.append(element('p', 'notice error', planFeedback));
      target.append(paused);
    }
    const group=element('section','navigation-group');
    const gated=route=>()=>getRouter().openDetail(data.viewer_plus_active?route:'subscription');
    group.append(navigationRow('Отчёты об эфирах','Автоотчёт и формат после трансляции','chart',()=>getRouter().openDetail({name:'reports',id:`${api.user.id}:${login}`}),'reports'));
    group.append(navigationRow('Видеопревью',videoStatus(row),'video',gated('video-selection'),'video'));
    group.append(navigationRow('Фильтры уведомлений',data.viewer_plus_active?explainFilter(row.filter||data.folders?.find(value=>value.id===row.folder_id)):'Посмотреть тариф','filter',gated(`filter:${login}`),'filter'));
    group.append(navigationRow('Категории',data.viewer_plus_active?(row.category_alert?.enabled?'Сигнал включён':'Сигнал выключен'):'Посмотреть тариф','notification',gated(`category:${login}`),'category'));
    group.append(navigationRow('Напоминание',row.reminder?.status==='scheduled'?`Через ${row.reminder.delay_minutes} минут`:data.viewer_plus_active?'15 или 30 минут во время эфира':'Посмотреть тариф','clock',gated(`reminder:${login}`),'reminder'));
    group.append(navigationRow('Папка',data.folders?.find(value=>value.id===row.folder_id)?.name||'Без папки','folder',gated(`move:${login}`),'folder'));target.append(group);
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
      await removeSubscription(row,()=>{if(current().detail===login)getRouter().back();});
    }, true));
    target.append(actions);
  }
  function renderFolderMove(target,row){
    const login=row.login; target.append(element('h1','','Папка'),element('p','lead',nameOf(login)));
    if (data.viewer_plus_active && (data.folders || []).length) {
      const section = element('section', 'panel feature-panel');
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
        folderSaving = true;refresh();
        try {
          await api.post('/app/api/viewer/folder/move', {
            login, folder_id: selector.value || null,
            expected_folder_id: row.folder_id || null,
          });
          folderMoveDrafts.delete(login);
          folderFeedback = 'Папка сохранена';
          await load({fresh:true});
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 409) await load({fresh:true});
          folderFeedback = cause instanceof ApiError && cause.status === 409
            ? 'Папка изменилась в другом окне. Проверьте выбор.'
            : 'Не удалось сохранить папку. Попробуйте ещё раз.';
          refresh();
        } finally { folderSaving = false; refresh(); }
      });
      selector.disabled=folderSaving;save.disabled=folderSaving;section.append(selector, save);
      if (folderFeedback) section.append(element('p', 'notice error', folderFeedback));
      target.append(section);
    }
    if(!data.viewer_plus_active)target.append(panel('Папки доступны с Plus','Ваши подписки остаются на месте.'));
    else if(!data.folders?.length)target.append(panel('Сначала создайте папку','Потом в неё можно перенести стримера.'),action('Мои папки',()=>getRouter().openDetail('folders'),true));
  }
  function renderReminder(target,row){
    const login=row.login;target.append(element('h1','','Напоминание'),element('p','lead',nameOf(login)));
    if (data.viewer_plus_active && (row.status === 'live' || row.reminder)) {
      const reminder = element('div', 'settings-group feature-panel');
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
      if(saved&&['scheduled','sending'].includes(saved.status)&&Number.isFinite(saved.due_at)){
        const time=element('time','muted',`Время напоминания: ${new Date(saved.due_at*1000).toLocaleString('ru-RU')}`);
        time.dateTime=new Date(saved.due_at*1000).toISOString();time.dataset.reminderDue='';reminder.append(time);
      }
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
      target.append(panel('Напоминание · Зритель Plus', 'Во время эфира можно попросить напомнить через 15 или 30 минут.'));
    }
    if(data.viewer_plus_active&&row.status!=='live'&&!row.reminder)target.append(panel('Сейчас нет подтверждённого эфира','Напоминание можно выбрать во время эфира.'));
  }
  async function saveReminder(login, delayMinutes) {
    if (reminderSaving) return;
    reminderSaving = true;
    reminderFeedback = '';
    refresh();
    try {
      await api.post('/app/api/viewer/reminder', { login, delay_minutes: delayMinutes });
      await load({fresh:true});
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'reminder_in_flight') await load({fresh:true});
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
      await load({fresh:true});
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'reminder_in_flight') await load({fresh:true});
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
        videoFeedback = 'Срок тарифа «Зритель Plus» истёк. Фото продолжает работать.';
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
      section.append(element('p', 'muted', 'Отдельный сигнал доступен с тарифом «Зритель Plus». Сохранённый выбор остаётся, пока доступ неактивен.'));
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
        query: '', results: [], busy: false, searchBusy:false, searchVersion:0, controller:null, feedback: '',
      };
      categoryDrafts.set(row.login, draft);
    }
    section.append(element('p', 'muted', 'Отдельное сообщение после подтверждённой смены во время эфира. Не чаще одного за 5 минут.'));
    const label = element('label', 'switch-row');
    const checkbox = element('input', '');
    checkbox.type = 'checkbox'; checkbox.name = 'category-alert'; checkbox.checked = draft.enabled;
    checkbox.disabled=draft.busy;
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
      remove.disabled=draft.busy;
      chip.append(remove); chips.append(chip);
    });
    section.append(chips);
    const inputRow = element('div', 'token-input-row');
    const input = element('input', 'input');
    input.type = 'search'; input.name = 'category-query'; input.maxLength = 80;
    input.autocomplete = 'off'; input.placeholder = 'Найти категорию Twitch…'; input.value = draft.query;
    input.setAttribute('aria-label', 'Найти категорию Twitch');
    input.disabled=draft.busy;
    input.addEventListener('input', () => { draft.query = input.value; ++draft.searchVersion;draft.controller?.abort();draft.searchBusy=false;draft.results=[];draft.feedback='';results.replaceChildren();searchButton.disabled=false;section.querySelector('[role=status]')?.remove(); });
    const searchButton=action('Найти', async () => {
      if(draft.busy||draft.searchBusy)return;
      if (draft.query.trim().length < 2) {
        draft.feedback = 'Введите хотя бы два символа.'; refresh(); return;
      }
      const version=++draft.searchVersion,query=draft.query;draft.controller=new AbortController();
      draft.searchBusy = true; draft.feedback = 'Ищем категории…'; refresh();
      try {
        const found = await api.post('/app/api/viewer/category-search', { query },{signal:draft.controller.signal});
        if(version!==draft.searchVersion||disposed)return;
        draft.results = found.results || [];
        draft.feedback = draft.results.length ? '' : 'Ничего не найдено.';
      } catch(cause) {
        if(cause.name!=='AbortError'&&version===draft.searchVersion)draft.feedback = 'Поиск сейчас недоступен. Попробуйте ещё раз.';
      } finally { if(version===draft.searchVersion){draft.searchBusy=false;refresh();} }
    }, true);searchButton.disabled=draft.busy||draft.searchBusy;inputRow.append(input,searchButton);
    section.append(inputRow);
    const results = element('div', 'list');
    for (const item of draft.results) {
      if (draft.ids.includes(item.id)) continue;
      const choose=action(item.name, () => {
        if (draft.ids.length >= 5) {
          draft.feedback = 'Можно выбрать до пяти категорий.';
        } else {
          draft.ids.push(item.id); draft.names.push(item.name); draft.feedback = '';
        }
        refresh();
      }, true);choose.disabled=draft.busy;results.append(choose);
    }
    section.append(results);
    const saveSignal=action('Сохранить сигнал', async () => {
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
          await load({fresh:true});
          draft.version = data?.subscriptions.find((item) => item.login === row.login)?.category_alert?.version || 0;
          draft.feedback = 'Настройка изменилась в другом окне. Проверьте выбор и сохраните ещё раз.';
        } else if (cause instanceof ApiError && cause.status === 403) {
          await load({fresh:true});
          draft.feedback = 'Срок тарифа «Зритель Plus» истёк. Сигнал сейчас выключен.';
        } else {
          draft.feedback = 'Не удалось сохранить. Попробуйте ещё раз.';
        }
      } finally { draft.busy = false; refresh(); }
    });saveSignal.disabled=draft.busy;section.append(saveSignal);
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
    target.append(element('h1','','Фильтры'),element('p','lead',nameOf(login)));
    if (!data.viewer_plus_active) {
      target.append(panel('Доступ к фильтру завершился', 'Правило сохранено. Бот не применяет его без Зритель Plus.'));
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
        feedback: '',busy:false,
      };
      filterDrafts.set(login, draft);
    }
    const specs = [
      ['games', 'Категория', 'Добавить категорию'],
      ['title_keywords', 'Слова в названии', 'Добавить слово'],
      ['exclude_keywords', 'Исключить слова', 'Добавить исключение'],
    ];
    for (const [key, labelText, addText] of specs) {
      const field = element('section', 'token-field settings-group');
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
      if(draft.busy)return;draft.busy=true;draft.feedback='';refresh();
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
        await load({fresh:true});
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) {
          await load({fresh:true});
          const currentRow = data?.subscriptions.find((item) => item.login === login);
          draft.version = currentRow?.filter?.version || 0;
          draft.feedback = 'Правило изменилось в другом окне. Проверьте значения и сохраните ещё раз.';
        } else if (cause instanceof ApiError && cause.status === 403) {
          await load({fresh:true});
          draft.feedback = 'Срок тарифа «Зритель Plus» истёк. Правило сохранено, но сейчас не применяется.';
        } else {
          draft.feedback = 'Не удалось сохранить фильтр. Проверьте значения и попробуйте ещё раз.';
        }
      } finally {draft.busy=false;refresh();}
    }));
    if (row.filter) {
      target.append(action(row.folder_id ? 'Использовать правило папки' : 'Убрать личный фильтр', async () => {
        if(draft.busy)return;const origin=current();draft.busy=true;refresh();
        try {
          await api.post('/app/api/viewer/filter/reset', {
            login, expected_version: row.filter.version,
          });
          filterDrafts.delete(login);
          await load({fresh:true});
          if(current()===origin)getRouter().back();
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 409) await load({fresh:true});
          draft.feedback = cause instanceof ApiError && cause.status === 409
            ? 'Личный фильтр изменился в другом окне. Проверьте его перед удалением.'
            : 'Не удалось убрать личный фильтр. Попробуйте ещё раз.';
        } finally {draft.busy=false;refresh();}
      }, true));
    }
    if(draft.busy)for(const node of target.querySelectorAll('input,button'))node.disabled=true;
  }
  function renderFolder(target, folderId) {
    const folder = data.folders?.find((item) => item.id === folderId);
    if (!folder) {
      heading(target, 'Зритель', 'Папка не найдена', 'Вернитесь к списку стримеров.');
      return;
    }
    heading(target, 'Зритель Plus', folder.name, 'Папка и общее правило для её стримеров.');
    const members = data.subscriptions.filter((row) => row.folder_id === folder.id);
    const memberPanel = element('section', 'settings-group feature-panel');
    memberPanel.append(element('h2', '', 'Стримеры'));
    memberPanel.append(element('p', 'muted', members.length
      ? `${members.length} в папке. Перенести стримера можно в его настройках.`
      : 'Папка пока пустая. Откройте настройки стримера, чтобы добавить его.'));
    for (const member of members) {
      memberPanel.append(action(nameOf(member.login), () => getRouter().openDetail(member.login), true));
    }
    target.append(memberPanel);
    if (!data.viewer_plus_active) {
      target.append(panel('Правило сохранено', 'Без Зритель Plus оно не влияет на оповещения. Подписки и папка останутся на месте.'));
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
      const conflict = element('section', 'settings-group feature-panel');
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
    const renamePanel = element('section', 'settings-group feature-panel');
    renamePanel.append(element('h2', '', 'Название'));
    const nameInput = element('input', 'input');
    nameInput.name = 'folder_name'; nameInput.autocomplete = 'off';
    nameInput.value = draft.name; nameInput.maxLength = 40;
    nameInput.setAttribute('aria-label', 'Название папки');
    nameInput.addEventListener('input', () => { draft.name = nameInput.value; });
    renamePanel.append(nameInput);
    const saveName = action('Сохранить название', async () => {
      if (draft.busy || draft.conflict) return;
      draft.busy = true;refresh();
      try {
        const response = await api.post('/app/api/viewer/folder/rename', {
          folder_id: folderId, name: draft.name, expected_version: draft.version,
        });
        draft.version = response.folder.version;
        draft.feedback = 'Название сохранено';
        await load({fresh:true});
      } catch (cause) { await folderSaveError(cause, draft, folderId); }
      finally { draft.busy = false; refresh(); }
    });
    saveName.disabled = draft.conflict;
    renamePanel.append(saveName);
    target.append(renamePanel);
    const rule = element('section', 'settings-group feature-panel');
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
      add.setAttribute('aria-label',key==='games'?'Добавить категорию папки':key==='title_keywords'?'Добавить слово папки':'Добавить исключение папки');
      inputRow.append(input, add);
      field.append(tokens, inputRow);
      rule.append(field);
    }
    rule.append(element('p', 'notice', explainFilter(draft)));
    const saveRule = action('Сохранить правило', async () => {
      if (draft.busy || draft.conflict) return;
      draft.busy = true;refresh();
      try {
        const response = await api.post('/app/api/viewer/folder/rule', {
          folder_id: folderId, expected_version: draft.version,
          games: draft.games, title_keywords: draft.title_keywords,
          exclude_keywords: draft.exclude_keywords,
        });
        draft.version = response.folder.version;
        draft.feedback = 'Правило сохранено';
        await load({fresh:true});
      } catch (cause) { await folderSaveError(cause, draft, folderId); }
      finally { draft.busy = false; refresh(); }
    });
    saveRule.disabled = draft.conflict;
    rule.append(saveRule);
    if (draft.feedback) rule.append(element('p', 'notice', draft.feedback));
    target.append(rule);
    target.append(action('Удалить папку', async () => {
      if(draft.busy)return;const origin=current();
      const sdk = window.Telegram?.WebApp;
      const confirmed = sdk?.showConfirm
        ? await new Promise((resolve) => sdk.showConfirm(`Удалить папку «${folder.name}»? Подписки останутся.`, resolve))
        : window.confirm(`Удалить папку «${folder.name}»? Подписки останутся.`);
      if (!confirmed || draft.busy) return;
      draft.busy = true;refresh();
      try {
        await api.post('/app/api/viewer/folder/delete', {
          folder_id: folderId, expected_version: draft.version,
        });
        folderDrafts.delete(folderId);
        await load({fresh:true});
        if(current()===origin)getRouter().back();
      } catch (cause) { await folderSaveError(cause, draft, folderId); refresh(); }
      finally { draft.busy = false;refresh(); }
    }, true));
    if(draft.busy)for(const node of target.querySelectorAll('input,button'))node.disabled=true;
  }
  async function folderSaveError(cause, draft, folderId) {
    if (cause instanceof ApiError && cause.code === 'folder_name_taken') {
      draft.feedback = 'Папка с таким названием уже есть. Выберите другое.';
    } else if (cause instanceof ApiError && cause.status === 409) {
      await load({fresh:true});
      draft.conflict = true;
      draft.feedback = 'Папка изменилась в другом окне. Черновик не сохранён.';
    } else if (cause instanceof ApiError && cause.status === 403) {
      await load({fresh:true});
      draft.feedback = 'Срок тарифа «Зритель Plus» истёк. Папка сохранена, правило сейчас не действует.';
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
  function renderViewerSettings(target) {
    target.append(element('h1','','Уведомления'),element('p','lead','Тихие часы и сводка доступны бесплатно.'));
    const quiet = data.quiet_hours;
    if (!quietDraft || !quietDraft.dirty) {
      const offset = quiet?.utc_offset_minutes ?? -new Date().getTimezoneOffset();
      quietDraft = {
        start: quiet ? minuteText((quiet.start_minute + offset + 1440) % 1440) : '23:00',
        end: quiet ? minuteText((quiet.end_minute + offset + 1440) % 1440) : '08:00',
        dirty: false,
      };
    }
    const section = element('section', 'settings-group quiet-panel');
    section.append(element('h2', '', 'Тихие часы'));
    section.append(element('p', 'muted', 'Обычные оповещения будут на паузе. При сохранении используем часовой пояс устройства.'));
    const form = element('div', 'time-fields');
    for (const [key, labelText] of [['start', 'Начало тихих часов'], ['end', 'Конец тихих часов']]) {
      const label = element('label', '', labelText);
      const input = element('input', 'input');
      input.type = 'time'; input.name = key; input.value = quietDraft[key];
      input.addEventListener('input', () => { quietDraft[key] = input.value; quietDraft.dirty = true; profileFeedback=''; section.querySelector('[role=status]')?.remove(); });
      label.append(input);
      form.append(label);
    }
    section.append(form);
    const buttons = element('div', 'actions');
    const saveQuiet = action('Сохранить', async () => {
      if(quietSaving)return;
      const start = parseTime(quietDraft.start);
      const end = parseTime(quietDraft.end);
      if (start === null || end === null || start === end) {
        profileFeedback = 'Укажите разное время начала и конца.';
        refresh();
        return;
      }
      const offset = -new Date().getTimezoneOffset();
      quietSaving=true;profileFeedback='';refresh();
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
      finally{quietSaving=false;refresh();}
    });
    saveQuiet.setAttribute('aria-label', 'Сохранить тихие часы');
    buttons.append(saveQuiet);
    if (quiet) buttons.append(action('Выключить', async () => {
      if(quietSaving)return;quietSaving=true;profileFeedback='';refresh();
      try {
        await api.post('/app/api/viewer/quiet-hours', { clear: true });
        data.quiet_hours = null;
        quietDraft.dirty = false;
        profileFeedback = 'Тихие часы выключены';
      } catch { profileFeedback = 'Не удалось выключить тихие часы. Попробуйте ещё раз.'; }
      finally{quietSaving=false;refresh();}
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
      if(quietSaving)return;const enabled=digest.checked;
      quietSaving=true;profileFeedback='';refresh();
      try {
        await api.post('/app/api/viewer/digest', { enabled });
        await load({fresh:true});
      } catch {profileFeedback='Не удалось сохранить сводку. Попробуйте ещё раз.';}
      finally {quietSaving=false;refresh();}
    });
    if (profileFeedback) {
      const note = element('p', 'notice', profileFeedback);
      note.setAttribute('role', 'status');
      section.append(note);
    }
    if(quietSaving)for(const node of section.querySelectorAll('input,button'))node.disabled=true;
    target.append(section);
  }
  function resetHistory(){
    ++historyToken;historyController?.abort();
    historyState={events:[],nextBefore:null,loaded:false,loading:false,error:''};
  }
  async function loadHistory(reset = false) {
    if(disposed)return;
    if(reset)resetHistory();
    if(historyState.loading)return;
    const state=historyState,token=++historyToken,controller=new AbortController();historyController=controller;
    state.loading = true;
    refresh();
    try {
      const payload = await api.post('/app/api/viewer/history', {
        limit: 20, before_id: state.nextBefore,
      },{signal:controller.signal});
      if(disposed||token!==historyToken)return;
      state.events.push(...payload.events);
      state.nextBefore = payload.next_before_id;
      state.loaded = true;
      state.error = '';
    } catch (cause) {
      if(disposed||token!==historyToken||cause.name==='AbortError')return;
      if (cause instanceof ApiError && cause.status === 403) {
        historyState.events = [];
        historyState.nextBefore = null;
        if (cause.code === 'plus_required') {
          historyState.error = 'Срок тарифа «Зритель Plus» истёк. Сохранённая история недоступна без него.';
          await load({fresh:true});
        } else {
          historyState.error = 'Сессия Telegram устарела. Закройте и откройте приложение снова.';
        }
      } else {
        historyState.error = 'Не удалось загрузить историю. Попробуйте ещё раз.';
      }
    } finally { if(!disposed&&token===historyToken){state.loading=false;state.loaded=true;historyController=null;refresh();} }
  }
  function renderHistory(target) {
    target.append(element('h1','','История уведомлений'),element('p','lead','Результаты ваших оповещений.'));
    if (!data.viewer_plus_active) {
      target.append(panel('Зритель Plus неактивен', 'История сохранена до технической очистки, но сейчас недоступна.'));
      target.append(action('Посмотреть тариф', () => getRouter().openDetail('subscription'), true));
      return;
    }
    if (!historyState.loaded && !historyState.loading) queueMicrotask(()=>{if(!historyState.loaded&&!historyState.loading)void loadHistory();});
    target.append(action('Обновить историю',()=>void loadHistory(true),true));
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
      item.dataset.historyEvent=String(event.id);item.dataset.rowKey=`history:${event.id}`;
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
      const ownRow=data.subscriptions.find(row=>row.login===route.detail?.split(':').slice(1).join(':'));
      if(route.detail==='folders')renderFolders(target);
      else if(route.detail?.startsWith('move:')&&ownRow)renderFolderMove(target,ownRow);
      else if(route.detail?.startsWith('reminder:')&&ownRow)renderReminder(target,ownRow);
      else if(route.detail?.startsWith('category:')&&ownRow){target.append(element('h1','','Категории'),element('p','lead',nameOf(ownRow.login)));renderCategoryAlert(target,ownRow);}
      else if (route.detail === 'video-selection') renderVideoPicker(target);
      else if (route.detail === 'history') renderHistory(target);
      else if (route.detail?.startsWith('folder:')) renderFolder(target, route.detail.slice(7));
      else if (route.detail?.startsWith('filter:')) renderFilter(target, route.detail.slice(7));
      else if (route.detail) renderDetail(target, route.detail);
      else if (route.tab === 'home') renderHome(target);
      else if (route.tab === 'streamers') renderStreamers(target);
      else renderViewerSettings(target);
      renderUndo(target);
    },
    refresh: load,
    resetHistory,
    dispose(){disposed=true;clearTimeout(undoTimer);++loadToken;++historyToken;historyController?.abort();clearTimeout(searchTimer);searchController?.abort();++searchVersion;for(const draft of categoryDrafts.values())draft.controller?.abort();document.removeEventListener('visibilitychange',onVisibility);},
  };
}
