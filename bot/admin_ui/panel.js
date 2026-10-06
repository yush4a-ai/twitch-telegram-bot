const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('ru-RU');
const VIEWS = ['overview', 'users', 'access', 'campaigns', 'system', 'growth', 'payments'];
const STATE_LABELS = { ok: 'Работает', degraded: 'Сбой', disabled: 'Отключён', unverified: 'Не проверена', unknown: 'Нет данных' };
const SOURCE_LABELS = { test: 'тестовый доступ', manual: 'ручная выдача', paid: 'оплата', mock: 'проверка оплаты' };
const ACTION_LABELS = { grant: 'Выдан', revoke: 'Отозван', extend: 'Продлён' };
// Совпадает с SNAPSHOT_PAGE в bot/admin_metrics.py.
const ROW_LIMIT = 20;
let lastSuccess = 0;
let snapshot = null;
let followUpTimer = null;

function value(x) { return x === null || x === undefined ? 'Нет данных' : number.format(x); }
function age(x) {
  if (x === null || x === undefined) return 'Нет данных';
  if (x < 120) return `${Math.floor(x)} сек. назад`;
  if (x < 7200) return `${Math.floor(x / 60)} мин. назад`;
  return `${Math.floor(x / 3600)} ч. назад`;
}
function bytes(x) {
  if (x === null || x === undefined) return 'Нет данных';
  if (x < 1024) return `${number.format(x)} Б`;
  if (x < 1048576) return `${number.format(Math.round(x / 1024))} КиБ`;
  return `${number.format(Math.round(x / 1048576))} МиБ`;
}
function put(id, text) { const node = $(id); if (node) node.textContent = text; }
function setState(id, raw) {
  const node = $(id);
  if (!node) return;
  node.textContent = STATE_LABELS[raw] || STATE_LABELS.unknown;
  node.dataset.state = raw || 'unknown';
}
function stamp(seconds) {
  if (!seconds) return 'Нет данных';
  // Сроки показываются в МСК независимо от часового пояса браузера.
  return new Date(seconds * 1000).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }) + ' МСК';
}
function row(cells) {
  const tr = document.createElement('tr');
  for (const [label, content] of cells) {
    const td = document.createElement('td');
    td.dataset.label = label;
    td.textContent = content;
    tr.append(td);
  }
  return tr;
}
function wrapRow(cells) {
  const tr = document.createElement('tr');
  for (const td of cells) tr.append(td);
  return tr;
}
function emptyRow(body, columns, text) {
  body.replaceChildren();
  const tr = document.createElement('tr');
  tr.className = 'empty-row';
  const td = document.createElement('td');
  td.colSpan = columns;
  td.textContent = text;
  tr.append(td);
  body.append(tr);
}

/* Навигация */
function currentRoute() {
  const raw = (window.location.hash || '#/overview').replace(/^#\/?/, '');
  const [view, param] = raw.split('/');
  return { view: VIEWS.includes(view) ? view : 'overview', param: param || '' };
}
function navigate() {
  const { view, param } = currentRoute();
  for (const name of VIEWS) {
    const section = $(`view-${name}`);
    if (section) section.hidden = name !== view;
  }
  for (const link of document.querySelectorAll('[data-view-link]')) {
    if (link.dataset.viewLink === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  if (view === 'users' && param) showPerson(param);
  else if (view === 'users') {
    showPerson('');
    // Пустой экран заставляет угадывать: сразу показываем людей, которых знает бот.
    if (!$('people-list').querySelector('a')) loadPeople();
  } else if (view === 'campaigns') {
    // Раздел открывается сразу с данными: кампании и непрочитанные диалоги.
    // #/campaigns/chat — прямая ссылка на переписку.
    if (param === 'chat') $('tab-chat').click();
    if (!campaignsLoaded) loadCampaigns();
    loadDialogues($('dialogues-query').value.trim());
  }
  // Живой чат обновляем только пока открыт раздел с перепиской.
  if (view === 'campaigns' && !$('chat-panel').hidden) startChatRefresh();
  else stopChatRefresh();
  document.querySelector('main').scrollIntoView({ block: 'start' });
}

function renderActivitySeries(byDay) {
  const list = $('activity-series');
  if (!list) return;
  list.replaceChildren();
  if (byDay === null || byDay === undefined) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Недостаточно данных';
    list.append(li);
    return;
  }
  const peak = Math.max(1, ...byDay.map((item) => item.users || 0));
  for (const item of byDay) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.className = 'activity-day';
    label.textContent = new Date(item.date * 1000).toLocaleDateString('ru-RU', {
      day: '2-digit', month: '2-digit', timeZone: 'Europe/Moscow',
    });
    const bar = document.createElement('span');
    bar.className = 'activity-bar';
    bar.style.inlineSize = `${Math.round(((item.users || 0) / peak) * 100)}%`;
    const users = document.createElement('span');
    users.className = 'activity-users';
    users.textContent = value(item.users);
    li.append(label, bar, users);
    list.append(li);
  }
}

function renderPeoplePath(steps, confirmedDelivery) {
  const list = $('people-path');
  if (!list) return;
  list.replaceChildren();
  if (!Array.isArray(steps) || !steps.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Недостаточно данных';
    list.append(li);
    return;
  }
  for (const step of steps) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.className = 'activity-day';
    label.textContent = String(step.title || '');
    const users = document.createElement('span');
    users.className = 'activity-users';
    users.textContent = value(step.value);
    const note = document.createElement('span');
    note.className = 'series-note';
    const share = step.share_of_previous;
    note.textContent = share === null || share === undefined
      ? 'Первый шаг'
      : `${Math.round(share * 100)}% от прошлого шага`;
    li.append(label, users, note);
    list.append(li);
  }
  if (confirmedDelivery !== null && confirmedDelivery !== undefined) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.className = 'activity-day';
    label.textContent = 'Подтверждённая доставка';
    const users = document.createElement('span');
    users.className = 'activity-users';
    users.textContent = value(confirmedDelivery);
    const note = document.createElement('span');
    note.className = 'series-note';
    // Честно про источник: журнал ведётся только при действующем Plus,
    // поэтому число ниже реального и в воронку не подставляется.
    note.textContent = 'Ведётся только для платных, поэтому ниже реального';
    li.append(label, users, note);
    list.append(li);
  }
}

/* Экраны */
function renderMetrics(data) {
  const audience = data.audience || {};
  put('stat-users', value(audience.private_users));
  const access = data.access || {};
  put('stat-plus', value(access.active_total));
  const sources = access.by_source || {};
  const parts = Object.keys(sources).sort().map((key) => `${SOURCE_LABELS[key] || key}: ${sources[key]}`);
  put('stat-plus-note', parts.length ? parts.join(' · ') : 'Действующие права');
  put('stat-deliveries', value((data.deliveries || {}).total));
  const activity = data.activity;
  put('stat-active-today', activity ? value(activity.active_today) : 'Нет данных');
  put('stat-new-7d', activity ? value(activity.new_7d) : 'Нет данных');
  // Заметка под числом не должна противоречить самому числу.
  put('stat-active-today-note', activity ? 'Отметки действий за сегодня, МСК' : 'Считается по отметкам действий');
  put('stat-new-7d-note', activity ? 'Первое появление за неделю' : 'Первое появление в боте');
  const people = data.people || {};
  put('stat-returned', people.returned === undefined || people.returned === null
    ? 'Нет данных' : value(people.returned));
  renderPeoplePath(people.funnel, people.with_delivery);
  renderActivitySeries(activity ? activity.by_day : null);

  const attention = data.attention || [];
  const list = $('attention-list');
  const danger = attention.some((item) => item.severity === 'danger')
    || (data.twitch || {}).state === 'degraded'
    || (data.preview || {}).state === 'degraded'
    || (data.telegram || {}).state === 'degraded';
  const health = $('health-line');
  if (data.errors && (data.errors.database || data.errors.directory)) {
    health.textContent = 'Часть данных недоступна';
    health.dataset.state = 'danger';
  } else if (danger) {
    health.textContent = 'Есть проблемы';
    health.dataset.state = 'danger';
  } else if (attention.length) {
    health.textContent = 'Есть задержки';
    health.dataset.state = 'warn';
  } else if ((data.telegram || {}).state === 'unknown' && (data.twitch || {}).state === 'unknown') {
    health.textContent = 'Не удалось проверить';
    health.dataset.state = 'unknown';
  } else {
    health.textContent = 'Система работает';
    health.dataset.state = 'ok';
  }

  list.replaceChildren();
  const badge = $('nav-system-badge');
  if (!attention.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    // «Нет проблем» честно только тогда, когда состояние действительно проверено.
    li.textContent = health.dataset.state === 'ok'
      ? 'Нет открытых проблем'
      : 'Открытых проблем не зафиксировано, состояние подсистем не подтверждено';
    list.append(li);
  } else {
    for (const item of attention) {
      const li = document.createElement('li');
      li.dataset.severity = item.severity || 'warn';
      const title = document.createElement('span');
      title.className = 'attention-title';
      title.textContent = item.title || 'Проблема';
      const detail = document.createElement('span');
      detail.className = 'attention-detail';
      detail.textContent = item.detail || '';
      li.append(title, detail);
      list.append(li);
    }
  }
  if (badge) {
    badge.textContent = String(attention.length);
    badge.hidden = attention.length === 0;
    // Голое число в имени ссылки ничего не говорит: подписываем словами.
    badge.setAttribute('aria-label', attention.length ? `открытых проблем: ${attention.length}` : '');
  }
}

