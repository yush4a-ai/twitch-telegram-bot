import { ApiError } from './api.js';
import { element, panel, action } from './components.js';

const fallbackName = (login) => login.charAt(0).toUpperCase() + login.slice(1);

export function createViewerFeature(api, getRouter, telegram) {
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
  let quietDraft = null;
  let profileFeedback = '';
  let videoFeedback = '';
  let videoSaving = false;
  let planFeedback = '';
  let lastDetail = null;
  const filterDrafts = new Map();
  const categoryDrafts = new Map();
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
    if (data.viewer_plus_active) {
      target.append(element('p', 'muted', `Видеопревью: ${data.video_selection.selected_logins.length} из ${data.video_selection.limit}`));
    }
    if (!rows.length) { target.append(panel('Добавьте первого стримера', 'Найдите его по нику Twitch или вставьте ссылку на канал.')); return; }
    const list = element('div', 'list');
    for (const row of rows) {
      const item = element('div', 'list-row');
      const copy = element('div', 'row-copy');
      copy.append(element('strong', '', nameOf(row.login)), element('small', '', row.paused_by_plan
        ? 'Приостановлен по лимиту тарифа'
        : row.notify_enabled ? 'Уведомления включены' : 'Уведомления выключены'));
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
    }
    if (data.viewer_plus_active) {
      const rule = element('div', 'panel feature-panel');
      rule.append(element('h2', '', 'Фильтр эфиров'));
      rule.append(element('p', 'muted', explainFilter(row.filter)));
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
  async function saveVideoSelection(selectedLogins) {
    if (videoSaving) return;
    videoSaving = true;
    videoFeedback = 'Сохраняем выбор…';
    refresh();
    try {
      const saved = await api.post('/app/api/viewer/video-selection', {
        selected_logins: selectedLogins,
        expected_version: data.video_selection.version,
      });
      data.video_selection = saved;
      for (const entry of data.subscriptions) {
        entry.video_selected = saved.selected_logins.includes(entry.login);
        entry.video_effective = entry.video_selected && saved.effective_ids.includes(saved.selected_ids[saved.selected_logins.indexOf(entry.login)]);
      }
      videoFeedback = 'Выбор сохранён';
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'version_conflict') {
        await load();
        videoFeedback = 'Выбор изменился в другой сессии. Проверьте список и повторите действие.';
      } else if (cause instanceof ApiError && cause.code === 'plus_required') {
        await load();
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
    input.placeholder = 'Найти категорию Twitch'; input.value = draft.query;
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
  return {
    render(target, route) {
      if (route.detail !== lastDetail) {
        videoFeedback = '';
        planFeedback = '';
        lastDetail = route.detail;
      }
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
      if (route.detail?.startsWith('filter:')) renderFilter(target, route.detail.slice(7));
      else if (route.detail) renderDetail(target, route.detail);
      else if (route.tab === 'home') renderHome(target);
      else if (route.tab === 'streamers') renderStreamers(target);
      else renderProfile(target);
    },
    refresh: load,
  };
}
