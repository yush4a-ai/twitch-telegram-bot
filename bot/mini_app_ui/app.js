import { createApi, ApiError } from './api.js';
import { createTelegramAdapter } from './telegram.js';
import { createRouter } from './router.js';
import { element, panel, action } from './components.js';
import { createViewerFeature } from './viewer.js';

const content = document.getElementById('content');
const modeSwitch = document.getElementById('mode-switch');
const tabBar = document.getElementById('tab-bar');
let session = null;
let authError = null;
let router;
let viewerFeature;
const telegram = createTelegramAdapter(() => router.back(), (theme) => {
  document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light';
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#101722' : '#f7f9fc';
});
const api = createApi(telegram.initData);
router = createRouter(render);
viewerFeature = createViewerFeature(api, () => router, telegram);
window.addEventListener('pagehide', () => telegram.dispose(), { once: true });

function render(state, canBack) {
  modeSwitch.replaceChildren();
  for (const [mode, label] of [['viewer', 'Зритель'], ['streamer', 'Стример']]) {
    const button = element('button', '', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(mode === state.mode));
    button.addEventListener('click', () => router.setMode(mode));
    modeSwitch.append(button);
  }
  tabBar.replaceChildren();
  const tabs = state.mode === 'viewer'
    ? [['home', 'Главная', '⌂'], ['streamers', 'Стримеры', '◉'], ['profile', 'Профиль', '○']]
    : [['channel', 'Мой канал', '⌂'], ['posts', 'Посты', '▤'], ['profile', 'Профиль', '○']];
  for (const [id, label, icon] of tabs) {
    const button = element('button', '', '');
    button.type = 'button';
    button.setAttribute('aria-label', label);
    if (state.tab === id) button.setAttribute('aria-current', 'page');
    const iconNode = element('span', 'tab-icon', icon);
    iconNode.setAttribute('aria-hidden', 'true');
    button.append(iconNode, element('span', '', label));
    button.addEventListener('click', () => router.setTab(id));
    tabBar.append(button);
  }
  telegram.syncBack(canBack);
  content.replaceChildren();
  if (authError) {
    content.append(element('p', 'eyebrow', 'Вход'), element('h1', '', 'Откройте приложение из Telegram'));
    content.append(element('p', 'lead', authError));
    content.append(panel('Сохраните введённое', 'При повторном открытии вернитесь к нужному разделу. Данные на сервер не отправлены.'));
    return;
  }
  if (!session) {
    content.append(element('div', 'status-panel', 'Проверяем вход…'));
    return;
  }
  if (state.mode === 'viewer') {
    viewerFeature.render(content, state);
    return;
  }
  if (state.detail) {
    content.append(element('p', 'eyebrow', state.mode === 'viewer' ? 'Зритель' : 'Стример'));
    content.append(element('h1', '', state.detail));
    content.append(element('p', 'lead', 'Настройки этого раздела появятся здесь после загрузки данных.'));
    return;
  }
  const layouts = {
    viewer: {
      home: ['Сейчас в эфире', 'Ваши стримеры и последние изменения.', 'Здесь появятся эфиры', 'Добавьте стримера, чтобы получать оповещения о его эфирах.'],
      streamers: ['Мои стримеры', 'Подписки и уведомления в одном месте.', 'Добавьте первого стримера', 'Найдите его по нику Twitch или вставьте ссылку на канал.'],
      profile: ['Профиль', 'Ваши настройки и доступ.', 'Ваши возможности', `До ${session.capabilities.viewer_channel_limit} отслеживаемых стримеров. ${session.capabilities.viewer_plus_active ? 'Viewer Plus активен.' : 'Основные оповещения доступны бесплатно.'}`],
    },
    streamer: {
      channel: ['Мой канал', 'Подключения и состояние публикаций.', 'Проверяем подключение', 'Чтобы публиковать сообщения, подключите Twitch и сообщество Telegram.'],
      posts: ['Посты', 'Оформление и подтверждённые публикации.', 'Публикации пока не загружены', 'Пример поста и настройки будут доступны после подключения канала.'],
      profile: ['Профиль', 'Настройки и доступ стримера.', 'Ваши возможности', session.capabilities.streamer_plus_active ? 'Streamer Plus активен.' : 'Подключение канала доступно бесплатно.'],
    },
  };
  const [title, lead, emptyTitle, emptyText] = layouts[state.mode][state.tab];
  content.append(element('p', 'eyebrow', state.mode === 'viewer' ? 'Зритель' : 'Стример'));
  content.append(element('h1', '', title), element('p', 'lead', lead));
  content.append(panel(emptyTitle, emptyText));
  if (state.mode === 'viewer' && state.tab === 'home') {
    const buttons = element('div', 'actions');
    buttons.append(action('Найти стримера', () => router.setTab('streamers')));
    content.append(buttons);
  }
}

router.refresh();
if (!telegram.initData) {
  authError = 'Для входа нужна кнопка приложения в чате бота.';
  router.refresh();
} else {
  try {
    session = await api.post('/app/api/bootstrap');
  } catch (error) {
    authError = error instanceof ApiError && (error.status === 401 || error.status === 403)
      ? 'Время входа истекло. Откройте приложение заново из чата бота.'
      : 'Связь прервалась. Откройте приложение заново, когда сеть восстановится.';
  }
  router.refresh();
}
