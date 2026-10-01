import { ApiError } from './api.js';
import { element, panel, action } from './components.js';

export function createStreamerFeature(api, getRouter, telegram) {
  let data = null;
  let loading = false;
  let requested = false;
  let error = '';
  let feedback = '';
  let connectIntent = '';
  let connectUrl = '';
  let communityIntent = '';
  let communityFallback = '';
  let statusTimer = null;
  try {
    connectIntent = localStorage.getItem('ts-streamer-connect-intent') || '';
    communityIntent = localStorage.getItem('ts-streamer-community-intent') || '';
  } catch {}
  const refresh = () => getRouter().refresh();
  const remember = (key, value) => {
    try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch {}
  };
  function heading(target, title, lead) {
    target.append(element('p', 'eyebrow', 'Стример'), element('h1', '', title), element('p', 'lead', lead));
  }
  async function load() {
    if (loading) return;
    requested = true; loading = true;
    if (!data) refresh();
    try {
      data = await api.post('/app/api/streamer/profile');
      error = '';
      if (data.connected) {
        connectIntent = ''; connectUrl = '';
        remember('ts-streamer-connect-intent', '');
      }
    } catch (cause) {
      error = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
        ? 'Время входа истекло. Откройте приложение из чата бота.'
        : 'Нет связи. Показываем последние загруженные данные.';
    } finally { loading = false; refresh(); }
    if (connectIntent || communityIntent) void checkIntent();
  }
  function scheduleStatus() {
    if (statusTimer) clearTimeout(statusTimer);
    if (!connectIntent && !communityIntent) return;
    statusTimer = setTimeout(() => {
      if (!document.hidden) void checkIntent();
    }, 5000);
  }
  async function checkIntent() {
    if (document.hidden) return;
    try {
      if (connectIntent) {
        const status = await api.post('/app/api/streamer/connect-intent/status', { intent_id: connectIntent });
        if (status.status === 'connected') {
          connectIntent = ''; remember('ts-streamer-connect-intent', '');
          feedback = `Twitch подключён: ${status.twitch_login}.`;
          await load(); return;
        }
        if (!['pending', 'verifying'].includes(status.status)) {
          connectIntent = ''; remember('ts-streamer-connect-intent', '');
          feedback = status.status === 'cancelled' ? 'Подключение отменено.'
            : 'Подключение не завершилось. Попробуйте ещё раз.';
        }
      }
      if (communityIntent) {
        const status = await api.post('/app/api/streamer/community-intent/status', { intent_id: communityIntent });
        if (status.status === 'connected') {
          communityIntent = ''; communityFallback = '';
          remember('ts-streamer-community-intent', '');
          feedback = 'Сообщество подключено.';
          await load(); return;
        }
        if (!['pending', 'verifying'].includes(status.status)) {
          communityIntent = ''; communityFallback = '';
          remember('ts-streamer-community-intent', '');
          feedback = status.status === 'denied'
            ? 'Не удалось подтвердить права в сообществе. Проверьте права администратора у вас и бота.'
            : 'Выбор сообщества не завершён. Можно попробовать снова.';
        }
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        connectIntent = ''; communityIntent = '';
        remember('ts-streamer-connect-intent', ''); remember('ts-streamer-community-intent', '');
        feedback = 'Срок входа истёк. Откройте приложение из чата бота.';
      } else {
        feedback = 'Не удалось проверить подключение. Повторим, когда связь восстановится.';
      }
    }
    refresh(); scheduleStatus();
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && requested) void load();
  });
  async function startConnect() {
    try {
      const intent = await api.post('/app/api/streamer/connect-intent');
      connectIntent = intent.intent_id;
      connectUrl = intent.authorize_url;
      remember('ts-streamer-connect-intent', connectIntent);
      feedback = 'Откройте Twitch и подтвердите подключение. После возврата проверим результат.';
      refresh(); scheduleStatus();
      telegram.openLink(intent.authorize_url);
    } catch (cause) {
      feedback = cause instanceof ApiError && cause.code === 'connect_unavailable'
        ? 'Подключение Twitch сейчас недоступно. Попробуйте позже.'
        : 'Не удалось начать подключение. Попробуйте ещё раз.';
      refresh();
    }
  }
  async function cancelConnect() {
    if (!connectIntent) return;
    try {
      await api.post('/app/api/streamer/connect-intent/cancel', { intent_id: connectIntent });
      connectIntent = ''; connectUrl = '';
      remember('ts-streamer-connect-intent', '');
      feedback = 'Подключение отменено.';
    } catch {
      feedback = 'Не удалось отменить подключение. Проверим его статус.';
      scheduleStatus();
    }
    refresh();
  }
  async function startCommunity(chatType) {
    try {
      const intent = await api.post('/app/api/streamer/community-intent', { chat_type: chatType });
      const intentId = intent.intent_id;
      communityIntent = intent.intent_id;
      communityFallback = intent.fallback_url || '';
      remember('ts-streamer-community-intent', communityIntent);
      feedback = 'Выберите сообщество. Подключим его после проверки ваших прав и прав бота.';
      refresh(); scheduleStatus();
      const sent = await telegram.requestChat(intent.prepared_id);
      if (sent === true) {
        feedback = 'Выбор отправлен боту. Проверяем права сообщества…';
        void checkIntent();
      } else if (sent === null && communityFallback) {
        feedback = 'Выберите сообщество через кнопку в чате бота, затем вернитесь сюда.';
        telegram.openTelegramLink(communityFallback);
      } else if (sent === false) {
        try {
          await api.post('/app/api/streamer/community-intent/cancel', { intent_id: intentId });
          if (communityIntent === intentId) {
            communityIntent = ''; communityFallback = '';
            remember('ts-streamer-community-intent', '');
          }
          feedback = 'Выбор отменён. Сообщество не подключено.';
        } catch {
          feedback = 'Не удалось подтвердить отмену. Проверяем статус выбора.';
          scheduleStatus();
        }
      } else {
        feedback = 'Откройте чат бота для выбора сообщества.';
      }
      refresh();
    } catch {
      feedback = 'Не удалось начать выбор сообщества. Попробуйте ещё раз.';
      refresh();
    }
  }
  async function togglePublishing(community, enabled) {
    try {
      await api.post('/app/api/streamer/communities/toggle', {
        chat_id: community.chat_id, enabled,
      });
      feedback = enabled
        ? 'Публикации включены. Бот отправит обычный пост при следующем подтверждённом эфире.'
        : 'Публикации приостановлены.';
      await load();
    } catch (cause) {
      feedback = cause instanceof ApiError && cause.code === 'permission_denied'
        ? 'Нужны права администратора у вас и бота. После исправления попробуйте ещё раз.'
        : 'Не удалось изменить публикации. Попробуйте ещё раз.';
      refresh();
    }
  }
  function renderChannel(target) {
    heading(target, 'Мой канал', 'Twitch и сообщества Telegram в одном месте.');
    if (!data.connected) {
      const box = element('section', 'panel feature-panel');
      box.append(element('h2', '', 'Подключите Twitch'));
      box.append(element('p', 'muted', 'Войдите в свой Twitch-аккаунт. Подключение сообщества и обычный пост бесплатны.'));
      box.append(action('Подключить Twitch', () => void startConnect()));
      if (connectUrl) box.append(action('Открыть страницу Twitch', () => telegram.openLink(connectUrl), true));
      if (connectIntent) {
        box.append(element('p', 'notice', 'Ожидаем подтверждение Twitch.'));
        box.append(action('Отменить подключение', () => void cancelConnect(), true));
      }
      target.append(box);
    } else {
      target.append(panel('Twitch подключён', data.twitch_login));
      const box = element('section', 'panel feature-panel');
      box.append(element('h2', '', 'Сообщества'));
      if (!data.communities.length) box.append(element('p', 'muted', 'Пока нет подключённых сообществ. Добавьте бота администратором группы или канала и выберите его здесь.'));
      for (const community of data.communities) {
        const line = element('div', 'panel feature-panel');
        line.append(element('strong', '', community.title), element('small', '',
          !community.permission_ok ? 'Нужны права администратора у вас и бота'
            : community.publishing ? 'Публикации включены' : 'Публикации выключены'));
        if (community.publishing) {
          line.append(action('Приостановить публикации', () => void togglePublishing(community, false), true));
        } else if (community.permission_ok) {
          line.append(action('Включить публикации', () => void togglePublishing(community, true)));
        }
        box.append(line);
      }
      const buttons = element('div', 'actions');
      buttons.append(action('Выбрать группу', () => void startCommunity('group')));
      buttons.append(action('Выбрать канал', () => void startCommunity('channel'), true));
      box.append(buttons);
      if (communityIntent) box.append(element('p', 'notice', 'Ждём выбор и повторную проверку прав.'));
      if (communityFallback) box.append(action('Открыть чат бота', () => telegram.openTelegramLink(communityFallback), true));
      target.append(box);
    }
    if (feedback) {
      const note = element('p', 'notice', feedback);
      note.setAttribute('role', 'status'); target.append(note);
    }
  }
  function renderPosts(target) {
    heading(target, 'Посты', 'Пример сообщения и подтверждённые публикации.');
    if (!data.connected) {
      target.append(panel('Сначала подключите Twitch', 'После подключения здесь появится пример стандартного поста.'));
      return;
    }
    const box = element('section', 'panel feature-panel');
    box.append(element('h2', '', 'Обычный пост'));
    box.append(element('p', 'muted', `${data.twitch_login} в эфире · категория · название трансляции · ссылка на Twitch`));
    box.append(element('p', 'muted', 'Это локальный пример. Сообщение в Telegram не отправляется.'));
    target.append(box);
    target.append(panel('Подтверждённые публикации', data.communities.length
      ? 'История появится после первой реальной публикации ботом.'
      : 'Подключите сообщество, чтобы бот мог публиковать сообщения.'));
  }
  function renderProfile(target) {
    heading(target, 'Профиль', 'Подключение и доступ стримера.');
    target.append(panel('Twitch', data.connected ? data.twitch_login : 'Не подключён'));
    target.append(panel('Streamer Plus', data.plus_active ? 'Активен' : 'Обычный пост и подключение сообщества доступны бесплатно.'));
  }
  return {
    render(target, route) {
      if (!requested) { void load(); target.append(element('div', 'status-panel', 'Загружаем данные стримера…')); return; }
      if (!data) { target.append(element('div', 'status-panel', error || 'Загружаем данные стримера…')); return; }
      if (error) target.append(element('p', 'notice error', error));
      if (route.tab === 'channel') renderChannel(target);
      else if (route.tab === 'posts') renderPosts(target);
      else renderProfile(target);
    },
  };
}
