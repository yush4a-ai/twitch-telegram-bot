const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('ru-RU');
const VIEWS = ['overview', 'users', 'access', 'system', 'growth', 'payments'];
const STATE_LABELS = { ok: 'Работает', degraded: 'Сбой', disabled: 'Отключён', unknown: 'Нет данных' };
const SOURCE_LABELS = { test: 'тестовый доступ', manual: 'ручная выдача', paid: 'оплата', mock: 'проверка оплаты' };
const ACTION_LABELS = { grant: 'Выдан', revoke: 'Отозван', extend: 'Продлён' };
let lastSuccess = 0;
let snapshot = null;

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
  return new Date(seconds * 1000).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
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
  if (view === 'users' && param) showUserCard(param);
  else if (view === 'users') showUserCard('');
  document.querySelector('main').scrollIntoView({ block: 'start' });
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
  put('stat-active-today', 'Нет данных');
  put('stat-new-7d', 'Нет данных');

  const attention = data.attention || [];
  const list = $('attention-list');
  list.replaceChildren();
  const badge = $('nav-system-badge');
  if (!attention.length) {
    const li = document.createElement('li');
    li.className = 'empty-line';
    li.textContent = 'Нет открытых проблем';
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
  }

  const danger = attention.some((item) => item.severity === 'danger')
    || (data.twitch || {}).state === 'degraded'
    || (data.preview || {}).state === 'degraded'
    || (data.telegram || {}).state === 'degraded';
  const health = $('health-line');
  if (data.errors && data.errors.database) {
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
      activeBody.append(row([
        ['Получатель', item.person_id ? String(item.person_id) : String(item.subject_id)],
        ['План', item.plan === 'streamer_plus' ? 'Streamer Plus' : 'Viewer Plus'],
        ['Источник', SOURCE_LABELS[item.source] || item.source || 'Не определён'],
        ['Срок', stamp(item.expires_at)],
      ]));
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
}

function renderSystem(data) {
  setState('system-telegram', (data.telegram || {}).state);
  setState('system-twitch', (data.twitch || {}).state);
  setState('system-preview', (data.preview || {}).state);
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
    put('backup-retention', backup.retention === null || backup.retention === undefined ? 'Нет данных' : String(backup.retention));
  }
  put('restore-verified', (backup && backup.restore_verified) ? 'Проверена' : 'Не проводилась');

  const errors = data.errors || {};
  for (const key of ['poller', 'eventsub', 'preview', 'database', 'directory']) put(`error-${key}`, errors[key] || 'Нет');
  const resources = data.resources || {};
  put('resource-cpu', resources.process_cpu_percent === null || resources.process_cpu_percent === undefined ? 'Нет данных' : `${number.format(resources.process_cpu_percent)} %`);
  put('resource-ram', bytes(resources.process_ram_bytes));
  put('resource-disk', bytes(resources.db_volume_free_bytes));
  put('resource-db', `${bytes(resources.db_file_bytes)} / ${bytes(resources.wal_file_bytes)}`);
}

function renderGrowth(rows) {
  const body = $('growth-body');
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

function showUserCard(rawId) {
  const id = String(rawId || '').trim();
  put('user-card-id', id ? `ID ${id}` : 'ID не выбран');
  put('user-card-plans', 'Нет данных');
  put('user-card-expiry', 'Нет данных');
  put('user-card-source', 'Нет данных');
  if (!id || !snapshot || !snapshot.access) return;
  const matches = (snapshot.access.active_rows || []).filter(
    (item) => String(item.person_id) === id || String(item.subject_id) === id,
  );
  if (!matches.length) {
    put('user-card-plans', 'Действующих прав нет');
    return;
  }
  put('user-card-plans', matches.map((item) => item.plan === 'streamer_plus' ? 'Streamer Plus' : 'Viewer Plus').join(', '));
  put('user-card-expiry', matches.map((item) => stamp(item.expires_at)).join(', '));
  put('user-card-source', matches.map((item) => SOURCE_LABELS[item.source] || item.source).join(', '));
}

function render(data) {
  snapshot = data;
  put('environment', (data.environment || 'staging').toUpperCase());
  setState('telegram-state', (data.telegram || {}).state);
  setState('twitch-state', (data.twitch || {}).state);
  setState('preview-state', (data.preview || {}).state);
  renderMetrics(data);
  renderLive(data.live);
  renderAccess(data.access);
  renderSystem(data);
  renderGrowth(data.growth);
  put('updated-at', `Обновлено ${new Date(data.generated_at * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`);
  lastSuccess = Date.now();
  updateFreshness();
  const { view, param } = currentRoute();
  if (view === 'users') showUserCard(param);
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
    if (!response.ok) throw new Error('snapshot');
    render(await response.json());
    $('notice').hidden = true;
  } catch (_) {
    $('notice').textContent = 'Не удалось обновить данные. Проверьте соединение и нажмите «Обновить».';
    $('notice').hidden = false;
    updateFreshness();
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
  }
}

/* Переключение вкладок «Доступы» */
for (const [tabId, panelId] of [['tab-active', 'access-active'], ['tab-history', 'access-history']]) {
  $(tabId).addEventListener('click', () => {
    $(tabId).setAttribute('aria-selected', 'true');
    $(tabId === 'tab-active' ? 'tab-history' : 'tab-active').setAttribute('aria-selected', 'false');
    $(panelId).hidden = false;
    $(panelId === 'access-active' ? 'access-history' : 'access-active').hidden = true;
  });
}

$('user-search').addEventListener('submit', (event) => {
  event.preventDefault();
  const id = $('user-id').value.trim();
  window.location.hash = id ? `#/users/${encodeURIComponent(id)}` : '#/users';
});

$('refresh').addEventListener('click', refresh);
window.addEventListener('hashchange', navigate);
navigate();
refresh();
setInterval(refresh, 30000);
setInterval(updateFreshness, 5000);
