const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('ru-RU');
const VIEWS = ['overview', 'users', 'access', 'system', 'growth', 'payments'];
const STATE_LABELS = { ok: 'Работает', degraded: 'Сбой', disabled: 'Отключён', unknown: 'Нет данных' };
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
  else if (view === 'users') showPerson('');
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

  const note = $('access-note');
  if (note) {
    const truncated = (rows !== null && rows !== undefined && rows.length >= ROW_LIMIT)
      || (events !== null && events !== undefined && events.length >= ROW_LIMIT);
    note.hidden = !truncated;
    note.textContent = truncated ? `Показаны первые ${ROW_LIMIT} записей, а не весь список.` : '';
  }
}

function renderSystem(data) {
  setState('system-telegram', (data.telegram || {}).state);
  setState('system-twitch', (data.twitch || {}).state);
  setState('system-preview', (data.preview || {}).state);
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

  const errors = data.errors || {};
  for (const key of ['poller', 'eventsub', 'preview', 'database', 'directory']) put(`error-${key}`, errors[key] || 'Нет');
  const resources = data.resources || {};
  put('resource-cpu', resources.process_cpu_percent === null || resources.process_cpu_percent === undefined ? 'Нет данных' : `${number.format(resources.process_cpu_percent)} %`);
  put('resource-ram', bytes(resources.process_ram_bytes));
  put('resource-disk', bytes(resources.db_volume_free_bytes));
  put('resource-db', `${bytes(resources.db_file_bytes)} / ${bytes(resources.wal_file_bytes)}`);
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
  emptyRow(body, 4, 'Загрузка…');
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
  put('person-card-id', id ? `ID ${id}` : 'ID не выбран');
  put('person-empty', id ? 'Загрузка…' : 'Выберите человека из списка или найдите его по имени, ID либо Twitch.');
  if (!/^\d+$/.test(id)) return;
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
  node.textContent = text;
  node.hidden = !text;
  if (text) {
    $('grant-dialog').open && $('grant-error').hidden === false;
  }
}

function manualGrants() {
  return ((currentPerson || {}).grants || []).filter((item) => item.source === 'manual');
}

function fillGrantChoices() {
  for (const id of ['grant-target-grant', 'revoke-target-grant']) {
    const select = $(id);
    select.replaceChildren();
    for (const grant of manualGrants()) {
      const option = document.createElement('option');
      option.value = grant.grant_id;
      option.textContent = `${planLabel(grant.plan)} · до ${stamp(grant.expires_at)}`;
      select.append(option);
    }
  }
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

function updateGrantSummary() {
  if (!currentPerson) return;
  const { plan, expiresAt, reason, note } = grantFields();
  const who = currentPerson.display_name || currentPerson.username || currentPerson.user_id;
  const manual = selectedGrant('grant-target-grant');
  const verb = manual && manual.plan === plan ? 'Продлить' : 'Выдать';
  const when = Number.isFinite(expiresAt) ? stamp(expiresAt) : 'укажите срок';
  const reasonLabel = { compensation: 'Компенсация', testing: 'Тестирование',
                        partnership: 'Партнёрство', other: 'Другое' }[reason] || reason;
  const extra = reason === 'other' && !note ? ' · нужно пояснение для «Другое»' : '';
  put('grant-summary', `${verb} ${planLabel(plan)} пользователю ${who} до ${when}. `
    + `Причина: ${reasonLabel}${extra}. Списания денег не будет.`);
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
  return fetch(path, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Admin-CSRF': csrfToken || '' },
    body: JSON.stringify(body),
  });
}

async function handleWriteResult(response, errorId) {
  if (response.ok) {
    $('grant-dialog').open && $('grant-dialog').close();
    $('revoke-dialog').open && $('revoke-dialog').close();
    await refresh();
    if (currentPerson) await showPerson(String(currentPerson.user_id));
    await loadPeople();
    return true;
  }
  if (response.status === 409) {
    showError(errorId, 'Права изменились. Обновите карточку и подтвердите заново.');
  } else if (response.status === 403) {
    showError(errorId, 'Действие запрещено: проверьте причину и права человека.');
  } else {
    showError(errorId, 'Не удалось выполнить действие. Повторите попытку.');
  }
  return false;
}

async function submitGrant(event) {
  event.preventDefault();
  if (!currentPerson) return;
  const { plan, expiresAt, reason, note, comment } = grantFields();
  if (!Number.isFinite(expiresAt)) { showError('grant-error', 'Укажите срок доступа.'); return; }
  const manual = selectedGrant('grant-target-grant');
  const requestKey = `admin-${currentPerson.user_id}-${Date.now().toString(36)}`;
  const base = { request_key: requestKey, expires_at: expiresAt, reason,
                 reason_note: note || null, comment: comment || null };
  const button = $('grant-submit');
  button.disabled = true;
  try {
    const response = manual && manual.plan === plan
      ? await postWrite('/admin/api/access/extend', {
          ...base, grant_id: manual.grant_id, expected_expires_at: manual.expires_at })
      : await postWrite('/admin/api/access/grant', {
          ...base, target_user_id: currentPerson.user_id, plan });
    await handleWriteResult(response, 'grant-error');
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
    await handleWriteResult(response, 'revoke-error');
  } catch (_) {
    showError('revoke-error', 'Результат пока не подтверждён. Проверьте историю операции.');
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  snapshot = data;
  csrfToken = data.csrf || csrfToken;
  put('environment', (data.environment || 'staging').toUpperCase());
  put('foot-environment', `окружение ${(data.environment || 'staging').toUpperCase()}`);
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
  fillGrantChoices();
  updateGrantSummary();
  $('grant-dialog').showModal();
});
$('grant-cancel').addEventListener('click', () => $('grant-dialog').close());
$('revoke-open').addEventListener('click', () => {
  if (!currentPerson) return;
  $('revoke-error').hidden = true;
  $('grant-dialog').close();
  fillGrantChoices();
  updateRevokeSummary();
  $('revoke-dialog').showModal();
});
$('revoke-cancel').addEventListener('click', () => $('revoke-dialog').close());
$('grant-form').addEventListener('submit', submitGrant);
$('revoke-form').addEventListener('submit', submitRevoke);
for (const field of ['grant-plan', 'grant-expiry', 'grant-reason', 'grant-note']) {
  $(field).addEventListener('change', updateGrantSummary);
  $(field).addEventListener('input', updateGrantSummary);
}
$('grant-target-grant').addEventListener('change', updateGrantSummary);
$('revoke-target-grant').addEventListener('change', updateRevokeSummary);

$('refresh').addEventListener('click', refresh);
window.addEventListener('hashchange', navigate);
navigate();
refresh();
setInterval(refresh, 30000);
setInterval(updateFreshness, 5000);
