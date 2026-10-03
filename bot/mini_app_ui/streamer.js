import { createStreamerPostsFeature } from './streamer_posts.js';
import { ApiError } from './api.js';
import { element, panel, action, navigationRow, icon, avatar } from './components.js';

export function createStreamerFeature(api, getRouter, telegram) {
  let data = null;
  let loading = false;
  let requested = false;
  let error = '';
  let feedback = '';
  let permissionCheck = null;
  let feedbackRevision = 0;
  function clearFeedback() { feedback = ''; permissionCheck = null; ++feedbackRevision; }
  function connectionFeedbackWriter() {
    const revision = feedbackRevision;
    return message => {
      const route = getRouter().state;
      if (!disposed && revision === feedbackRevision && route.mode === 'streamer' && route.tab === 'channel' && !route.detail) feedback = message;
    };
  }
  let connectIntent = '';
  let connectUrl = '';
  let communityIntent = '';
  let communityFallback = '';
  let statusTimer = null;
  let disposed = false;
  let profileController = null;
  let profilePromise = null;
  let profileGeneration = 0;
  let checking = false;
  let intentGeneration = 0;
  let connectionBusy = false;
  const publishingPending = new Set();
  try {
    connectIntent = api.storage.getItem('ts-streamer-connect-intent') || '';
    communityIntent = api.storage.getItem('ts-streamer-community-intent') || '';
  } catch {}
  const refresh = () => getRouter().refresh();
  const remember = (key, value) => {
    try { if (value) api.storage.setItem(key, value); else api.storage.removeItem(key); } catch {}
  };
  function heading(target, title, lead) {
    target.append(element('h1', '', title), element('p', 'lead', lead));
  }
  const permissionText = {
    ready: 'Готов к публикациям', bot_absent: 'Бот не добавлен в канал',
    bot_member: 'Бот пока не администратор', missing_post_right: 'Нет права публиковать сообщения',
    user_denied: 'Не удалось подтвердить ваши права администратора',
    wrong_chat_type: 'Для нового подключения нужен Telegram-канал',
    network_error: 'Не удалось проверить права',
  };
  const permissionHelp = {
    bot_absent: 'Добавьте бота в канал и назначьте администратором.',
    bot_member: 'Назначьте бота администратором канала.',
    missing_post_right: 'В правах бота включите «Публикация сообщений».',
    user_denied: 'Выберите канал, которым вы управляете.',
    network_error: 'Telegram не ответил. Повторите проверку, когда связь восстановится.',
  };
  async function load({fresh = false} = {}) {
    if (disposed) return;
    if (profilePromise && !fresh) return profilePromise;
    profileController?.abort(); profileController = new AbortController();
    const controller = profileController, generation = ++profileGeneration;
    let timedOut = false;
    // The server checks three channels at a time (5s rights + optional 1s photo).
    const budget = Math.max(30000, Math.ceil((data?.communities.length || 1) / 3) * 6000 + 5000);
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, budget);
    requested = true; loading = true;
    refresh();
    const promise = (async () => {
      try {
        const result = await api.post('/app/api/streamer/profile', {}, {signal:controller.signal});
        if (disposed || generation !== profileGeneration) return;
        data = result; error = '';
        if (data.connected && !connectIntent) connectUrl = '';
        return result;
      } catch (cause) {
        if (disposed || (controller.signal.aborted && !timedOut) || generation !== profileGeneration) return;
        error = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : 'Нет связи. Показываем последние загруженные данные.';
      } finally {
        clearTimeout(timeout);
        if (!disposed && generation === profileGeneration) { loading = false; refresh(); }
      }
    })();
    profilePromise = promise;
    let result;
    try { result = await promise; } finally { if (profilePromise === promise) profilePromise = null; }
    if (!disposed && (connectIntent || communityIntent)) scheduleStatus();
    return result;
  }
  async function checkPermissions(community) {
    if (loading || disposed) return;
    clearFeedback();
    const revision = feedbackRevision, chatId = community.chat_id;
    permissionCheck = {chatId, pending:true, message:'Проверяем права…'};
    const result = await load({fresh:true});
    const route = getRouter().state;
    if (disposed || revision !== feedbackRevision || route.mode !== 'streamer' || route.detail !== `channel:${chatId}`) return;
    const checked = result?.communities.find(item => item.chat_id === chatId);
    const status = checked?.permission_status || (checked?.permission_ok ? 'ready' : 'network_error');
    const message = !result
      ? error.startsWith('Время входа') ? error : 'Не удалось проверить права. Повторите попытку.'
      : !checked ? 'Канал больше недоступен. Вернитесь к списку подключений.'
      : status === 'ready' && checked.permission_ok ? 'Права проверены. Бот может публиковать.'
      : `${permissionText[status] || 'Права не подтверждены'}. ${permissionHelp[status] || ''}`.trim();
    permissionCheck = {chatId, pending:false, message};
    refresh();
  }
  function scheduleStatus() {
    clearTimeout(statusTimer);
    if (disposed || (!connectIntent && !communityIntent)) return;
    statusTimer = setTimeout(() => { if (!document.hidden) void checkIntent(); }, 5000);
  }
  function clearCommunity() {
    communityIntent = ''; communityFallback = ''; remember('ts-streamer-community-intent', '');
  }
  async function checkIntent() {
    if (disposed || document.hidden || checking || connectionBusy) return;
    const writeFeedback = connectionFeedbackWriter();
    checking = true; const generation = intentGeneration;
    try {
      const twitchId = connectIntent, channelId = communityIntent;
      if (twitchId) {
        const status = await api.post('/app/api/streamer/connect-intent/status', {intent_id:twitchId});
        if (disposed || generation !== intentGeneration || connectIntent !== twitchId) return;
        if (status.status === 'connected') {
          connectIntent = ''; connectUrl = ''; remember('ts-streamer-connect-intent', '');
          writeFeedback(`Twitch подключён: ${status.twitch_login}.`); await load({fresh:true});
        } else if (!['pending', 'verifying'].includes(status.status)) {
          connectIntent = ''; connectUrl = ''; remember('ts-streamer-connect-intent', '');
          writeFeedback(status.status === 'cancelled' ? 'Подключение Twitch отменено.'
            : status.status === 'expired' ? 'Срок подключения истёк. Начните снова.'
            : 'Twitch не подключён. Попробуйте ещё раз.');
        }
      }
      if (channelId && channelId === communityIntent) {
        const status = await api.post('/app/api/streamer/community-intent/status', {intent_id:channelId});
        if (disposed || generation !== intentGeneration || communityIntent !== channelId) return;
        if (status.status === 'connected') {
          clearCommunity(); await load({fresh:true});
          if (!disposed && generation === intentGeneration) {
            const saved = data?.communities.find(item => item.chat_id === status.chat_id);
            writeFeedback(saved ? `Канал подключён. Публикации ${saved.publishing ? 'включены' : 'выключены'}.`
              : 'Подключение подтверждено. Обновите список каналов.');
          }
        } else if (!['pending', 'verifying'].includes(status.status)) {
          clearCommunity();
          writeFeedback(status.status === 'cancelled' ? 'Добавление нового канала отменено.'
            : status.status === 'expired' ? 'Срок выбора истёк. Выберите канал снова.'
            : status.permission_reason === 'network_error' ? 'Telegram не ответил. Права канала пока не подтверждены.'
            : permissionText[status.permission_reason] || 'Канал не подключён. Попробуйте выбрать его снова.');
        }
      }
    } catch (cause) {
      if (disposed || generation !== intentGeneration) return;
      if (cause instanceof ApiError && cause.status === 403) {
        connectIntent = ''; connectUrl = ''; clearCommunity(); remember('ts-streamer-connect-intent', '');
        writeFeedback('Срок входа истёк. Откройте приложение из чата бота.');
      } else writeFeedback('Не удалось проверить подключение. Повторим, когда связь восстановится.');
    } finally { checking = false; if (!disposed) { refresh(); scheduleStatus(); } }
  }
  const onVisibility = () => { if (!document.hidden && requested && !disposed) { postsFeature.refresh(); void load({fresh:true}); void checkIntent(); } };
  document.addEventListener('visibilitychange', onVisibility);
  async function startConnect() {
    if (disposed || connectionBusy || connectIntent) return;
    const writeFeedback = connectionFeedbackWriter();
    connectionBusy = true; const generation = ++intentGeneration; refresh();
    try {
      const intent = await api.post('/app/api/streamer/connect-intent');
      if (disposed || generation !== intentGeneration) return;
      connectIntent = intent.intent_id; connectUrl = intent.authorize_url;
      remember('ts-streamer-connect-intent', connectIntent);
      writeFeedback('Подтвердите подключение в Twitch. После возврата проверим результат.');
      telegram.openLink(intent.authorize_url);
    } catch (cause) {
      if (!disposed && generation === intentGeneration) writeFeedback(cause instanceof ApiError && cause.code === 'connect_unavailable'
        ? 'Подключение Twitch сейчас недоступно. Попробуйте позже.' : 'Не удалось начать подключение. Попробуйте ещё раз.');
    } finally { if (generation === intentGeneration) { connectionBusy = false; if (!disposed) { refresh(); scheduleStatus(); } } }
  }
  async function cancelConnect() {
    if (!connectIntent || connectionBusy || disposed) return;
    const writeFeedback = connectionFeedbackWriter();
    connectionBusy = true; ++intentGeneration; const id = connectIntent; refresh();
    try {
      await api.post('/app/api/streamer/connect-intent/cancel', {intent_id:id});
      if (disposed || connectIntent !== id) return;
      connectIntent = ''; connectUrl = ''; remember('ts-streamer-connect-intent', '');
      writeFeedback('Подключение Twitch отменено.');
    } catch { writeFeedback('Не удалось отменить подключение. Проверим его статус.'); }
    finally { connectionBusy = false; if (!disposed) { refresh(); scheduleStatus(); } }
  }
  async function cancelCommunity() {
    if (!communityIntent || connectionBusy || disposed) return;
    const writeFeedback = connectionFeedbackWriter();
    connectionBusy = true; ++intentGeneration; const id = communityIntent; refresh();
    try {
      await api.post('/app/api/streamer/community-intent/cancel', {intent_id:id});
      if (disposed || communityIntent !== id) return;
      clearCommunity(); writeFeedback('Добавление нового канала отменено.');
    } catch { writeFeedback('Не удалось подтвердить отмену. Проверим статус выбора.'); }
    finally { connectionBusy = false; if (!disposed) { refresh(); scheduleStatus(); } }
  }
  async function startCommunity() {
    if (connectionBusy || communityIntent || disposed) return;
    const writeFeedback = connectionFeedbackWriter();
    connectionBusy = true; const generation = ++intentGeneration; refresh();
    try {
      const intent = await api.post('/app/api/streamer/community-intent', {chat_type:'channel'});
      if (disposed || generation !== intentGeneration) return;
      communityIntent = intent.intent_id; communityFallback = intent.fallback_url || '';
      remember('ts-streamer-community-intent', communityIntent);
      connectionBusy = false; writeFeedback('Выберите Telegram-канал. Затем проверим права.');
      refresh(); scheduleStatus();
      const sent = await telegram.requestChat(intent.prepared_id);
      if (disposed || generation !== intentGeneration || communityIntent !== intent.intent_id) return;
      if (sent === true) {
        writeFeedback('Выбор отправлен. Проверяем канал…'); void checkIntent();
      } else if (sent === false) { await cancelCommunity(); return; }
      else if (communityFallback) {
        writeFeedback('Выберите канал через кнопку в чате бота, затем вернитесь сюда.');
        telegram.openTelegramLink(communityFallback);
      } else writeFeedback('Выбор канала сейчас недоступен. Попробуйте позже.');
    } catch { if (!disposed && generation === intentGeneration) writeFeedback('Не удалось начать выбор канала. Попробуйте ещё раз.'); }
    finally { if (generation === intentGeneration) { connectionBusy = false; if (!disposed) { refresh(); scheduleStatus(); } } }
  }
  async function togglePublishing(community, enabled) {
    if (disposed || publishingPending.has(community.chat_id)) return;
    clearFeedback();
    const revision = feedbackRevision;
    publishingPending.add(community.chat_id); refresh();
    try {
      await api.post('/app/api/streamer/communities/toggle', {chat_id:community.chat_id,enabled});
      if (disposed) return;
      if (revision === feedbackRevision) feedback = enabled ? 'Публикации включены. Бот отправит обычный пост при следующем подтверждённом эфире.' : 'Публикации приостановлены.';
      await load({fresh:true});
    } catch (cause) {
      if (!disposed && revision === feedbackRevision) feedback = cause instanceof ApiError && cause.code === 'verification_unavailable'
        ? 'Telegram не ответил. Публикации не изменены.'
        : cause instanceof ApiError && cause.code === 'permission_denied'
          ? 'Права изменились. Проверьте их и попробуйте снова.' : 'Не удалось изменить публикации. Попробуйте ещё раз.';
    } finally { publishingPending.delete(community.chat_id); if (!disposed) refresh(); }
  }
  function connectionAction(label, callback, secondary = false) {
    const button = action(label, callback, secondary); button.disabled = connectionBusy; button.classList.add('connection-action'); return button;
  }
  const postsFeature=createStreamerPostsFeature(api,getRouter,()=>data);
  function renderChannel(target) {
    heading(target, 'Мой канал', 'Посты о ваших эфирах в Telegram-канале.');
    const identity = element('section', 'connection-identity');
    identity.append(icon('channel'), element('div', 'row-copy'));
    identity.lastChild.append(element('strong', '', data.connected ? data.twitch_login : 'Twitch не подключён'),
      element('small', 'muted', data.connected ? 'Аккаунт подтверждён' : 'Войдите в свой аккаунт Twitch'));
    target.append(identity);
    if (!data.connected) {
      target.append(element('p', 'connection-steps', 'Сначала подтвердите свой аккаунт Twitch. Затем выберите Telegram-канал и включите публикации.'));
      target.append(element('p', 'lead', 'Подключение канала и стандартный пост с фото доступны бесплатно.'));
      if (!connectIntent) target.append(connectionAction('Подключить Twitch', startConnect));
      else {
        target.append(element('p', 'notice', 'Проверяем подключение Twitch…'));
        if (connectUrl) target.append(connectionAction('Открыть страницу Twitch', () => telegram.openLink(connectUrl), true));
        target.append(connectionAction('Проверить подключение', checkIntent, true), connectionAction('Отменить подключение', cancelConnect, true));
      }
    } else {
      const title = element('div', 'section-head'); title.append(element('h2', '', 'Telegram'), element('small', '', String(data.communities.length))); target.append(title);
      if (!data.communities.length) target.append(panel('Подключите Telegram-канал', 'Здесь появится канал для автоматических постов о ваших эфирах.'));
      const list = element('div', 'list');
      for (const community of data.communities) {
        const status = community.permission_status || (community.permission_ok ? 'ready' : 'network_error');
        const detail = community.chat_type === 'channel' ? 'Telegram-канал' : 'Подключённая группа';
        const row=navigationRow(community.title,
          `${detail} · ${status === 'ready' ? community.publishing ? 'Публикации включены' : 'Публикации выключены' : permissionText[status] || 'Права не подтверждены'}`,
          'channel', () => getRouter().openDetail(`channel:${community.chat_id}`), `channel:${community.chat_id}`);
        row.firstChild.replaceWith(avatar(community.title,community.avatar_url));list.append(row);
      }
      if (list.childElementCount) target.append(list);
      const instructions = element('details', 'connection-instructions');
      instructions.append(element('summary', '', 'Как подключить канал'), element('p', 'muted',
        'Добавьте бота администратором канала и включите «Публикация сообщений». Затем выберите канал. Публикации вы включите отдельно.'));
      target.append(instructions);
      if (!communityIntent) target.append(connectionAction('Подключить Telegram-канал', startCommunity));
      else {
        target.append(connectionAction('Проверить подключение', checkIntent, true), connectionAction('Отменить выбор', cancelCommunity, true));
        if (communityFallback) target.append(connectionAction('Открыть чат бота', () => telegram.openTelegramLink(communityFallback), true));
      }
    }
    renderFeedback(target);
  }
  function renderFeedback(target) {
    if (!feedback) return;
    const note = element('p', 'notice connection-feedback', feedback); note.setAttribute('role', 'status'); target.append(note);
  }
  function renderCommunity(target, id) {
    const community = data.communities.find(item => item.chat_id === Number(id));
    if (!community) { heading(target, 'Канал недоступен', 'Вернитесь к списку подключений.'); return; }
    heading(target, community.title, community.chat_type === 'channel' ? 'Telegram-канал' : 'Telegram-группа');
    const portrait=avatar(community.title,community.avatar_url);portrait.classList.add('channel-avatar');target.prepend(portrait);
    const status = community.permission_status || (community.permission_ok ? 'ready' : 'network_error');
    const state = element('section', 'connection-state channel-publishing'); state.dataset.permissionStatus = status;
    state.append(element('strong', '', community.publishing ? 'Публикации включены' : 'Публикации приостановлены'));
    state.append(element('p', 'muted', community.publishing
      ? status === 'ready' ? 'Бот опубликует пост при следующем эфире.' : 'Для отправки постов нужно восстановить права.'
      : 'Новые посты об эфирах не отправляются.'));
    target.append(state);
    const pending = publishingPending.has(community.chat_id);
    if (community.publishing || community.permission_ok) {
      const toggle = action(community.publishing ? 'Приостановить публикации' : 'Включить публикации',
        () => togglePublishing(community, !community.publishing), community.publishing);
      toggle.disabled = pending; toggle.classList.add('connection-action'); state.append(toggle);
    }
    const rights = element('section', 'permission-check');
    rights.append(element('h2', '', 'Права бота'));
    const result = permissionCheck?.chatId === community.chat_id ? permissionCheck : null;
    const note = element('p', 'notice permission-result', result?.message || (status === 'ready' ? 'Бот может публиковать сообщения.' : `${permissionText[status] || 'Права не подтверждены'}. ${permissionHelp[status] || ''}`.trim()));
    note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite'); note.setAttribute('aria-atomic', 'true');
    const retry = action(result?.pending ? 'Проверяем…' : 'Проверить права', () => checkPermissions(community), true);
    retry.disabled = loading; retry.classList.add('permission-button');
    rights.append(retry, note); target.append(rights);
    target.append(navigationRow('Отчёты об эфирах','Автоотчёт и формат после трансляции','chart',()=>getRouter().openDetail({name:'reports',id:`${community.chat_id}:${data.twitch_login}`}),'reports'));
    if (typeof community.public_url === 'string' && /^https:\/\/t\.me\/[A-Za-z][A-Za-z0-9_]{4,31}$/.test(community.public_url)) {
      target.append(connectionAction('Открыть канал', () => telegram.openTelegramLink(community.public_url), true));
    }
    renderFeedback(target);
  }
  function renderProfile(target) {
    heading(target, 'Профиль', 'Подключение и доступ стримера.');
    target.append(panel('Twitch', data.connected ? data.twitch_login : 'Не подключён'));
    target.append(panel('Стример Plus', data.plus_active ? 'Активен' : 'Обычный пост и подключение сообщества доступны бесплатно.'));
    const access = element('div', 'actions');
    access.append(action('Доступ и история', () => getRouter().openDetail('subscription'), true));
    target.append(access);
  }
  return {
    clearFeedback,
    render(target, route) {
      if (!requested) { queueMicrotask(() => { if (!requested) void load(); }); target.append(element('div', 'status-panel', 'Загружаем данные стримера…')); return; }
      if (!data) { target.append(element('div', 'status-panel', error || 'Загружаем данные стримера…')); return; }
      if (error) target.append(element('p', 'notice error', error));
      if (typeof route.detail === 'string' && route.detail.startsWith('channel:')) renderCommunity(target, route.detail.slice(8));
      else if (route.tab === 'channel') renderChannel(target);
      else if (route.tab === 'posts') postsFeature.render(target,route);
      else renderProfile(target);
    },
    async refresh(){await load({fresh:true});postsFeature.refresh();},
    dispose() { postsFeature.dispose(); disposed = true; ++profileGeneration; ++intentGeneration; profileController?.abort(); clearTimeout(statusTimer); document.removeEventListener('visibilitychange', onVisibility); },
  };
}
