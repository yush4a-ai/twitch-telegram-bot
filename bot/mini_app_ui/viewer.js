import { ApiError } from './api.js';
import { element, panel, action } from './components.js';

const fallbackName = (login) => login.charAt(0).toUpperCase() + login.slice(1);

export function createViewerFeature(api, getSession, getRouter, telegram) {
  let data = null;
  let loading = false;
  let requested = false;
  let error = '';
  let searchDraft = '';
  let searchResults = [];
  let searchTimer = null;
  let searchController = null;
  let searchVersion = 0;
  let feedback = '';
  let lastLoadedAt = 0;
  const names = new Map();
  try { searchDraft = (localStorage.getItem('ts-app-search-draft') || '').slice(0, 200); } catch {}
  const nameOf = (login) => names.get(login) || fallbackName(login);
  const current = () => getRouter().state;
  const refresh = () => getRouter().refresh();
  async function load() {
    if (loading) return;
    requested = true;
    loading = true;
    lastLoadedAt = Date.now();
    if (!data) refresh();
    try {
      data = await api.post('/app/api/viewer/state');
      error = '';
    } catch (cause) {
      error = cause instanceof ApiError && cause.status === 403
        ? 'Время входа истекло. Откройте приложение из чата бота.'
        : 'Нет связи. Показываем последние загруженные данные.';
    } finally { loading = false; refresh(); }
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && data) void load();
  });
  function heading(target, eyebrow, title, lead) {
    target.append(element('p', 'eyebrow', eyebrow), element('h1', '', title), element('p', 'lead', lead));
  }
  function banner(target, message, danger = false) {
    const note = element('p', `notice${danger ? ' error' : ''}`, message);
    note.setAttribute('role', 'status');
    target.append(note);
  }
  function renderHome(target) {
    heading(target, 'Зритель', 'Сейчас в эфире', 'Эфиры отслеживаемых стримеров.');
    const tracked = data.subscriptions;
    const live = tracked.filter((row) => row.status === 'live');
    const stale = tracked.some((row) => row.status === 'stale');
    if (!tracked.length) {
      target.append(panel('Здесь появятся эфиры', 'Добавьте стримера, чтобы получать оповещения о его эфирах.'));
    } else if (!live.length) {
      target.append(panel(stale ? 'Проверяем статус эфиров' : 'Пока нет подтверждённого эфира', 'Бот обновит статус после следующей проверки. Ваши подписки остаются в разделе «Стримеры».'));
    } else {
      banner(target, 'Эфиры по последней проверке бота.');
      const list = element('div', 'list');
      for (const row of live) {
        const item = element('div', 'list-row');
        const left = element('div', 'row-copy');
        left.append(element('strong', '', nameOf(row.login)));
        if (row.live?.title) left.append(element('small', '', row.live.title));
        if (row.live?.category) left.append(element('small', '', row.live.category));
        const link = element('a', 'text-link', 'Смотреть');
        link.href = `https://www.twitch.tv/${row.login}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(left, link);
        list.append(item);
      }
      target.append(list);
    }
    const actions = element('div', 'actions');
    actions.append(action('Найти стримера', () => getRouter().setTab('streamers')));
    target.append(actions);
  }
  function queueSearch(input, resultBox) {
    searchDraft = input.value;
    feedback = '';
    resultBox.parentNode.querySelector('[data-feedback]')?.remove();
    try { localStorage.setItem('ts-app-search-draft', searchDraft); } catch {}
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
        if (subscribed) { getRouter().openDetail(row.login); return; }
        button.disabled = true;
        try {
          const writeAccess = await telegram.requestWriteAccess();
          await api.post('/app/api/viewer/follow', { login: row.login });
          searchResults = [];
          searchDraft = '';
          try { localStorage.removeItem('ts-app-search-draft'); } catch {}
          feedback = writeAccess === false
            ? `${row.display_name} добавлен. Разрешите боту личные сообщения, чтобы получать оповещения.`
            : `${row.display_name} добавлен`;
          await load();
        } catch (cause) {
          button.disabled = false;
          feedback = cause instanceof ApiError && cause.code === 'channel_limit'
            ? 'Достигнут лимит подписок. Удалите одну подписку и повторите попытку.'
            : cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
              ? 'Время входа истекло. Откройте приложение из чата бота. Ник сохранён.'
            : 'Не удалось добавить стримера. Попробуйте ещё раз.';
          refresh();
        }
      }, subscribed);
      button.setAttribute('aria-label', subscribed ? `Открыть ${row.display_name}` : `Добавить ${row.display_name}`);
      item.append(copy, button);
      box.append(item);
    }
  }
  function renderStreamers(target) {
    heading(target, 'Зритель', 'Мои стримеры', 'Подписки и уведомления в одном месте.');
    const form = element('div', 'search-field');
    const label = element('label', '', 'Ник или ссылка Twitch');
    const input = element('input', 'input');
    input.type = 'search'; input.name = 'channel_search'; input.autocomplete = 'off'; input.spellcheck = false;
    input.maxLength = 200;
    input.placeholder = 'Например, twitch.tv/alpha…';
    input.value = searchDraft;
    label.append(input);
    form.append(label);
    const results = element('div', 'search-results');
    results.setAttribute('role', 'status');
    input.addEventListener('input', () => queueSearch(input, results));
    target.append(form, results);
    if (searchResults.length) showResults(results);
    if (feedback) {
      const note = element('p', 'notice', feedback);
      note.setAttribute('role', 'status');
      note.setAttribute('data-feedback', '');
      target.append(note);
      feedback = '';
    }
    const rows = data.subscriptions;
    const section = element('div', 'section-head');
    section.append(element('h2', '', 'Подписки'), element('small', '', `${rows.length} из ${data.channel_limit}`));
    target.append(section);
    if (!rows.length) { target.append(panel('Добавьте первого стримера', 'Найдите его по нику Twitch или вставьте ссылку на канал.')); return; }
    const list = element('div', 'list');
    for (const row of rows) {
      const item = element('div', 'list-row');
      const copy = element('div', 'row-copy');
      copy.append(element('strong', '', nameOf(row.login)), element('small', '', row.notify_enabled ? 'Уведомления включены' : 'Уведомления выключены'));
      item.append(copy, action(`Настройки ${nameOf(row.login)}`, () => getRouter().openDetail(row.login), true));
      list.append(item);
    }
    target.append(list);
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
        await api.post('/app/api/viewer/unfollow', { login });
        data.subscriptions = data.subscriptions.filter((item) => item.login !== login);
        getRouter().back();
      } catch (cause) {
        status.textContent = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
          ? 'Время входа истекло. Откройте приложение из чата бота.'
          : 'Не удалось удалить подписку. Попробуйте ещё раз.';
      }
    }, true));
    target.append(actions);
  }
  function renderProfile(target) {
    heading(target, 'Зритель', 'Профиль', 'Настройки и доступ.');
    target.append(panel('Ваши возможности', `${data.subscriptions.length} из ${data.channel_limit} отслеживаемых стримеров. ${getSession().capabilities.viewer_plus_active ? 'Viewer Plus активен.' : 'Основные оповещения доступны бесплатно.'}`));
  }
  return {
    render(target, route) {
      target.replaceChildren();
      if (data && !loading && Date.now() - lastLoadedAt > 30000) void load();
      if (!data) {
        if (!requested) void load();
        target.append(element('p', 'eyebrow', 'Зритель'), element('h1', '', 'Загружаем подписки…'));
        if (error) {
          banner(target, error, true);
          target.append(action('Повторить загрузку', () => void load()));
        }
        return;
      }
      if (error) banner(target, error, true);
      if (route.detail) renderDetail(target, route.detail);
      else if (route.tab === 'home') renderHome(target);
      else if (route.tab === 'streamers') renderStreamers(target);
      else renderProfile(target);
    },
    refresh: load,
  };
}
