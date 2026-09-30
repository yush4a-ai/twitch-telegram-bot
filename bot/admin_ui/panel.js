const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat('ru-RU');
let lastSuccess = 0;

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
function state(id, raw) {
  const labels = {ok:'Работает',degraded:'Сбой',disabled:'Отключён',unknown:'Нет данных'};
  $(id).textContent = labels[raw] || labels.unknown;
  $(id).dataset.state = raw || 'unknown';
}
function put(id, text) { $(id).textContent = text; }
function renderLive(rows) {
  const list = $('live-list');
  list.replaceChildren();
  if (rows === null) { const p = document.createElement('p'); p.className='empty'; p.textContent='Список эфиров временно недоступен'; list.append(p); return; }
  if (!rows.length) { const p = document.createElement('p'); p.className='empty'; p.textContent='Сейчас нет зафиксированных эфиров'; list.append(p); return; }
  for (const row of rows) {
    const article = document.createElement('div'); article.className='live-row';
    for (const [label, content, cls] of [
      ['Канал', row.login || 'Нет данных', 'live-login'],
      ['Назначений', value(row.destinations), ''],
      ['Зрителей', value(row.viewers), ''],
      ['Наблюдение', row.observed_at ? new Date(row.observed_at * 1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}) : 'Нет данных', '']
    ]) {
      const span=document.createElement('span'); if (cls) span.className=cls;
      const small=document.createElement('small'); small.textContent=label;
      span.append(small,document.createTextNode(content)); article.append(span);
    }
    list.append(article);
  }
}
function render(data) {
  put('environment',(data.environment || 'staging').toUpperCase());
  state('telegram-state',data.telegram?.state);
  put('telegram-polling',data.telegram?.polling_running === null ? 'Нет данных' : data.telegram?.polling_running ? 'Активен' : 'Остановлен');
  put('telegram-delivery',data.telegram?.delivery_verified === 'verified' ? 'Проверена' : 'Не проверена');
  state('twitch-state',data.twitch?.state);
  put('twitch-success',age(data.twitch?.last_success_age_seconds));
  put('twitch-eventsub',`${value(data.twitch?.eventsub_ready)} / ${value(data.twitch?.eventsub_configured)}`);
  put('twitch-auth',value(data.twitch?.auth_blocked_logins));
  state('preview-state',data.preview?.state);
  put('preview-active',`${value(data.preview?.active_sessions)} / ${value(data.preview?.active_jobs)}`);
  put('preview-success',age(data.preview?.last_success_age_seconds));
  put('preview-reason',data.preview?.disabled_reason || (data.preview?.state === 'unknown' ? 'Нет данных' : 'Нет'));
  put('stat-users',value(data.audience?.private_users));
  put('stat-groups',value(data.audience?.groups));
  put('stat-tracked',value(data.audience?.tracked_channels));
  put('stat-channels',value(data.audience?.unique_twitch_channels));
  renderLive(data.live);
  put('queue-pending',value(data.queues?.pending_deliveries));
  put('queue-age',age(data.queues?.oldest_pending_age_seconds));
  put('queue-deferred',value(data.queues?.deferred_reports));
  for (const key of ['poller','eventsub','preview','database']) put(`error-${key}`,data.errors?.[key] || 'Нет');
  put('resource-cpu',data.resources?.process_cpu_percent === null ? 'Нет данных' : `${value(data.resources?.process_cpu_percent)} %`);
  put('resource-ram',bytes(data.resources?.process_ram_bytes));
  put('resource-disk',bytes(data.resources?.db_volume_free_bytes));
  put('resource-db',`${bytes(data.resources?.db_file_bytes)} / ${bytes(data.resources?.wal_file_bytes)}`);
  put('updated-at',`Обновлено ${new Date(data.generated_at * 1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}`);
  lastSuccess=Date.now(); updateFreshness();
}
function updateFreshness() {
  const item=$('freshness');
  if (!lastSuccess) {item.textContent='Ожидание данных';item.dataset.state='unknown';return;}
  if (Date.now()-lastSuccess>90000) {item.textContent='Данные устарели';item.dataset.state='stale';}
  else {item.textContent='Данные актуальны';item.dataset.state='fresh';}
}
async function refresh() {
  const button=$('refresh'); button.disabled=true;
  const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),8000);
  try {
    const response=await fetch('/admin/api/snapshot',{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if (response.status===401) {window.location.assign('/admin');return;}
    if (!response.ok) throw new Error('snapshot');
    render(await response.json()); $('notice').hidden=true;
  } catch (_) {
    $('notice').textContent='Не удалось обновить данные. Проверьте соединение и нажмите «Обновить».';
    $('notice').hidden=false; updateFreshness();
  } finally {clearTimeout(timeout);button.disabled=false;}
}
$('refresh').addEventListener('click',refresh);
refresh(); setInterval(refresh,30000); setInterval(updateFreshness,5000);