function renderSkeleton(containerId, rows) {
  // Принимаем и id, и сам элемент: вызовы бывают оба, иначе скелетон не рисуется.
  const node = typeof containerId === 'string' ? $(containerId) : containerId;
  if (!node) return;
  const count = Math.max(1, rows || 3);
  node.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const tr = document.createElement('tr');
    tr.className = 'skeleton-row';
    const td = document.createElement('td');
    td.colSpan = 4;
    const bar = document.createElement('span');
    bar.className = 'skeleton';
    td.append(bar);
    tr.append(td);
    node.append(tr);
  }
}

function renderEvents(rows) {
  const list = $('event-list');
  if (!list) return;
  list.replaceChildren();
  // Недоступные данные и пустой список — разные вещи.
  if (rows === null || rows === undefined) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'События временно недоступны';
    list.append(li);
    return;
  }
  const items = rows.slice(0, 3);
  if (!items.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Событий пока нет';
    list.append(li);
    return;
  }
  for (const item of items) {
    const li = document.createElement('li');
    const title = document.createElement('span');
    title.className = 'event-title';
    title.textContent = `Эфир ${item.login || 'без названия'}`;
    const detail = document.createElement('span');
    detail.className = 'event-detail';
    detail.textContent = `${value(item.viewers)} зрителей · назначений ${value(item.destinations)}`;
    const time = document.createElement('span');
    time.className = 'event-time';
    time.textContent = item.observed_at ? stamp(item.observed_at) : 'Нет данных';
    li.append(title, detail, time);
    list.append(li);
  }
}

function renderLive(rows) {
  const body = $('live-body');
  if (rows === null || rows === undefined) return emptyRow(body, 4, 'Список эфиров временно недоступен');
  if (!rows.length) return emptyRow(body, 4, 'Сейчас нет зафиксированных эфиров');
  body.replaceChildren();
  for (const item of rows) {
    body.append(row([
      ['Канал', item.login || 'Нет данных'],
      ['Назначений', value(item.destinations)],
      ['Зрителей', value(item.viewers)],
      ['Наблюдение', item.observed_at ? stamp(item.observed_at) : 'Нет данных'],
    ]));
  }
}

function renderAccess(access) {
  const activeBody = $('access-active-body');
  const historyBody = $('access-history-body');
  const rows = (access || {}).active_rows;
  if (rows === null || rows === undefined) {
    emptyRow(activeBody, 4, 'Данные временно недоступны');
  } else if (!rows.length) {
    emptyRow(activeBody, 4, 'Ничего не найдено');
  } else {
    activeBody.replaceChildren();
    for (const item of rows) {
      const person = item.person_id;
      // Получатель — имя со ссылкой на карточку: из «Доступов» владелец идёт
      // к человеку, а не набирает ID в поиске заново.
      const who = document.createElement('span');
      if (person) {
        const link = document.createElement('a');
        link.href = `#/users/${person}`;
        link.textContent = item.display_name || (item.username ? `@${item.username}` : String(person));
        who.append(link);
        if (item.display_name && item.username) {
          const handle = document.createElement('span');
          handle.className = 'muted-inline';
          handle.textContent = ` @${item.username}`;
          who.append(handle);
        }
      } else {
        who.textContent = String(item.subject_id);
      }
      const whoCell = document.createElement('td');
      whoCell.dataset.label = 'Получатель';
      whoCell.append(who);
      const planCell = cell('План', planLabel(item.plan));
      const sourceCell = cell('Источник', SOURCE_LABELS[item.source] || item.source || 'Не определён');
      const expiryCell = cell('Срок', stamp(item.expires_at));
      activeBody.append(wrapRow([whoCell, planCell, sourceCell, expiryCell]));
    }
  }

  const events = (access || {}).history;
  if (events === null || events === undefined) {
    emptyRow(historyBody, 4, 'Данные временно недоступны');
  } else if (!events.length) {
    emptyRow(historyBody, 4, 'Ничего не найдено');
  } else {
    historyBody.replaceChildren();
    for (const item of events) {
      const actor = item.actor_telegram_id ? String(item.actor_telegram_id) : 'Автоматически';
      historyBody.append(row([
        ['Когда', stamp(item.happened_at)],
        ['Кто', actor],
        ['Действие', ACTION_LABELS[item.action] || item.action || 'Неизвестно'],
        ['План', item.plan ? (item.plan === 'streamer_plus' ? 'Streamer Plus' : 'Viewer Plus') : 'Нет данных'],
      ]));
    }
  }

  // «Истекающие» — то, что заканчивается в ближайшую неделю: владелец успевает продлить.
  const expiringBody = $('access-expiring-body');
  const soon = ((rows || []).filter((item) => item.expires_at
    && item.expires_at <= Date.now() / 1000 + 7 * 86400))
    .sort((left, right) => left.expires_at - right.expires_at);
  if (rows === null || rows === undefined) {
    emptyRow(expiringBody, 4, 'Данные временно недоступны');
  } else if (!soon.length) {
    emptyRow(expiringBody, 4, 'Ничего не найдено');
  } else {
    expiringBody.replaceChildren();
    for (const item of soon) {
      const person = item.person_id;
      const who = document.createElement('span');
      if (person) {
        const link = document.createElement('a');
        link.href = `#/users/${person}`;
        link.textContent = item.display_name || (item.username ? `@${item.username}` : String(person));
        who.append(link);
      } else {
        who.textContent = String(item.subject_id);
      }
      const whoCell = document.createElement('td');
      whoCell.dataset.label = 'Получатель';
      whoCell.append(who);
      expiringBody.append(wrapRow([
        whoCell,
        cell('План', planLabel(item.plan)),
        cell('Источник', SOURCE_LABELS[item.source] || item.source || 'Не определён'),
        cell('Истекает', stamp(item.expires_at)),
      ]));
    }
  }

  const note = $('access-note');
  if (note) {
    const truncated = (rows !== null && rows !== undefined && rows.length >= ROW_LIMIT)
      || (events !== null && events !== undefined && events.length >= ROW_LIMIT);
    note.hidden = !truncated;
    note.textContent = truncated ? `Показаны первые ${ROW_LIMIT} записей, а не весь список.` : '';
  }
}

function humanBytes(raw) {
  if (raw === null || raw === undefined || !Number.isFinite(Number(raw))) return 'Нет данных';
  const units = ['Б', 'КБ', 'МБ', 'ГБ'];
  let size = Number(raw);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size >= 10 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
}

