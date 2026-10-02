import { element, panel, action, dialog } from './components.js';
import { ApiError } from './api.js';

const products = {
  viewer_plus: {
    title: 'Viewer Plus',
    detail: 'До 200 стримеров, фильтры, категории, напоминания и пять выбранных видеопревью.',
  },
  streamer_plus: {
    title: 'Streamer Plus',
    detail: 'Оформление публикаций, кнопки, живое превью и измеряемые публикации в подключённых сообществах.',
  },
};
const statuses = {
  pending: 'Ожидает подтверждения', paid: 'Подтверждён',
  cancelled: 'Отменён', expired: 'Истёк', refunded: 'Возврат',
};
const sourceName = (source) => source === 'test' || source === 'mock' ? 'Тестовый доступ' : 'Активный доступ';
const dateText = (value) => value ? new Date(value * 1000).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }) : '';

export function createSubscriptionFeature(api, getRouter, onAccessChanged) {
  let state = null;
  let catalog = null;
  let loading = false;
  let loadToken = 0;
  let pendingLoad = null;
  let requested = false;
  let busy = false;
  let feedback = '';
  let error = '';
  const refresh = () => getRouter().refresh();
  async function load({ fresh = false } = {}) {
    if (busy && !fresh) return;
    if (pendingLoad && !fresh) return pendingLoad;
    requested = true;
    loading = true;
    const token = ++loadToken;
    const request = (async () => {
      try {
        const [loaded, products] = await Promise.all([api.post('/app/api/subscription/state'), api.post('/app/api/subscription/catalog')]);
        if (token === loadToken) { state = loaded; catalog = products; error = ''; }
      } catch {
        if (token === loadToken) error = state ? 'Нет связи. Показан последний загруженный статус.' : 'Не удалось загрузить доступ.';
      } finally {
        if (token === loadToken) { loading = false; pendingLoad = null; refresh(); }
      }
    })();
    pendingLoad = request;
    return request;
  }
  const onVisibility = () => {
    if (!document.hidden && getRouter().state.detail === 'subscription') void load();
  };
  document.addEventListener('visibilitychange', onVisibility);
  async function change(path, values) {
    if (busy) return;
    ++loadToken;
    pendingLoad = null;
    loading = false;
    busy = true;
    feedback = '';
    refresh();
    try {
      const result = await api.post(`/app/api/subscription/${path}`, values);
      if (path === 'test-trial' && state) {
        state.viewer = {
          ...state.viewer, active: true, source: 'test',
          expires_at: result.expires_at, test_trial_used: true,
          test_trial_active: true, test_trial_available: false,
          test_trial_expires_at: result.expires_at,
        };
      }
      if (path === 'test-trial') feedback = result.started_now
        ? 'Ознакомление на 7 дней включено. Деньги не списываются и продления нет.'
        : 'Ознакомление уже включено. Срок не изменился.';
      else if (path === 'test-checkout') feedback = 'Тестовый заказ создан. Доступ ещё не активен.';
      else if (result.status === 'paid') feedback = 'Тестовый доступ активен.';
      else if (result.status === 'refunded') feedback = 'Тестовый доступ отозван.';
      else if (result.status === 'cancelled') feedback = 'Заказ отменён.';
      else feedback = 'Статус изменился. Проверьте его ниже.';
      await load({ fresh: true });
      if (path === 'test-trial' || path === 'test-confirm' || path === 'test-refund') onAccessChanged();
    } catch (cause) {
      feedback = cause instanceof ApiError && cause.code === 'trial_used'
        ? 'Ознакомление уже использовано. Бесплатные возможности продолжают работать.'
        : cause instanceof ApiError && cause.code === 'plus_active'
          ? 'Viewer Plus уже активен. Ознакомление остаётся доступным позже.'
          : 'Действие не выполнено. Проверьте статус и попробуйте снова.';
      await load({ fresh: true });
    } finally { busy = false; refresh(); }
  }
  function renderProduct(target, key) {
    const item = products[key];
    const productState = state[key === 'viewer_plus' ? 'viewer' : 'streamer'];
    const box = element('section', 'panel subscription-product');
    box.append(element('h2', '', item.title));
    const offer = catalog?.products.find(product => product.product_id === key);
    if (offer) box.append(element('p','subscription-price',`${offer.price_label} / месяц`));
    box.append(element('p', 'muted', item.detail));
    let status = productState.active
      ? `${sourceName(productState.source)} · до ${dateText(productState.expires_at)}`
      : 'Сейчас без Plus';
    if (key === 'streamer_plus' && !productState.linked) status += ' · сначала подключите Twitch';
    box.append(element('p', 'subscription-status', status));
    if (offer && !productState.active) {
      box.append(action(`Подключить ${item.title} — ${offer.price_label}`,event=>dialog('Подключение Plus',content=>{
        content.append(element('p','','Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.'));
      },{origin:event.currentTarget})));
    }
    if (key === 'viewer_plus' && productState.test_trial_available) {
      box.append(element('p', 'muted', 'Один раз на 7 дней в тестовом окружении. Без оплаты и автопродления.'));
      const trial = action('Попробовать 7 дней', () => void change('test-trial', {}));
      trial.disabled = busy || loading;
      box.append(trial);
    } else if (key === 'viewer_plus' && productState.test_trial_used) {
      box.append(element('p', 'muted', productState.test_trial_active
        ? `Тестовое ознакомление до ${dateText(productState.test_trial_expires_at)}. Продления нет.`
        : 'Тестовое ознакомление использовано. Бесплатные возможности доступны.'));
    }
    if (state.test_checkout_available && (key === 'viewer_plus' || productState.linked)) {
      const controls = element('div', 'actions');
      const start = action('Создать тестовый заказ', () => void change('test-checkout', { product: key }), true);
      start.disabled = busy || loading;
      controls.append(start);
      box.append(controls);
    }
    target.append(box);
  }
  function renderOrder(target, order) {
    const row = element('div', 'subscription-order');
    const copy = element('div', 'row-copy');
    copy.append(element('strong', '', products[order.product]?.title || 'Продукт'));
    copy.append(element('small', '', `${statuses[order.status] || 'Статус неизвестен'} · ${dateText(order.created_at)}`));
    row.append(copy);
    if (state.test_checkout_available && order.status === 'pending') {
      const controls = element('div', 'actions');
      const confirm = action('Подтвердить тест', () => void change('test-confirm', { order_id: order.order_id }));
      const cancel = action('Отменить', () => void change('test-cancel', { order_id: order.order_id }), true);
      confirm.disabled = busy || loading; cancel.disabled = busy || loading;
      controls.append(confirm, cancel); row.append(controls);
    } else if (state.test_checkout_available && order.status === 'paid') {
      const refund = action('Отозвать тестовый доступ', () => void change('test-refund', { order_id: order.order_id }), true);
      refund.disabled = busy || loading;
      row.append(refund);
    }
    target.append(row);
  }
  return {
    render(target, route) {
      target.replaceChildren();
      if (!requested) void load();
      target.append(element('p', 'eyebrow', route.mode === 'viewer' ? 'Зритель' : 'Стример'));
      target.append(element('h1', '', state?.viewer.active || state?.streamer.active ? 'Моя подписка' : 'Возможности Plus'));
      target.append(element('p', 'lead', 'Возможности и история тестовых заказов.'));
      if (!state) {
        target.append(panel('Проверяем доступ', error || 'Загружаем текущий статус.'));
        if (error) target.append(action('Повторить', () => void load()));
        return;
      }
      if (error) target.append(element('p', 'notice error', error));
      if (feedback) {
        const note = element('p', 'notice', feedback);
        note.setAttribute('role', 'status');
        target.append(note);
      }
      if (state.test_checkout_available) target.append(panel('Тестовый доступ', 'Деньги не списываются. Подтверждение действует только в закреплённом тестовом окружении.'));
      else target.append(panel('Подписка', 'Покупка в приложении пока недоступна. Бесплатные возможности работают без неё.'));
      const first = route.mode === 'viewer' ? 'viewer_plus' : 'streamer_plus';
      renderProduct(target, first);
      renderProduct(target, first === 'viewer_plus' ? 'streamer_plus' : 'viewer_plus');
      target.append(element('h2', 'section-head', 'История'));
      if (!state.history.length) target.append(panel('Заказов пока нет', 'Здесь будут показаны ваши заказы и их статус.'));
      else {
        const list = element('div', 'subscription-history');
        state.history.forEach((order) => renderOrder(list, order));
        target.append(list);
      }
      const update = action('Обновить статус', () => void load(), true);
      update.disabled = loading || busy;
      const actions = element('div', 'actions');
      actions.append(update);
      target.append(actions);
    },
    refresh: load,
    dispose() { ++loadToken; document.removeEventListener('visibilitychange', onVisibility); },
  };
}
