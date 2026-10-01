import { createApi, ApiError } from './api.js';
import { createTelegramAdapter } from './telegram.js';
import { createRouter } from './router.js';
import { element, panel } from './components.js';
import { createViewerFeature } from './viewer.js';
import { createStreamerFeature } from './streamer.js';

const content = document.getElementById('content');
const modeSwitch = document.getElementById('mode-switch');
const tabBar = document.getElementById('tab-bar');
let session = null;
let authError = null;
let router;
let viewerFeature;
let streamerFeature;
const telegram = createTelegramAdapter(() => router.back(), (theme) => {
  document.documentElement.dataset.theme = theme === 'dark' ? 'dark' : 'light';
  document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#101722' : '#f7f9fc';
});
const api = createApi(telegram.initData);
router = createRouter(render);
viewerFeature = createViewerFeature(api, () => router, telegram);
streamerFeature = createStreamerFeature(api, () => router, telegram);
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
  streamerFeature.render(content, state);
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