function humanAge(raw) {
  if (raw === null || raw === undefined || !Number.isFinite(Number(raw))) return 'Нет данных';
  const seconds = Number(raw);
  if (seconds < 90) return 'только что';
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} ч назад`;
  return `${Math.round(hours / 24)} дн назад`;
}

function renderPreviewDetails(preview) {
  const active = preview.active_sessions;
  const limit = preview.max_active_sessions;
  put('preview-active', active === null || active === undefined
    ? 'Нет данных'
    : (Number.isFinite(Number(limit)) && Number(limit) > 0
      ? `${active} из ${limit}` : String(active)));
  const deferred = preview.deferred_sessions;
  put('preview-deferred', deferred === null || deferred === undefined
    ? 'Нет данных'
    : (Number(deferred) > 0 ? `${deferred} каналов` : 'никого'));
  const simple = preview.simple_sessions;
  put('preview-simple', simple === null || simple === undefined
    ? 'Нет данных'
    : (Number(simple) > 0
      ? `${simple} — свежие секунды без разбора`
      : 'ничего — все каналы со стримерами'));
  const jobs = preview.active_jobs;
  const jobsLimit = preview.max_concurrent_jobs;
  put('preview-jobs', jobs === null || jobs === undefined
    ? 'Нет данных'
    : (Number.isFinite(Number(jobsLimit)) && Number(jobsLimit) > 0
      ? `${jobs} из ${jobsLimit}` : String(jobs)));
  put('preview-last', humanAge(preview.last_success_age_seconds));
  const failures = preview.consecutive_provider_failures;
  put('preview-failures', failures === null || failures === undefined
    ? 'Нет данных'
    : (Number(failures) > 0 ? String(failures) : 'нет'));
  const disk = preview.disk || {};
  put('preview-temp', humanBytes(disk.preview_bytes));
  put('preview-data-free', humanBytes(disk.data_free_bytes) === 'Нет данных'
    ? 'Нет данных'
    : `${humanBytes(disk.data_free_bytes)} из ${humanBytes(disk.data_total_bytes)}`);
  put('preview-free', humanBytes(disk.free_bytes) === 'Нет данных'
    ? 'Нет данных'
    : `${humanBytes(disk.free_bytes)} из ${humanBytes(disk.total_bytes)}`);
  const note = $('preview-note');
  if (note && preview.disabled_reason) {
    note.textContent = `Остановлено: ${preview.disabled_reason}`;
  } else if (note) {
    const minutes = Number.isFinite(preview.interval_seconds) && preview.interval_seconds > 0
      ? Math.round(preview.interval_seconds / 60) : null;
    const every = minutes ? `Пересчёт раз в ${minutes} мин` : 'Пересчёт по циклу';
    // Простой режим — это осознанный выбор, а не сбой: свежие секунды без разбора.
    note.textContent = preview.mode === 'simple'
      ? `Простой режим: свежие секунды без разбора · ${every}`
      : every;
  }
}

function renderSystem(data) {
  setState('system-telegram', (data.telegram || {}).state);
  setState('system-twitch', (data.twitch || {}).state);
  setState('system-preview', (data.preview || {}).state);
  renderPreviewDetails(data.preview || {});
  // «Сбой» без причины бесполезен: показываем, сколько каналов отвалилось и кого просить.
  const twitch = data.twitch || {};
  const note = $('system-twitch-note');
  const ready = twitch.eventsub_ready;
  const configured = twitch.eventsub_configured;
  const blocked = Array.isArray(twitch.auth_blocked_names) ? twitch.auth_blocked_names : [];
  const parts = [];
  if (Number.isInteger(ready) && Number.isInteger(configured) && configured > 0) {
    parts.push(`каналов ${ready} из ${configured}`);
  }
  if (blocked.length) parts.push(`нужна авторизация: ${blocked.join(', ')}`);
  note.hidden = !parts.length;
  note.textContent = parts.join(' · ');
  const queues = data.queues || {};
  const failed = queues.failed_jobs;
  const due = queues.due_jobs;
  const queueAge = queues.oldest_due_age_seconds;
  let queueState = 'unknown';
  if (queues.pending_jobs !== undefined) {
    if (failed) queueState = 'degraded';
    else if (due && queueAge !== null && queueAge !== undefined && queueAge > 300) queueState = 'degraded';
    else queueState = 'ok';
  }
  setState('system-queue', queueState);
  setState('system-database', data.errors && data.errors.database ? 'degraded' : 'ok');

  put('queue-pending', value(queues.pending_jobs));
  put('queue-leased', value(queues.leased_jobs));
  put('queue-due', value(queues.due_jobs));
  put('queue-failed', value(queues.failed_jobs));
  put('queue-oldest', age(queues.oldest_due_age_seconds));

  const backup = data.backup;
  if (backup === null || backup === undefined) {
    put('backup-state', 'Нет данных');
    put('backup-name', 'Нет данных');
    put('backup-retention', 'Нет данных');
  } else {
    put('backup-state', backup.last_backup_at ? stamp(backup.last_backup_at) : 'Копий пока нет');
    put('backup-name', backup.last_backup_name || 'Нет данных');
    const copies = backup.copies === null || backup.copies === undefined ? 'Нет данных' : String(backup.copies);
    const limit = backup.retention === null || backup.retention === undefined ? null : String(backup.retention);
    put('backup-retention', limit === null ? copies : `${copies} (лимит ${limit})`);
  }
  put('restore-verified', (backup && backup.restore_verified) ? 'Проверена' : 'Не проводилась');
  // Строка сводки: свежая копия не выдаётся за проверенное восстановление.
  let backupState = 'unknown';
  if (backup === null || backup === undefined) backupState = 'unknown';
  else if (!backup.last_backup_at) backupState = 'degraded';
  else if (backup.restore_verified) backupState = 'ok';
  else backupState = 'unverified';
  setState('system-backup', backupState);
  put('system-observed', data.generated_at
    ? `Наблюдение по снимку в ${new Date(data.generated_at * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' })} МСК`
    : 'Время наблюдения неизвестно');

  const errors = data.errors || {};
  for (const key of ['poller', 'eventsub', 'preview', 'database', 'directory']) put(`error-${key}`, errors[key] || 'Нет');
  const resources = data.resources || {};
  put('resource-cpu', resources.process_cpu_percent === null || resources.process_cpu_percent === undefined ? 'Нет данных' : `${number.format(resources.process_cpu_percent)} %`);
  put('resource-ram', bytes(resources.process_ram_bytes));
  put('resource-disk', bytes(resources.db_volume_free_bytes));
  put('resource-db', `${bytes(resources.db_file_bytes)} / ${bytes(resources.wal_file_bytes)}`);
}

function renderFunnel(funnel) {
  const list = $('funnel-list');
  if (!list) return;
  const totals = $('funnel-totals');
  if (!funnel || !Array.isArray(funnel.steps)) {
    emptyRow(list, 1, 'Данных пока нет');
    if (totals) totals.textContent = '';
    return;
  }
  list.replaceChildren();
  const top = funnel.steps[0] ? funnel.steps[0].users : 0;
  funnel.steps.forEach((step, index) => {
    const li = document.createElement('li');
    li.className = 'funnel-step';
    const title = document.createElement('span');
    title.className = 'funnel-title';
    title.textContent = step.title;
    const bar = document.createElement('span');
    bar.className = 'funnel-bar';
    const fill = document.createElement('span');
    fill.style.inlineSize = top ? `${Math.round((step.users / top) * 100)}%` : '0%';
    bar.append(fill);
    const numbers = document.createElement('span');
    numbers.className = 'funnel-numbers';
    // У первого шага доля всегда 100% — она ничего не сообщает.
    numbers.textContent = index === 0 || step.share === null || step.share === undefined
      ? `${value(step.users)}`
      : `${value(step.users)} · ${step.share}%`;
    li.append(title, bar, numbers);
    list.append(li);
  });
  if (totals) {
    const data = funnel.totals || {};
    totals.textContent = 'Всего бот знает '
      + `${value(data.users)} · добавили канал ${value(data.with_channel)}`
      + ` · с Plus ${value(data.with_plus)}`;
  }
}

function renderGrowth(rows) {
  const body = $('growth-list');
  if (rows === null || rows === undefined) return emptyRow(body, 4, 'Данные временно недоступны');
  if (!rows.length) return emptyRow(body, 4, 'Источники пока не зафиксированы');
  body.replaceChildren();
  for (const item of rows) {
    const source = item.source === 'site' ? 'Сайт' : item.source === 'referral' ? 'Приглашения' : 'Неизвестно';
    body.append(row([
      ['Источник', source],
      ['Пришли', value(item.touched)],
      ['Добавили канал', value(item.activated)],
      ['Получали тестовый Plus', value(item.ever_test_plus)],
    ]));
  }
}

/* Люди и доступы */
let currentPerson = null;
let csrfToken = null;
let peopleTimer = null;
let lastPersonId = null;

async function api(path, options) {
  const response = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...(options || {}),
  });
  if (response.status === 401) { window.location.assign('/admin'); throw new Error('unauthorized'); }
  return response;
}

function cell(label, text) {
  const td = document.createElement('td');
  td.dataset.label = label;
  td.textContent = text;
  return td;
}

function planLabel(plan) {
  return plan === 'streamer_plus' ? 'Streamer Plus' : plan === 'viewer_plus' ? 'Viewer Plus' : 'Free';
}

function renderPeople(rows) {
  const body = $('people-list');
  const note = $('people-note');
  if (rows === null || rows === undefined) return emptyRow(body, 4, 'Данные временно недоступны');
  if (!rows.length) return emptyRow(body, 4, 'Пользователь не найден. Проверьте Telegram ID или имя');
  body.replaceChildren();
  for (const item of rows) {
    const tr = document.createElement('tr');
    const name = document.createElement('td');
    name.dataset.label = 'Человек';
    const link = document.createElement('a');
    link.href = `#/users/${item.user_id}`;
    link.textContent = item.display_name || item.username || String(item.user_id);
    name.append(link);
    tr.append(name, cell('Тариф', planLabel(item.plan)),
              cell('Срок', item.expires_at ? stamp(item.expires_at) : 'Нет данных'),
              cell('Активность', item.last_active_at ? stamp(item.last_active_at) : 'Нет данных'));
    body.append(tr);
  }
  if (note) {
    note.hidden = rows.length < ROW_LIMIT;
    note.textContent = rows.length >= ROW_LIMIT
      ? `Показаны первые ${ROW_LIMIT} записей. Уточните запрос.` : '';
  }
  // После возврата из карточки фокус остаётся на выбранном человеке.
  if (lastPersonId) {
    const link = body.querySelector(`a[href="#/users/${lastPersonId}"]`);
    const active = document.activeElement;
    const typing = active && active.tagName && ['INPUT', 'SELECT', 'TEXTAREA'].includes(active.tagName);
    if (link && !typing) link.focus();
  }
}

async function loadPeople() {
  const query = $('people-query').value.trim();
  const filter = $('people-filter').value;
  const body = $('people-list');
  renderSkeleton(body, 3);
  try {
    const response = await api(`/admin/api/users?q=${encodeURIComponent(query)}&filter=${encodeURIComponent(filter)}`);
    if (!response.ok) throw new Error('people');
    renderPeople((await response.json()).people);
  } catch (error) {
    if (String(error) === 'Error: unauthorized') return;
    emptyRow(body, 4, 'Не удалось загрузить пользователей');
  }
}

function renderPerson(card, events) {
  $('person-empty').hidden = true;
  $('person-card').hidden = false;
  put('person-card-id', `ID ${card.user_id}`);
  put('person-name', card.display_name || 'Нет данных');
  put('person-username', card.username ? `@${card.username}` : 'Нет данных');
  put('person-twitch', card.twitch_login || 'Не подключён');
  const grants = card.grants || [];
  const hasStreamer = grants.some((item) => item.plan === 'streamer_plus');
  const hasPlus = hasStreamer || grants.some((item) => item.plan === 'viewer_plus');
  put('person-grants', grants.length
    ? grants.map((item) => `${planLabel(item.plan)} · ${SOURCE_LABELS[item.source] || item.source}`).join(', ')
      + (hasStreamer ? ' · Viewer Plus включён' : '')
    : 'Действующих прав нет');
  put('person-expiry', grants.length ? stamp(grants[0].expires_at) : 'Нет данных');
  put('person-activity', card.last_active_at ? stamp(card.last_active_at) : 'Нет данных');
  const limits = card.limits || {};
  const channels = limits.channels || {};
  put('limit-channels', `${value(channels.used)} / ${value(channels.limit)}`);
  const video = limits.video || {};
  put('limit-video', hasPlus
    ? `${value(video.used)} / ${value(video.limit)}`
    : 'Видео доступно с Plus');

  const list = $('person-history');
  list.replaceChildren();
  if (!events || !events.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Событий пока нет';
    list.append(li);
    return;
  }
  for (const item of events) {
    const li = document.createElement('li');
    const title = document.createElement('span');
    title.className = 'attention-title';
    const actor = item.actor_telegram_id ? item.actor_telegram_id : 'Автоматически';
    title.textContent = `${ACTION_LABELS[item.action] || item.action} · ${planLabel(item.plan)}`;
    const detail = document.createElement('span');
    detail.className = 'attention-detail';
    const parts = [stamp(item.happened_at), `исполнитель ${actor}`];
    if (item.reason) parts.push(`причина ${item.reason}`);
    if (item.previous_expires_at && item.new_expires_at) {
      parts.push(`было ${stamp(item.previous_expires_at)} → стало ${stamp(item.new_expires_at)}`);
    }
    if (item.comment) parts.push(item.comment);
    detail.textContent = parts.join(' · ');
    li.append(title, detail);
    list.append(li);
  }
}

async function showPerson(rawId) {
  const id = String(rawId || '').trim();
  currentPerson = null;
  $('person-card').hidden = true;
  $('person-empty').hidden = false;
  showReceipt('');
  put('person-card-id', id ? `ID ${id}` : 'ID не выбран');
  put('person-empty', id ? 'Загрузка…' : 'Выберите человека из списка или найдите его по имени, ID либо Twitch.');
  if (!/^\d+$/.test(id)) {
    // Иначе карточка навсегда осталась бы в «Загрузка…».
    if (id) put('person-empty', 'Пользователь не найден. Проверьте Telegram ID или имя');
    return;
  }
  try {
    const [cardResponse, historyResponse] = await Promise.all([
      api(`/admin/api/users/${id}`), api(`/admin/api/users/${id}/history`),
    ]);
    if (cardResponse.status === 404) {
      put('person-empty', 'Пользователь не найден. Проверьте Telegram ID или имя');
      return;
    }
    if (!cardResponse.ok) throw new Error('card');
    const card = await cardResponse.json();
    const events = historyResponse.ok ? (await historyResponse.json()).events : [];
    currentPerson = card;
    lastPersonId = String(card.user_id);
    renderPerson(card, events);
  } catch (error) {
    if (String(error) === 'Error: unauthorized') return;
    put('person-empty', 'Не удалось загрузить карточку');
  }
}

function showError(id, text) {
  const node = $(id);
  if (!node) return;
  // В «notice» есть отдельный текст и кнопка повтора — их не затираем.
  const target = node.querySelector('.notice-text') || node;
  target.textContent = text;
  const retry = node.querySelector('#notice-retry');
  if (retry) retry.hidden = !text;
  node.hidden = !text;
}

function toggleOtherNote(reasonId, noteId, labelId) {
  const other = $(reasonId).value === 'other';
  $(noteId).hidden = !other;
  const label = $(labelId);
  if (label) label.hidden = !other;
}

function showReceipt(text) {
  const node = $('card-receipt');
  if (!node) return;
  node.textContent = text;
  node.hidden = !text;
}

function manualGrants() {
  return ((currentPerson || {}).grants || []).filter((item) => item.source === 'manual');
}

function fillGrantChoices() {
  for (const id of ['grant-target-grant', 'revoke-target-grant']) {
    const select = $(id);
    const label = $(`${id}-label`);
    select.replaceChildren();
    for (const grant of manualGrants()) {
      const option = document.createElement('option');
      option.value = grant.grant_id;
      option.textContent = `${planLabel(grant.plan)} · до ${stamp(grant.expires_at)}`;
      select.append(option);
    }
    // Пустой список прав только сбивает с толку: без ручных выдач выбирать нечего.
    const empty = manualGrants().length === 0;
    select.hidden = empty;
    if (label) label.hidden = empty;
  }
}

function applyGrantAvailability() {
  const hasTwitch = Boolean(currentPerson && currentPerson.twitch_login);
  const select = $('grant-plan');
  const option = select.querySelector('option[value="streamer_plus"]');
  if (!option) return;
  option.disabled = !hasTwitch;
  option.textContent = hasTwitch
    ? 'Streamer Plus · включает Viewer Plus'
    : 'Streamer Plus · нужен подключённый Twitch';
  if (!hasTwitch && select.value === 'streamer_plus') select.value = 'viewer_plus';
}

function selectedGrant(selectId) {
  const select = $(selectId);
  const chosen = select && select.value;
  return manualGrants().find((item) => item.grant_id === chosen)
    || manualGrants()[0]
    || null;
}

function grantFields() {
  return {
    plan: $('grant-plan').value,
    expiresAt: Math.floor(Date.parse($('grant-expiry').value) / 1000),
    reason: $('grant-reason').value,
    note: $('grant-note').value.trim(),
    comment: $('grant-comment').value.trim(),
  };
}

function typedTargetId() {
  const raw = ($('grant-target-id').value || '').trim();
  return /^\d{4,20}$/.test(raw) ? Number(raw) : null;
}

function grantTarget() {
  if (currentPerson) return { id: currentPerson.user_id, known: true };
  const id = typedTargetId();
  return id === null ? null : { id, known: false };
}

function updateGrantSummary() {
  const target = grantTarget();
  const { plan, expiresAt, reason, note } = grantFields();
  const manual = currentPerson ? selectedGrant('grant-target-grant') : null;
  const verb = manual && manual.plan === plan ? 'Продлить' : 'Выдать';
  const when = Number.isFinite(expiresAt) ? stamp(expiresAt) : 'укажите срок';
  const reasonLabel = { compensation: 'Компенсация', testing: 'Тестирование',
                        partnership: 'Партнёрство', other: 'Другое' }[reason] || reason;
  const extra = reason === 'other' && !note ? ' · нужно пояснение для «Другое»' : '';
  // Streamer Plus уже включает Viewer Plus: отдельная выдача только запутает.
  const streamerActive = currentPerson
    ? (currentPerson.grants || []).some((item) => item.plan === 'streamer_plus')
    : false;
  const redundant = plan === 'viewer_plus' && streamerActive
    ? ' У человека действует Streamer Plus — он уже включает Viewer Plus.' : '';
  const who = currentPerson
    ? (currentPerson.display_name || currentPerson.username || currentPerson.user_id)
    : (target ? `ID ${target.id}` : 'укажите Telegram ID');
  if (!currentPerson) {
    put('grant-target', target
      ? `Получатель: ID ${target.id}${target.known ? '' : ' (человек ещё не запускал бота)'}`
      : 'Получатель: укажите Telegram ID');
  }
  put('grant-summary', `${verb} ${planLabel(plan)} пользователю ${who} до ${when}. `
    + `Причина: ${reasonLabel}${extra}. Списания денег не будет.${redundant}`);
}

function updateRevokeSummary() {
  if (!currentPerson) return;
  const manual = selectedGrant('revoke-target-grant');
  const others = (currentPerson.grants || []).filter((item) => item !== manual);
  const who = currentPerson.display_name || currentPerson.username || currentPerson.user_id;
  put('revoke-target', `Получатель: ${who}`);
  if (!manual) {
    put('revoke-summary', 'Ручного доступа нет — отзывать нечего.');
    return;
  }
  const left = others.length
    ? `Права после отзыва: ${others.map((item) => planLabel(item.plan)).join(', ')} сохранятся.`
    : 'Права после отзыва: человек перейдёт на Free, подключения и данные сохранятся.';
  put('revoke-summary', `${planLabel(manual.plan)} завершится сейчас. ${left}`);
}

async function postWrite(path, body) {
  // Метка одноразовая и живёт ограниченно, а рядом идут автообновления снапшота:
  // берём свежую прямо перед записью, иначе запись падает с «сессия устарела».
  let token = csrfToken || '';
  try {
    const snapshotResponse = await fetch('/admin/api/snapshot', { credentials: 'same-origin', cache: 'no-store' });
    if (snapshotResponse.ok) {
      const data = await snapshotResponse.json();
      if (typeof data.csrf === 'string' && data.csrf) {
        token = data.csrf;
        csrfToken = data.csrf;
      }
    }
  } catch (_) {
    // Сеть подвела — пробуем с прежней меткой.
  }
  return fetch(path, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Admin-CSRF': token },
    body: JSON.stringify(body),
  });
}

const WRITE_ERRORS = {
  twitch_required: 'Streamer Plus выдаётся только с подключённым Twitch. Пусть человек подключит канал в боте, затем повторите.',
  not_manual: 'Это право выдано не вручную (оплата или тестовый доступ) — из панели его изменить нельзя.',
  conflict: 'Права изменились. Обновите карточку и подтвердите заново.',
  owner_required: 'Действие доступно только подтверждённому владельцу.',
  owner_not_configured: 'Владелец не настроен: на сервере не задан OWNER_CHAT_ID, поэтому запись недоступна.',
  csrf_denied: 'Сессия устарела. Обновите страницу и повторите.',
  invalid_request: 'Проверьте срок и причину: для «Другое» нужно пояснение.',
  origin_denied: 'Запрос пришёл с чужого адреса — обновите страницу панели.',
  denied: 'Действие запрещено политикой прав.',
  unauthorized: 'Сессия закончилась. Войдите заново через Telegram.',
  unavailable: 'Сервер не смог выполнить действие. Повторите позже.',
  invalid_content_type: 'Сервер не принял формат запроса. Обновите страницу панели.',
};

async function handleWriteResult(response, errorId, successText) {
  if (response.ok) {
    $('grant-dialog').open && $('grant-dialog').close();
    $('revoke-dialog').open && $('revoke-dialog').close();
    await refresh();
    if (currentPerson) await showPerson(String(currentPerson.user_id));
    await loadPeople();
    // Квитанция: владелец должен видеть, что именно записалось.
    showReceipt(successText || 'Готово.');
    return true;
  }
  let code = response.status === 409 ? 'conflict' : 'denied';
  try {
    const payload = await response.json();
    if (payload && typeof payload.error === 'string') code = payload.error;
  } catch (_) {
    // Ответ без тела: остаётся код по статусу.
  }
  showError(errorId, WRITE_ERRORS[code] || `Не удалось выполнить действие (${code}).`);
  return false;
}

async function submitGrant(event) {
  event.preventDefault();
  const target = grantTarget();
  if (!target) { showError('grant-error', 'Укажите Telegram ID получателя: только цифры.'); return; }
  const { plan, expiresAt, reason, note, comment } = grantFields();
  if (!Number.isFinite(expiresAt)) { showError('grant-error', 'Укажите срок доступа.'); return; }
  const manual = currentPerson ? selectedGrant('grant-target-grant') : null;
  const requestKey = `admin-${target.id}-${Date.now().toString(36)}`;
  const base = { request_key: requestKey, expires_at: expiresAt, reason,
                 reason_note: note || null, comment: comment || null };
  const button = $('grant-submit');
  button.disabled = true;
  try {
    const response = manual && manual.plan === plan
      ? await postWrite('/admin/api/access/extend', {
          ...base, grant_id: manual.grant_id, expected_expires_at: manual.expires_at })
      : await postWrite('/admin/api/access/grant', {
          ...base, target_user_id: target.id, plan });
    const verb = manual && manual.plan === plan ? 'Продлено' : 'Выдано';
    await handleWriteResult(response, 'grant-error',
      `${verb}: ${planLabel(plan)} до ${stamp(expiresAt)}. Запись есть в истории ниже.`);
  } catch (_) {
    showError('grant-error', 'Результат пока не подтверждён. Проверьте историю операции.');
  } finally {
    button.disabled = false;
  }
}

async function submitRevoke(event) {
  event.preventDefault();
  if (!currentPerson) return;
  const manual = selectedGrant('revoke-target-grant');
  if (!manual) { showError('revoke-error', 'Ручного доступа нет.'); return; }
  const reason = $('revoke-reason').value;
  const note = $('revoke-note').value.trim();
  const button = $('revoke-submit');
  button.disabled = true;
  try {
    const response = await postWrite('/admin/api/access/revoke', {
      request_key: `admin-revoke-${manual.grant_id}-${Date.now().toString(36)}`,
      grant_id: manual.grant_id, expected_expires_at: manual.expires_at,
      reason, reason_note: note || null, comment: null,
    });
    await handleWriteResult(response, 'revoke-error',
      `${planLabel(manual.plan)} отозван. Человек вернётся к прежнему тарифу, подключения сохранятся.`);
  } catch (_) {
    showError('revoke-error', 'Результат пока не подтверждён. Проверьте историю операции.');
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  snapshot = data;
  csrfToken = data.csrf || csrfToken;
  // Без данных окружение не называется: прежний запасной «staging» вводил в заблуждение.
  const environment = String(data.environment || '').trim();
  put('environment', environment ? environment.toUpperCase() : 'ОКРУЖЕНИЕ НЕИЗВЕСТНО');
  put('foot-environment', environment ? `окружение ${environment.toUpperCase()}` : 'окружение неизвестно');
  setState('telegram-state', (data.telegram || {}).state);
  setState('twitch-state', (data.twitch || {}).state);
  setState('preview-state', (data.preview || {}).state);
  renderMetrics(data);
  renderLive(data.live);
  renderEvents(data.live);
  renderAccess(data.access);
  renderSystem(data);
  renderGrowth(data.growth);
  renderFunnel(data.funnel);
  put('updated-at', `Обновлено ${new Date(data.generated_at * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`);
  lastSuccess = Date.now();
  updateFreshness();
  // Первый снапшот после долгого простоя приходит без тяжёлых срезов: один
  // короткий повтор избавляет владельца от «Нет данных» до ручного обновления.
  const missing = !data.activity || !data.access;
  if (missing && !followUpTimer) {
    followUpTimer = setTimeout(() => { followUpTimer = null; refresh(); }, 3000);
  } else if (!missing && followUpTimer) {
    clearTimeout(followUpTimer);
    followUpTimer = null;
  }
  const { view, param } = currentRoute();
  if (view === 'users') showPerson(param);
}

/* Раздел «Рассылки»: кампании и переписка */
let currentDialogue = null;
// Живой диалог: панель сама подтягивает новые сообщения, без обновления страницы.
let chatTimer = null;
let lastThreadMessageId = 0;
const CHAT_REFRESH_MS = 3000;
let campaignsLoaded = false;

const CAMPAIGN_STATES = {
  draft: 'Черновик', sending: 'Отправляется', sent: 'Отправлена',
  stopped: 'Остановлена', failed: 'Сбой',
};

function showComposeError(text) {
  const node = $('compose-error');
  node.textContent = text || '';
  node.hidden = !text;
}

function setChatBadge(count) {
  for (const id of ['nav-chat-badge', 'chat-tab-badge']) {
    const badge = $(id);
    if (!badge) continue;
    badge.textContent = String(count);
    badge.hidden = !count;
    badge.setAttribute('aria-label', count ? `непрочитанных сообщений: ${count}` : '');
  }
}

async function loadCampaigns() {
  const audience = $('campaigns-audience');
  try {
    const response = await api('/admin/api/broadcasts');
    if (!response.ok) throw new Error('broadcasts');
    renderCampaigns(await response.json());
    campaignsLoaded = true;
  } catch (error) {
    if (String(error) === 'Error: unauthorized') return;
    audience.textContent = 'Расчёт аудитории недоступен';
    audience.dataset.state = 'unknown';
    emptyRow($('campaign-list'), 5, 'Не удалось загрузить кампании');
    emptyRow($('optout-list'), 1, 'Список отписавшихся недоступен');
  }
}

function renderCampaigns(data) {
  const audience = data.audience || {};
  const summary = $('campaigns-audience');
  summary.textContent = `Получат ${value(audience.total)} · недоступны ${value(audience.unreachable)}`
    + ` · отписались ${value(audience.opted_out)} · владелец ${value(audience.owner)}`;
  summary.dataset.state = audience.total ? 'ok' : 'warn';

  const body = $('campaign-list');
  const rows = data.campaigns || [];
  if (!rows.length) {
    emptyRow(body, 5, 'Кампаний пока нет');
  } else {
    body.replaceChildren();
    for (const campaign of rows) {
      const name = document.createElement('td');
      name.dataset.label = 'Название';
      name.textContent = campaign.title;
      const action = document.createElement('td');
      action.dataset.label = 'Действие';
      if (campaign.state === 'draft') {
        const send = document.createElement('button');
        send.className = 'button';
        send.type = 'button';
        send.textContent = 'Отправить';
        send.addEventListener('click', () => sendCampaign(campaign));
        action.append(send);
      } else if (campaign.state === 'sending') {
        const stop = document.createElement('button');
        stop.className = 'button button-quiet';
        stop.type = 'button';
        stop.textContent = 'Остановить';
        stop.addEventListener('click', () => stopCampaign(campaign.id));
        action.append(stop);
      } else {
        action.textContent = '—';
      }
      body.append(wrapRow([
        name,
        cell('Состояние', CAMPAIGN_STATES[campaign.state] || campaign.state),
        cell('Отправлено', `${value(campaign.sent_count)} из ${value(campaign.audience_total)}`),
        cell('Не дошли', `${value(campaign.unreachable_count)} недоступны · ${value(campaign.failed_count)} ошибок`),
        action,
      ]));
    }
  }
  renderOptouts(data.optouts || []);
  setChatBadge(Number(data.unread || 0));
}

function renderOptouts(rows) {
  const list = $('optout-list');
  list.replaceChildren();
  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Пока никто не отписывался';
    list.append(li);
    return;
  }
  for (const person of rows) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = person.display_name
      || (person.username ? `@${person.username}` : `ID ${person.user_id}`);
    const restore = document.createElement('button');
    restore.className = 'button button-quiet';
    restore.type = 'button';
    restore.textContent = 'Вернуть';
    restore.addEventListener('click', () => restoreOptout(person.user_id));
    li.append(label, restore);
    list.append(li);
  }
}

async function saveDraft(event) {
  event.preventDefault();
  showComposeError('');
  const title = $('compose-title').value.trim();
  const body = $('compose-body').value.trim();
  if (!title || !body) {
    showComposeError('Нужны название и текст сообщения.');
    return;
  }
  const buttonOn = $('compose-button-on').checked;
  const buttonText = buttonOn ? $('compose-button-text').value.trim() : '';
  const buttonUrl = buttonOn ? $('compose-button-url').value.trim() : '';
  if (buttonOn && (!buttonText || !buttonUrl.startsWith('https://'))) {
    showComposeError('Для кнопки нужны текст и ссылка, начинающаяся с https://');
    return;
  }
  const response = await postWrite('/admin/api/broadcasts', {
    request_key: `draft-${Date.now()}`,
    title, body,
    button_text: buttonText || null,
    button_url: buttonUrl || null,
  });
  if (!response.ok) {
    showComposeError('Не удалось сохранить черновик. Обновите страницу и повторите.');
    return;
  }
  const saved = await response.json();
  const file = $('compose-image').files[0];
  if (file) {
    const uploaded = await uploadCampaignImage(saved.campaign_id, file);
    if (!uploaded) {
      showComposeError('Черновик сохранён, но картинку принять не удалось: нужен JPEG или PNG до 5 МБ.');
      await loadCampaigns();
      return;
    }
  }
  $('compose-form').reset();
  $('compose-button-fields').hidden = true;
  showReceipt('Черновик сохранён. Отправка — только по кнопке «Отправить».');
  await loadCampaigns();
}

async function uploadCampaignImage(campaignId, file) {
  // Метка одноразовая: берём свежую прямо перед загрузкой файла.
  let token = csrfToken || '';
  try {
    const snapshot = await fetch('/admin/api/snapshot', { credentials: 'same-origin', cache: 'no-store' });
    if (snapshot.ok) {
      const data = await snapshot.json();
      if (typeof data.csrf === 'string' && data.csrf) { token = data.csrf; csrfToken = data.csrf; }
    }
  } catch (_) {
    // Пробуем с прежней меткой.
  }
  const form = new FormData();
  form.append('image', file);
  const response = await fetch(`/admin/api/broadcasts/${campaignId}/image`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'X-Admin-CSRF': token }, body: form,
  });
  return response.ok;
}

async function sendCampaign(campaign) {
  const summary = $('campaigns-audience').textContent;
  // Массовая отправка — действие с последствиями, поэтому подтверждение словами.
  const confirmed = window.confirm(
    `Отправить «${campaign.title}»?\n\n${summary}\n\nСообщение уйдёт живым людям, отменить отправленное нельзя.`,
  );
  if (!confirmed) return;
  const response = await postWrite(
    `/admin/api/broadcasts/${campaign.id}/send`,
    { request_key: `send-${campaign.id}-${Date.now()}` },
  );
  if (!response.ok) {
    showComposeError('Не удалось начать отправку. Обновите страницу и повторите.');
    return;
  }
  showReceipt('Отправка началась. Прогресс виден в списке кампаний.');
  await loadCampaigns();
}

async function stopCampaign(campaignId) {
  if (!window.confirm('Остановить отправку? Уже отправленное останется у людей.')) return;
  const response = await postWrite(`/admin/api/broadcasts/${campaignId}/stop`, {});
  if (!response.ok) {
    showComposeError('Не удалось остановить. Обновите страницу и повторите.');
    return;
  }
  showReceipt('Остаток остановлен.');
  await loadCampaigns();
}

async function restoreOptout(userId) {
  const response = await postWrite(`/admin/api/optouts/${userId}/restore`, {});
  if (response.ok) {
    showReceipt('Человек снова получает рассылки.');
    await loadCampaigns();
  }
}

async function loadDialogues(query) {
  const list = $('dialogue-list');
  try {
    const response = await api(`/admin/api/dialogues?q=${encodeURIComponent(query || '')}`);
    if (!response.ok) throw new Error('dialogues');
    const data = await response.json();
    renderDialogues(data.dialogues || []);
    setChatBadge(Number(data.unread || 0));
  } catch (error) {
    if (String(error) === 'Error: unauthorized') return;
    emptyRow(list, 1, 'Не удалось загрузить переписку');
  }
}

const ATTACHMENT_LABELS = {
  photo: 'Картинка', sticker: 'Стикер', animation: 'Гифка', video: 'Видео',
  video_note: 'Видеокружок', voice: 'Голосовое', audio: 'Аудио',
  document: 'Файл', contact: 'Контакт', location: 'Геолокация',
  venue: 'Место', poll: 'Опрос', dice: 'Кубик',
};
const ATTACHMENT_ICONS = {
  photo: '📷', sticker: '🩵', animation: '🎞', video: '📹', video_note: '⭕️',
  voice: '🎤', audio: '🎵', document: '📎', contact: '👤', location: '📍',
  venue: '📍', poll: '📊', dice: '🎲',
};

function attachmentLabel(kind) {
  return ATTACHMENT_LABELS[kind] || 'Вложение';
}

function attachmentChip(kind) {
  const chip = document.createElement('p');
  chip.className = 'bubble-chip';
  chip.textContent = `${ATTACHMENT_ICONS[kind] || '📎'} ${attachmentLabel(kind)}`;
  return chip;
}

function renderDialogues(rows) {
  const list = $('dialogue-list');
  list.replaceChildren();
  if (!rows.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Переписки пока нет';
    list.append(li);
    return;
  }
  for (const dialogue of rows) {
    const li = document.createElement('li');
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'dialogue-link';
    if (currentDialogue === dialogue.user_id) link.setAttribute('aria-current', 'true');
    const name = document.createElement('span');
    name.className = 'dialogue-name';
    name.textContent = dialogue.display_name
      || (dialogue.username ? `@${dialogue.username}` : `ID ${dialogue.user_id}`);
    const preview = document.createElement('span');
    preview.className = 'dialogue-preview';
    const fromOwner = dialogue.last_direction === 'out' ? 'Вы: ' : '';
    const last = dialogue.last_body
      || (dialogue.last_attachment ? attachmentLabel(dialogue.last_attachment)
        : Number(dialogue.has_history) ? 'Вложение' : 'Сообщений пока нет');
    preview.textContent = `${fromOwner}${last}`;
    const meta = document.createElement('span');
    meta.className = 'dialogue-meta';
    const unread = Number(dialogue.unread_count || 0);
    if (unread) meta.textContent = `${unread} новых`;
    else if (Number(dialogue.has_history)) meta.textContent = stamp(dialogue.last_message_at);
    else meta.textContent = 'не писали';
    link.append(name, preview, meta);
    link.addEventListener('click', () => openDialogue(dialogue.user_id));
    li.append(link);
    list.append(li);
  }
  // Диалог открываем сами: пустая правая половина заставляет гадать, куда нажать.
  if (!currentDialogue) {
    const first = rows.find((row) => Number(row.unread_count || 0) > 0) || rows[0];
    openDialogue(first.user_id);
  }
}

async function openDialogue(userId) {
  currentDialogue = userId;
  const note = $('thread-note');
  try {
    const response = await api(`/admin/api/dialogues/${userId}`);
    if (!response.ok) throw new Error('dialogue');
    const data = await response.json();
    renderThread(data.messages || []);
    // Прочитанное отмечаем после показа: счётчик не должен врать.
    await postWrite(`/admin/api/dialogues/${userId}/read`, {});
    await loadDialogues($('dialogues-query').value.trim());
  } catch (error) {
    if (String(error) === 'Error: unauthorized') return;
    note.textContent = 'Переписка недоступна';
  }
}

function chatIsOpen() {
  const panel = $('chat-panel');
  const view = $('view-campaigns');
  return Boolean(panel && view && !panel.hidden && !view.hidden && !document.hidden);
}

async function refreshChat() {
  if (!chatIsOpen()) return;
  await loadDialogues($('dialogues-query').value.trim());
  if (!currentDialogue) return;
  const response = await api(`/admin/api/dialogues/${currentDialogue}`);
  if (!response.ok) return;
  const messages = (await response.json()).messages || [];
  const newest = messages.length ? Number(messages[messages.length - 1].id) : 0;
  if (newest === lastThreadMessageId) return;
  // Перерисовываем только когда правда пришло новое: иначе мигает ввод.
  renderThread(messages);
  await postWrite(`/admin/api/dialogues/${currentDialogue}/read`, {});
  setChatBadge(0);
}

function startChatRefresh() {
  if (chatTimer) return;
  chatTimer = setInterval(() => {
    refreshChat().catch(() => {});
  }, CHAT_REFRESH_MS);
}

function stopChatRefresh() {
  if (!chatTimer) return;
  clearInterval(chatTimer);
  chatTimer = null;
}

function renderThread(messages) {
  const list = $('thread');
  list.replaceChildren();
  if (!messages.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Сообщений нет';
    list.append(li);
    return;
  }
  for (const message of messages) {
    const li = document.createElement('li');
    li.className = message.direction === 'out' ? 'bubble out' : 'bubble in';
    const kind = message.attachment;
    const hasImage = Boolean(message.image_name);
    if (hasImage) {
      const image = document.createElement('img');
      image.className = 'bubble-image';
      image.loading = 'lazy';
      image.alt = 'Картинка из переписки';
      image.src = `/admin/api/media/${encodeURIComponent(message.image_name)}`;
      li.append(image);
    } else if (kind && kind !== 'photo') {
      // Стикер, гифку или голосовое показать нечем, но человек их прислал.
      li.append(attachmentChip(kind));
    }
    if (message.body || (!hasImage && !kind)) {
      const text = document.createElement('p');
      text.className = 'bubble-text';
      text.textContent = message.body || 'Вложение';
      li.append(text);
    }
    const time = document.createElement('span');
    time.className = 'bubble-time';
    const failed = message.direction === 'out' && message.delivery !== 'sent';
    time.textContent = `${stamp(message.created_at)}${failed ? ' · не доставлено' : ''}`;
    li.append(time);
    list.append(li);
  }
  lastThreadMessageId = Number(messages[messages.length - 1].id) || 0;
  // Как в мессенджере: диалог открывается на последнем сообщении.
  const box = $('thread');
  if (box) box.scrollTop = box.scrollHeight;
}

async function sendReply(event) {
  event.preventDefault();
  const error = $('reply-error');
  error.hidden = true;
  const text = $('reply-body').value.trim();
  const file = $('reply-image').files[0];
  if (!text && !file) {
    error.textContent = 'Нужен текст или картинка.';
    error.hidden = false;
    return;
  }
  if (!currentDialogue) { error.textContent = 'Сначала выберите диалог.'; error.hidden = false; return; }
  const requestKey = `reply-${currentDialogue}-${Date.now()}`;
  let response;
  if (file) {
    // Картинка уходит вместе с текстом, поэтому это multipart, а не JSON.
    let token = csrfToken || '';
    try {
      const snapshot = await fetch('/admin/api/snapshot', { credentials: 'same-origin', cache: 'no-store' });
      if (snapshot.ok) {
        const data = await snapshot.json();
        if (typeof data.csrf === 'string' && data.csrf) { token = data.csrf; csrfToken = data.csrf; }
      }
    } catch (_) {
      // Пробуем с прежней меткой.
    }
    const form = new FormData();
    form.append('request_key', requestKey);
    form.append('body', text);
    form.append('image', file);
    response = await fetch(`/admin/api/dialogues/${currentDialogue}/reply`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'X-Admin-CSRF': token }, body: form,
    });
  } else {
    response = await postWrite(`/admin/api/dialogues/${currentDialogue}/reply`, {
      request_key: requestKey, body: text,
    });
  }
  if (!response.ok) {
    error.textContent = 'Не удалось отправить. Проверьте соединение и повторите.';
    error.hidden = false;
    return;
  }
  $('reply-form').reset();
  await openDialogue(currentDialogue);
}

async function deleteThread() {
  if (!currentDialogue) return;
  if (!window.confirm('Удалить переписку? Сообщения исчезнут без возможности вернуть.')) return;
  const response = await postWrite(`/admin/api/dialogues/${currentDialogue}/delete`, {});
  if (!response.ok) return;
  currentDialogue = null;
  renderThread([]);
  put('thread-note', 'Переписка удалена');
  await loadDialogues($('dialogues-query').value.trim());
}

function updateFreshness() {
  const item = $('freshness');
  if (!lastSuccess) { item.textContent = 'Ожидание данных'; item.dataset.state = 'unknown'; return; }
  if (Date.now() - lastSuccess > 90000) { item.textContent = 'Данные устарели'; item.dataset.state = 'stale'; }
  else { item.textContent = 'Данные актуальны'; item.dataset.state = 'fresh'; }
}

async function refresh() {
  const button = $('refresh');
  button.disabled = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/admin/api/snapshot', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
    if (response.status === 401) { window.location.assign('/admin'); return; }
    if (response.status === 503) throw new Error('snapshot_offline');
    if (!response.ok) throw new Error('snapshot');
    render(await response.json());
    $('notice').hidden = true;
  } catch (error) {
    // Совет должен соответствовать причине: отказ сервера — не «проверьте соединение».
    const offline = String(error) === 'Error: snapshot_offline';
    showError('notice', offline
      ? 'Сервер не смог собрать данные. Попробуйте обновить через минуту.'
      : 'Не удалось получить данные. Проверьте соединение и нажмите «Обновить».');
    updateFreshness();
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
  }
}

$('notice-retry').addEventListener('click', () => { refresh(); });

/* Переключение вкладок «Доступы» */
const ACCESS_TABS = [
  ['tab-active', 'access-active'],
  ['tab-history', 'access-history'],
  ['tab-expiring', 'access-expiring'],
];
for (const [tabId, panelId] of ACCESS_TABS) {
  const activate = () => {
    for (const [otherTab, otherPanel] of ACCESS_TABS) {
      const selected = otherTab === tabId;
      $(otherTab).setAttribute('aria-selected', selected ? 'true' : 'false');
      $(otherTab).setAttribute('tabindex', selected ? '0' : '-1');
      $(otherPanel).hidden = !selected;
    }
  };
  $(tabId).addEventListener('click', activate);
  // Вкладки должны переключаться стрелками, Home и End: мышью это делает не каждый.
  $(tabId).addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const order = ACCESS_TABS.map(([tab]) => tab);
    const step = event.key === 'ArrowRight' ? 1 : -1;
    const next = event.key === 'Home' ? order[0]
      : event.key === 'End' ? order[order.length - 1]
        : order[(order.indexOf(tabId) + step + order.length) % order.length];
    const target = $(next);
    target.click();
    target.focus();
  });
}

/* Раздел «Рассылки»: вкладки, формы и кнопки */
const CAMPAIGN_TABS = [['tab-campaigns', 'campaigns-panel'], ['tab-chat', 'chat-panel']];
for (const [tabId, panelId] of CAMPAIGN_TABS) {
  const activate = () => {
    for (const [otherTab, otherPanel] of CAMPAIGN_TABS) {
      const selected = otherTab === tabId;
      $(otherTab).setAttribute('aria-selected', selected ? 'true' : 'false');
      $(otherTab).setAttribute('tabindex', selected ? '0' : '-1');
      $(otherPanel).hidden = !selected;
    }
    // Переписка живёт, пока её видно: иначе панель зря дёргает сервер.
    if (tabId === 'tab-chat') startChatRefresh();
    else stopChatRefresh();
  };
  $(tabId).addEventListener('click', activate);
  $(tabId).addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const order = CAMPAIGN_TABS.map(([tab]) => tab);
    const step = event.key === 'ArrowRight' ? 1 : -1;
    const next = event.key === 'Home' ? order[0]
      : event.key === 'End' ? order[order.length - 1]
        : order[(order.indexOf(tabId) + step + order.length) % order.length];
    $(next).click();
    $(next).focus();
  });
}

$('compose-form').addEventListener('submit', saveDraft);
$('compose-reset').addEventListener('click', () => {
  $('compose-form').reset();
  $('compose-button-fields').hidden = true;
  showComposeError('');
});
$('compose-button-on').addEventListener('change', (event) => {
  $('compose-button-fields').hidden = !event.target.checked;
});
$('dialogues-form').addEventListener('submit', (event) => {
  event.preventDefault();
  loadDialogues($('dialogues-query').value.trim());
});
$('reply-form').addEventListener('submit', sendReply);
// Как в мессенджере: Enter отправляет, Shift+Enter переносит строку.
$('reply-body').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  $('reply-form').requestSubmit();
});
$('thread-delete').addEventListener('click', deleteThread);

$('people-form').addEventListener('submit', (event) => {
  event.preventDefault();
  clearTimeout(peopleTimer);
  loadPeople();
});

// Поиск реагирует на ввод с небольшой паузой, Enter отправляет сразу.
$('people-query').addEventListener('input', () => {
  clearTimeout(peopleTimer);
  peopleTimer = setTimeout(loadPeople, 300);
});
$('people-filter').addEventListener('change', () => {
  clearTimeout(peopleTimer);
  loadPeople();
});

$('grant-open').addEventListener('click', () => {
  if (!currentPerson) return;
  $('grant-error').hidden = true;
  $('grant-target-id-row').hidden = true;
  $('grant-target-id').value = '';
  put('grant-target', `Получатель: ${currentPerson.display_name || currentPerson.username || currentPerson.user_id}`);
  fillGrantChoices();
  applyGrantAvailability();
  setExpiryFloor();
  toggleOtherNote('grant-reason', 'grant-note', 'grant-note-label');
  updateGrantSummary();
  $('grant-dialog').showModal();
});

// Выдача человеку, которого нет в поиске: доступ можно дать по Telegram ID.
$('grant-by-id-open').addEventListener('click', () => {
  currentPerson = null;
  $('grant-error').hidden = true;
  $('grant-target-id-row').hidden = false;
  $('grant-target-id').value = '';
  put('grant-target', 'Получатель: укажите Telegram ID');
  fillGrantChoices();
  applyGrantAvailability();
  setExpiryFloor();
  toggleOtherNote('grant-reason', 'grant-note', 'grant-note-label');
  updateGrantSummary();
  $('grant-dialog').showModal();
});
$('grant-target-id').addEventListener('input', updateGrantSummary);
$('grant-cancel').addEventListener('click', () => $('grant-dialog').close());
$('revoke-open').addEventListener('click', () => {
  if (!currentPerson) return;
  $('revoke-error').hidden = true;
  $('grant-dialog').close();
  fillGrantChoices();
  toggleOtherNote('revoke-reason', 'revoke-note', 'revoke-note-label');
  updateRevokeSummary();
  $('revoke-dialog').showModal();
});
$('revoke-cancel').addEventListener('click', () => $('revoke-dialog').close());
$('grant-form').addEventListener('submit', submitGrant);
$('revoke-form').addEventListener('submit', submitRevoke);
for (const field of ['grant-plan', 'grant-expiry', 'grant-reason', 'grant-note']) {
  $(field).addEventListener('change', () => { toggleOtherNote('grant-reason', 'grant-note', 'grant-note-label'); updateGrantSummary(); });
  $(field).addEventListener('input', () => { toggleOtherNote('grant-reason', 'grant-note', 'grant-note-label'); updateGrantSummary(); });
}
$('revoke-reason').addEventListener('change', () => toggleOtherNote('revoke-reason', 'revoke-note', 'revoke-note-label'));
$('revoke-reason').addEventListener('input', () => toggleOtherNote('revoke-reason', 'revoke-note', 'revoke-note-label'));

// Срок в прошлом отсекаем в интерфейсе, а не только на сервере.
function setExpiryFloor() {
  const input = $('grant-expiry');
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
  input.min = now.toISOString().slice(0, 16);
  if (input.value && input.value < input.min) input.value = input.min;
}
$('grant-target-grant').addEventListener('change', updateGrantSummary);
$('revoke-target-grant').addEventListener('change', updateRevokeSummary);

$('refresh').addEventListener('click', refresh);
window.addEventListener('hashchange', navigate);
navigate();
refresh();
setInterval(refresh, 30000);
setInterval(updateFreshness, 5000);
